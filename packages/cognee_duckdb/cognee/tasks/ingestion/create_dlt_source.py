import csv
import os
import re

DB_CONNECTION_PATTERNS = [
    "postgresql://",
    "postgres://",
    "mysql://",
    "mysql+pymysql://",
    "sqlite:///",
    "mssql://",
    "oracle://",
]


def is_connection_string(data: str) -> bool:
    return any(data.startswith(p) for p in DB_CONNECTION_PATTERNS)


def is_csv_path(data: str) -> bool:
    return data.lower().endswith(".csv") and not data.startswith(("http://", "https://"))


def is_csv_upload(item) -> bool:
    """A file-like CSV input: an API upload (``.file`` + ``.filename``) or a
    binary handle (``.read`` + ``.name``) whose filename ends in .csv."""
    if isinstance(item, (str, bytes)):
        return False
    filename = getattr(item, "filename", None) or getattr(item, "name", None)
    if not isinstance(filename, str) or not filename.lower().endswith(".csv"):
        return False
    return hasattr(item, "file") or hasattr(item, "read")


def csv_source_name(filename: str) -> str:
    """Deterministic dlt resource name for a CSV file, derived from its
    basename stem. The manifest identity is seeded from this name, so it must
    be stable across runs and distinct across files."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_").lower()
    return safe or "csv_source"


def _attached_sqlite(path: str):
    """Return an in-memory DuckDB connection with the SQLite file attached read-only as ``external``.

    The file is read through DuckDB's own sqlite extension, so no SQLite package is
    needed. A path that is not an existing file is refused, because attaching it would
    otherwise be an error that does not name the file. The caller closes the connection.
    """
    import duckdb

    if not os.path.isfile(path):
        raise ValueError(f"The sqlite file {path} does not exist")
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("LOAD sqlite")
        quoted = path.replace("'", "''")
        connection.execute(f"ATTACH '{quoted}' AS external (TYPE sqlite, READ_ONLY)")
        connection.execute("USE external")
    except duckdb.Error:
        connection.close()
        raise
    return connection


def _sqlite_rows(path: str, table: str, where_clause: str):
    """Yield the rows of one SQLite table as dicts, one at a time, filtered by a WHERE text."""
    connection = _attached_sqlite(path)
    try:
        quoted_table = '"' + table.replace('"', '""') + '"'
        cursor = connection.execute(f"SELECT * FROM {quoted_table} WHERE {where_clause}")
        names = [column[0] for column in cursor.description]
        while (row := cursor.fetchone()) is not None:
            yield dict(zip(names, row))
    finally:
        connection.close()


def create_dlt_source_from_sqlite_file(path: str, query: str | None = None):
    """Build a dlt source from a SQLite file, read through DuckDB's sqlite extension.

    Goes in: the file's path and optionally a ``SELECT ... FROM table WHERE ...`` text
    that picks one table and a filter. Comes out: a dlt source named ``sql_database``
    with one resource per table (primary keys carried over). A query naming a table the
    file lacks, or a schema other than ``main``, raises ValueError. It never writes the file.
    """
    import dlt

    connection = _attached_sqlite(path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_catalog = 'external' "
                "AND table_type = 'BASE TABLE' ORDER BY table_name"
            ).fetchall()
        ]
        keys = {
            row[0]: list(row[1])
            for row in connection.execute(
                "SELECT table_name, constraint_column_names FROM duckdb_constraints() "
                "WHERE database_name = 'external' AND constraint_type = 'PRIMARY KEY'"
            ).fetchall()
        }
    finally:
        connection.close()
    wanted = {name: "1=1" for name in tables}
    if query:
        table_name, where_clause = _parse_sql_query(query)
        schema_name, bare_table_name = _split_schema_and_table(table_name)
        if schema_name not in (None, "main"):
            raise ValueError(f"A sqlite file has only the schema main, not {schema_name}: {query}")
        if bare_table_name not in tables:
            raise ValueError(f"The sqlite file {path} has no table {bare_table_name}: {query}")
        wanted = {bare_table_name: where_clause}

    def resource_of(table: str, where_clause: str):
        """Return the dlt resource that reads one table, so every run reads the file afresh."""

        @dlt.resource(name=table, primary_key=keys.get(table) or None)
        def rows():
            """Yield the table's rows."""
            yield from _sqlite_rows(path, table, where_clause)

        return rows

    @dlt.source(name="sql_database")
    def sqlite_source():
        """Return one dlt resource per wanted table."""
        return [resource_of(table, where_clause) for table, where_clause in wanted.items()]

    return sqlite_source()


def create_dlt_source_from_connection_string(
    connection_string: str,
    query: str | None = None,
):
    """Auto-generate a dlt source from a database connection string with optional SQL query filtering."""
    # sqlite:/// = relative, sqlite://// = absolute; a SQLite file is read through DuckDB.
    if connection_string.startswith("sqlite:///"):
        return create_dlt_source_from_sqlite_file(
            os.path.abspath(connection_string[len("sqlite:///") :]), query
        )

    import sqlalchemy
    from dlt.sources.sql_database import sql_database

    if query:
        table_name, where_clause = _parse_sql_query(query)
        schema_name, bare_table_name = _split_schema_and_table(table_name)

        def query_adapter_callback(q, table):
            # sqlalchemy Table.name is never schema-qualified
            if table.name == bare_table_name:
                return q.where(sqlalchemy.text(where_clause))
            return q

        source_kwargs = {
            "credentials": connection_string,
            "table_names": [bare_table_name],
            "query_adapter_callback": query_adapter_callback,
        }
        if schema_name:
            source_kwargs["schema"] = schema_name

        source = sql_database(**source_kwargs)
    else:
        source = sql_database(credentials=connection_string)

    return source


CSV_DELIMITER_CANDIDATES = (",", ";", "\t", "|")


def detect_csv_delimiter(csv_path: str, sample_lines: int = 20) -> str:
    """Pick the delimiter that splits the first lines into a consistent number
    of columns. Candidates are tried in order, so a comma wins ties.

    ``csv.Sniffer`` is not used: it counts characters, so a comma-heavy text
    column (JSON lists, prose) makes it pick "," for a semicolon-delimited
    file. Parsing with ``csv.reader`` respects quoting, and a wrong delimiter
    shows up as a ragged column count. Falls back to "," when nothing fits.
    """
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as handle:
        lines = [line for _, line in zip(range(sample_lines), handle) if line.strip()]
    if not lines:
        return ","

    for delimiter in CSV_DELIMITER_CANDIDATES:
        try:
            counts = {len(row) for row in csv.reader(lines, delimiter=delimiter)}
        except csv.Error:
            continue
        if len(counts) == 1 and counts.pop() > 1:
            return delimiter
    return ","


def create_dlt_source_from_csv(csv_path: str, source_name: str | None = None):
    """Auto-generate a dlt resource from a CSV file path.

    ``source_name`` overrides the filename-derived resource name — callers
    reading from a localized copy (temp download, stored upload) pass the
    name derived from the ORIGINAL file so the manifest identity is stable
    across runs regardless of where the bytes were staged.

    The delimiter is detected from the file (see :func:`detect_csv_delimiter`)
    and forwarded to pandas through dlt's ``read_csv`` — semicolon, tab, and
    pipe files load as tables instead of failing on a ragged comma split.
    """
    from dlt.sources.filesystem import filesystem, read_csv

    parent_dir = os.path.dirname(os.path.abspath(csv_path))
    filename = os.path.basename(csv_path)

    source = filesystem(
        bucket_url=f"file://{parent_dir}",
        file_glob=filename,
    ) | read_csv(sep=detect_csv_delimiter(csv_path))
    # A piped read_csv resource is otherwise always named "_read_csv", and the
    # manifest identity is seeded from (dataset, source name) — every CSV in a
    # dataset would collapse into one identity. Name per file instead.
    return source.with_name(source_name or csv_source_name(filename))


def _split_schema_and_table(qualified_name: str) -> tuple:
    """Split a possibly schema-qualified table ref into (schema, table).

    ``public.users`` -> (``public``, ``users``). A bare name has no schema.
    """
    if "." not in qualified_name:
        return None, qualified_name
    schema, table = qualified_name.rsplit(".", 1)
    return schema, table


def _parse_sql_query(query: str) -> tuple:
    """Extract table name and WHERE clause from a SELECT query.
    Returns (table_name, where_clause) or raises ValueError.

    Table names may be schema-qualified (``public.users``). ``\\w+`` alone
    stops at the first dot, which used to drop both the schema and the WHERE
    clause.

    A table alias (``users u`` / ``users AS u``) is allowed as long as the
    WHERE clause qualifies columns with the table name, not the alias: dlt's
    select is built on the bare table, so an alias reference (``u.age``)
    cannot be replayed and raises instead of silently ingesting the whole
    table. JOIN queries raise for the same reason — their WHERE spans tables
    the single-table source cannot express.
    """
    # Words that can follow a table name without being an alias.
    reserved_after_table = (
        "WHERE",
        "JOIN",
        "INNER",
        "LEFT",
        "RIGHT",
        "FULL",
        "CROSS",
        "ORDER",
        "GROUP",
        "LIMIT",
        "UNION",
        "OFFSET",
        "HAVING",
    )
    alias_pattern = "|".join(reserved_after_table)
    match = re.match(
        r"SELECT\s+.+?\s+FROM\s+"
        r"(?P<table>\w+(?:\.\w+)*)"
        rf"(?:\s+(?:AS\s+)?(?!(?:{alias_pattern})\b)(?P<alias>\w+))?"
        rf"(?P<join>\s+(?:(?:INNER|LEFT|RIGHT|FULL|CROSS)\s+)?JOIN\b)?"
        r"(?:\s+WHERE\s+(?P<where>.+))?",
        query.strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise ValueError(f"Cannot parse SQL query: {query}")
    if match.group("join"):
        raise ValueError(
            "JOIN queries are not supported for filtered ingestion (the WHERE clause spans "
            f"tables this source cannot express): {query}. Ingest the table without a filter, "
            "or create a view and ingest that."
        )
    table_name = match.group("table")
    where_clause = match.group("where") or "1=1"
    alias = match.group("alias")
    if alias and re.search(rf"\b{re.escape(alias)}\.\w+", where_clause, re.IGNORECASE):
        raise ValueError(
            f"WHERE clause references the table alias '{alias}', but the filter is replayed against "
            f"the bare table '{table_name}'. Qualify columns with the table name instead: {query}"
        )
    return table_name, where_clause
