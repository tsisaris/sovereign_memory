"""Reading and changing the settings stored in the memory DuckDB file.

This module is the only place that writes the settings table and its history table,
and the only place that builds the readable settings page. It takes the memory
database path plus, for a change, the new values, the plain-words descriptions and
the name of who is changing them; it gives back the lines to print. A change is
checked with the same validator the service uses (config.parse) on the complete
settings that would result, and is written, with its history rows, in one
transaction. It never reads a text file as a source of settings, never starts the
service, and never leaves a half-written change behind.
"""

import getpass
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from .config import (
    HISTORY_TABLE,
    SETTINGS_TABLE,
    ConfigurationError,
    _flatten,
    _unflatten,
    _with_database,
    parse,
)

SETTINGS_COLUMNS = ("path", "value", "description", "changed_at", "changed_by")
HISTORY_COLUMNS = ("path", "what", "old_text", "new_text", "changed_at", "changed_by")


def parse_assignment(text: str) -> tuple[str, Any]:
    """Split "name=value" into the setting name and its value.

    The value is read as JSON (numbers, true, false, lists, tables, and strings in
    double quotes); text that is not JSON is taken as a plain string. A missing "=",
    an empty name or a name with an empty part is refused, naming the text. It
    never touches the database.
    """
    name, separator, raw = text.partition("=")
    if not separator or not name or any(not part for part in name.split(".")):
        raise ConfigurationError(
            f"{text!r} is not name=value, where the name is parts joined by dots, such as "
            "service.max_request_bytes=1048576"
        )
    try:
        return name, json.loads(raw)
    except json.JSONDecodeError:
        return name, raw


def parse_description(text: str) -> tuple[str, str]:
    """Split "name=words" into a setting name and its plain-words description; both must be non-empty."""
    name, separator, words = text.partition("=")
    if not separator or not name or not words.strip():
        raise ConfigurationError(
            f'{text!r} is not name=words, such as service.socket_path="where the service listens"'
        )
    return name, words.strip()


def _open_for_write(database: Path) -> duckdb.DuckDBPyConnection:
    """Open the memory database for writing or raise ConfigurationError saying why not."""
    try:
        return duckdb.connect(str(database))
    except duckdb.Error as error:
        raise ConfigurationError(
            f"Cannot open memory database {database} to change settings (the memory service "
            f"may be running on it; stop it first): {error}"
        ) from error


def _create_tables(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the settings table and the history table when they are absent."""
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {SETTINGS_TABLE} (path VARCHAR PRIMARY KEY, "
        "value VARCHAR NOT NULL, description VARCHAR NOT NULL, changed_at VARCHAR NOT NULL, "
        "changed_by VARCHAR NOT NULL)"
    )
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (path VARCHAR NOT NULL, what VARCHAR NOT NULL, "
        "old_text VARCHAR, new_text VARCHAR, changed_at VARCHAR NOT NULL, changed_by VARCHAR NOT NULL)"
    )


def _check_columns(
    connection: duckdb.DuckDBPyConnection, table: str, wanted: tuple[str, ...], database: Path
) -> None:
    """Raise ConfigurationError when table does not hold exactly the wanted columns, in order."""
    found = tuple(
        row[0]
        for row in connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ? "
            "ORDER BY ordinal_position",
            [table],
        ).fetchall()
    )
    if found != wanted:
        raise ConfigurationError(
            f"{table} in {database} has the columns {', '.join(found)}, not "
            f"{', '.join(wanted)}; it was made by an older version and holds settings that "
            "must be re-entered with set-setting on a fresh database"
        )


def _under(path: str, prefix: str) -> bool:
    """Return whether path is the prefix setting itself or sits below it."""
    return path == prefix or path.startswith(prefix + ".")


def _describe(path: str, words: dict[str, str]) -> str | None:
    """Return the description given for path or its nearest ancestor, or None when none was given."""
    best = None
    for prefix in words:
        if _under(path, prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return None if best is None else words[best]


def _text_of(path: str, value: Any, source: str) -> str:
    """Return value as the JSON text stored for path, or raise ConfigurationError naming path."""
    try:
        return json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ConfigurationError(
            f"setting {path} in {source} cannot be stored as JSON: {error}"
        ) from error


def change_settings(
    database_path: str | Path,
    assignments: dict[str, Any],
    descriptions: dict[str, str],
    unsets: list[str],
    changed_by: str | None,
) -> list[str]:
    """Change one or several settings in the memory database and return one line per change.

    Takes the database path, the new values by dotted name, the plain-words
    descriptions by name (a name given for a parent covers its new children), the
    names to remove, and who is changing them (the login name when None). The
    complete settings that would result are checked with the service's own validator
    first; then the values, descriptions and one history row per change are written
    in one transaction. Raises ConfigurationError, naming the problem, for a wrong
    value, a missing description for a new setting, an unknown setting to remove or
    describe, or a database in use, and then changes nothing. It never reads a text
    file and never starts the service.
    """
    database = Path(database_path).resolve()
    who = changed_by or getpass.getuser()
    if not assignments and not unsets and not descriptions:
        raise ConfigurationError(
            "nothing to change: give name=value, --unset name or --describe name=words"
        )
    connection = _open_for_write(database)
    try:
        connection.execute("BEGIN")
        _create_tables(connection)
        _check_columns(connection, SETTINGS_TABLE, SETTINGS_COLUMNS, database)
        _check_columns(connection, HISTORY_TABLE, HISTORY_COLUMNS, database)
        current = {
            path: (value, description)
            for path, value, description in connection.execute(
                f"SELECT path, value, description FROM {SETTINGS_TABLE}"
            ).fetchall()
        }
        values = {path: pair[0] for path, pair in current.items()}
        for name in unsets:
            gone = [path for path in values if _under(path, name)]
            if not gone:
                raise ConfigurationError(f"cannot remove {name}: no such setting")
            for path in gone:
                del values[path]
        for name, value in assignments.items():
            for path in [p for p in values if _under(p, name)]:
                del values[path]
            for path, leaf in _flatten(value, name):
                values[path] = _text_of(path, leaf, "the command line")
        for name in descriptions:
            if not any(_under(path, name) for path in values):
                raise ConfigurationError(f"cannot describe {name}: no such setting")
        final_words: dict[str, str] = {}
        for path in values:
            if path in descriptions:
                final_words[path] = descriptions[path]
            elif path in current:
                final_words[path] = current[path][1]
            else:
                given = _describe(path, descriptions)
                if given is None:
                    raise ConfigurationError(
                        f'setting {path} has no description: give --describe {path}="what it does, in plain words"'
                    )
                final_words[path] = given
        parse(
            _with_database(_unflatten([(p, v) for p, v in values.items()]), database),
            database.parent,
        )
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lines: list[str] = []
        for path in sorted(set(current) | set(values)):
            old = current[path][0] if path in current else None
            new = values.get(path)
            old_words = current[path][1] if path in current else None
            new_words = final_words.get(path)
            if new is None:
                connection.execute(f"DELETE FROM {SETTINGS_TABLE} WHERE path = ?", [path])
                lines.append(f"removed {path} (was {old})")
            elif old != new or old_words != new_words:
                connection.execute(
                    f"INSERT OR REPLACE INTO {SETTINGS_TABLE} VALUES (?, ?, ?, ?, ?)",
                    [path, new, new_words, now, who],
                )
                if old != new:
                    lines.append(
                        f"changed {path}: {old} -> {new}"
                        if old is not None
                        else f"added {path} = {new}"
                    )
                if old_words != new_words:
                    connection.execute(
                        f"INSERT INTO {HISTORY_TABLE} VALUES (?, 'description', ?, ?, ?, ?)",
                        [path, old_words, new_words, now, who],
                    )
                    if old == new:
                        lines.append(f"described {path}")
            else:
                continue
            if old != new:
                connection.execute(
                    f"INSERT INTO {HISTORY_TABLE} VALUES (?, 'value', ?, ?, ?, ?)",
                    [path, old, new, now, who],
                )
        connection.execute("COMMIT")
    except duckdb.Error as error:
        raise ConfigurationError(f"Cannot store settings in {database}: {error}") from error
    finally:
        # An unfinished transaction is abandoned when the file is closed, so a refusal keeps no change.
        connection.close()
    return lines or ["nothing changed: every value and description was already as given"]


def show_page(database_path: str | Path, with_history: bool) -> str:
    """Return the readable settings page for the memory database, built from the database each time.

    Takes the database path and whether to append the change history. The page lists
    each setting's name, value, plain-words description, when it was last changed and
    by whom. Raises ConfigurationError, naming the problem, for a missing file, a
    database in use, or missing or older tables. It only reads and never edits.
    """
    database = Path(database_path).resolve()
    if not database.is_file():
        raise ConfigurationError(f"Memory database {database} does not exist")
    try:
        connection = duckdb.connect(str(database), read_only=True)
    except duckdb.Error as error:
        raise ConfigurationError(
            f"Cannot open memory database {database} to show its settings (is the memory "
            f"service running on it?): {error}"
        ) from error
    try:
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_name IN (?, ?)",
                [SETTINGS_TABLE, HISTORY_TABLE],
            ).fetchall()
        }
        if SETTINGS_TABLE not in existing:
            raise ConfigurationError(f"Memory database {database} has no {SETTINGS_TABLE} table")
        _check_columns(connection, SETTINGS_TABLE, SETTINGS_COLUMNS, database)
        rows = connection.execute(
            f"SELECT path, value, description, changed_at, changed_by FROM {SETTINGS_TABLE} ORDER BY path"
        ).fetchall()
        history = []
        if with_history and HISTORY_TABLE in existing:
            _check_columns(connection, HISTORY_TABLE, HISTORY_COLUMNS, database)
            history = connection.execute(
                f"SELECT path, what, old_text, new_text, changed_at, changed_by FROM {HISTORY_TABLE} "
                "ORDER BY changed_at DESC, path"
            ).fetchall()
    except duckdb.Error as error:
        raise ConfigurationError(f"Cannot read settings in {database}: {error}") from error
    finally:
        connection.close()
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"Settings of the memory database {database}",
        f"Made from the database at {generated}",
        "",
    ]
    for path, value, description, changed_at, changed_by in rows:
        lines.append(f"{path} = {value}")
        lines.append(f"    What it does: {description}")
        lines.append(f"    Last changed: {changed_at} by {changed_by}")
        lines.append("")
    lines.append(f"{len(rows)} settings")
    if with_history:
        lines.extend(["", "Changes, newest first:"])
        for path, what, old, new, changed_at, changed_by in history:
            lines.append(f"{changed_at} {changed_by} {path} {what}: {old} -> {new}")
        if not history:
            lines.append("none recorded")
    return "\n".join(lines)
