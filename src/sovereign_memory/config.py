"""Deployment settings of the Sovereign Memory Owner runtime, kept in the memory DuckDB file.

Every deployment choice lives in the table memory_runtime_settings of the memory
system's own DuckDB file: where the socket is, request and shutdown bounds, who
shares the memory pool, which tier engines to run, the models to register (as
rows of data), the search settings and the supplier. Only the path of that file
is given from outside. Nothing here has a default for a model, a bound or a
tuning number: a missing or unknown setting, a wrong type or a tier that cannot
be built is a ConfigurationError that names the setting, and the service never
starts on half-valid settings. Parsed values are checked here, and the table is changed
only by the set-setting command (settings_store.py); this module never edits the table,
calls a model or touches the network.
"""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
from pydantic import ValidationError

if TYPE_CHECKING:
    from mem0.configs.base import MemoryConfig

TIER_KEYS = ("episodic", "temporal", "relational", "personalization")


class ConfigurationError(ValueError):
    """The configuration file is missing, unreadable or invalid; the message names the key."""


@dataclass(frozen=True)
class RuntimeConfig:
    """Everything the runtime needs, validated; the tier fields are None when not configured."""

    socket_path: Path
    database_path: Path
    max_request_bytes: int
    drain_timeout_seconds: float
    maintenance_interval_seconds: float
    pool_scope_labels: frozenset[str]
    episodic_organization: str | None
    temporal_enabled: bool
    relational_database_name: str | None
    relational_embedding_model: tuple[str, str] | None
    ranking: dict[str, Any] | None
    models: tuple[dict[str, Any], ...]
    personalization: "MemoryConfig | None"
    supplier: dict[str, Any] | None


def _take(
    table: Any, where: str, required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> dict:
    """Return table as a dict after checking it has every required key and no unknown one."""
    if not isinstance(table, dict):
        raise ConfigurationError(f"[{where}] must be a table")
    missing = [key for key in required if key not in table]
    if missing:
        raise ConfigurationError(f"[{where}] is missing: {', '.join(missing)}")
    unknown = sorted(set(table) - set(required) - set(optional))
    if unknown:
        raise ConfigurationError(f"[{where}] has unknown keys: {', '.join(unknown)}")
    return table


def _text(table: dict, where: str, key: str) -> str:
    """Return a non-empty string value or raise ConfigurationError naming the key."""
    value = table[key]
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{where}.{key} must be a non-empty string")
    return value


def _positive(table: dict, where: str, key: str, integer: bool) -> float:
    """Return a finite positive number (an integer when asked) or raise ConfigurationError."""
    value = table[key]
    kinds = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, kinds) or value <= 0:
        raise ConfigurationError(
            f"{where}.{key} must be a positive {'integer' if integer else 'number'}"
        )
    try:
        finite = math.isfinite(value)
    except OverflowError as error:
        raise ConfigurationError(f"{where}.{key} is too large for a runtime bound") from error
    if not finite:
        raise ConfigurationError(f"{where}.{key} must be finite")
    return value


def _embedding_model(table: Any, where: str) -> tuple[str, str]:
    """Read which registered model revision Cognee's own embedder object is bound to.

    The table names a model id and a revision only; the model itself is a row in the
    registry, never code, and the embedder answers from received numbers.
    """
    table = _take(table, where, ("model_id", "revision"))
    return _text(table, where, "model_id"), _text(table, where, "revision")


def _ranking(table: Any) -> dict[str, Any]:
    """Read the search settings table; the four main settings are required and none has a default.

    The three decay settings are optional and go together. Types and ranges are
    checked by the Owner when it is built, which raises a named error; here the
    shape (the four keys, plus only the decay keys) is enforced.
    """
    return dict(
        _take(
            table,
            "ranking",
            ("comparison_score", "held_back_share", "smallest_held_back_set", "merging_constant"),
            ("decay_time_constant_seconds", "decay_lower_bound", "decay_upper_bound"),
        )
    )


def _models(rows: Any) -> tuple[dict[str, Any], ...]:
    """Read the [[models]] rows to register at start: id, revision, width, kind, supplier collection and recipe."""
    if not isinstance(rows, list):
        raise ConfigurationError("[[models]] must be an array of tables")
    result = []
    for index, row in enumerate(rows):
        where = f"models[{index}]"
        row = _take(
            row,
            where,
            ("model_id", "revision", "width", "kind", "supplier_collection", "supplier_recipe"),
        )
        _positive(row, where, "width", True)
        for key in ("model_id", "revision", "kind", "supplier_collection", "supplier_recipe"):
            _text(row, where, key)
        result.append(dict(row))
    return tuple(result)


def _supplier(table: Any) -> dict[str, Any]:
    """Read the [supplier] table: where agent_corpus answers, who memory says it is, the time limit.

    Every key is required and none has a default; the time limit must be a positive number.
    """
    table = dict(_take(table, "supplier", ("base_url", "requester_agent_id", "timeout_seconds")))
    _text(table, "supplier", "base_url")
    _text(table, "supplier", "requester_agent_id")
    _positive(table, "supplier", "timeout_seconds", False)
    return table


def _personalization(table: Any) -> "MemoryConfig":
    """Read the Mem0 table: llm, embedder and vector_store are required, the vector store is DuckDB."""
    where = "tiers.personalization"
    table = _take(table, where, ("memory",))
    memory = _take(
        table["memory"],
        f"{where}.memory",
        ("llm", "embedder", "vector_store", "enable_update_delete_branches"),
        ("custom_fact_extraction_prompt", "custom_update_memory_prompt", "custom_instructions"),
    )
    for engine in ("llm", "embedder"):
        engine_path = f"{where}.memory.{engine}"
        required = (
            ("provider", "config", "revision") if engine == "embedder" else ("provider", "config")
        )
        settings = _take(memory[engine], engine_path, required)
        _text(settings, engine_path, "provider")
        if engine == "embedder":
            _text(settings, engine_path, "revision")
        if not isinstance(settings["config"], dict):
            raise ConfigurationError(f"{engine_path}.config must be a table")
        if "model" not in settings["config"]:
            raise ConfigurationError(f"{engine_path}.config is missing: model")
        _text(settings["config"], f"{engine_path}.config", "model")
    vector_path = f"{where}.memory.vector_store"
    vector = _take(memory["vector_store"], vector_path, ("provider", "config"))
    if _text(vector, vector_path, "provider") != "duckdb":
        raise ConfigurationError(f"{vector_path}.provider must be duckdb")
    vector_config = vector["config"]
    if not isinstance(vector_config, dict):
        raise ConfigurationError(f"{vector_path}.config must be a table")
    if "embedding_model_dims" not in vector_config:
        raise ConfigurationError(f"{vector_path}.config is missing: embedding_model_dims")
    _positive(vector_config, f"{vector_path}.config", "embedding_model_dims", True)
    from mem0.configs.base import MemoryConfig

    try:
        config = MemoryConfig.model_validate(memory)
    except ValidationError as error:
        raise ConfigurationError(f"{where}.memory is invalid: {error}") from error
    if config.vector_store.provider != "duckdb":
        raise ConfigurationError(f"{where}.memory.vector_store.provider must be duckdb")
    if not config.enable_update_delete_branches:
        raise ConfigurationError(
            f"{where}.memory.enable_update_delete_branches must be true: the tier "
            "refuses Mem0's ADD-only pipeline"
        )
    if config.embedder.provider != "received_numbers":
        raise ConfigurationError(
            f"{where}.memory.embedder.provider must be received_numbers: "
            "memory never makes numbers, so no model-calling embedder is accepted"
        )
    options = config.embedder.config
    received = options.get("received_numbers")
    if (
        not isinstance(received, dict)
        or received.get("revision") != config.embedder.revision
        or type(options.get("embedding_dims")) is not int
        or options["embedding_dims"] != config.vector_store.config.embedding_model_dims
    ):
        raise ConfigurationError(
            f"{where}.memory.embedder must match the declared revision and vector-store dimensions"
        )
    return config


def parse(data: dict[str, Any], base: Path) -> RuntimeConfig:
    """Validate parsed TOML and return the RuntimeConfig; relative paths resolve against base."""
    data = _take(
        data, "file", ("service", "tiers"), ("visibility", "ranking", "models", "supplier")
    )
    service = _take(
        data["service"],
        "service",
        (
            "socket_path",
            "database_path",
            "max_request_bytes",
            "drain_timeout_seconds",
            "maintenance_interval_seconds",
        ),
    )
    visibility = _take(data.get("visibility", {}), "visibility", (), ("pool_scope_labels",))
    labels = visibility.get("pool_scope_labels", [])
    if not isinstance(labels, list) or not all(isinstance(x, str) and x for x in labels):
        raise ConfigurationError("visibility.pool_scope_labels must be a list of non-empty strings")
    tiers = _take(data["tiers"], "tiers", (), TIER_KEYS)
    if not tiers:
        raise ConfigurationError("[tiers] configures no tier; configure at least one")

    organization = None
    if "episodic" in tiers:
        episodic = _take(tiers["episodic"], "tiers.episodic", ("organization_id",))
        organization = _text(episodic, "tiers.episodic", "organization_id")
    if "temporal" in tiers:
        _take(tiers["temporal"], "tiers.temporal", ())
    database_name, embedding_model = None, None
    if "relational" in tiers:
        relational = _take(
            tiers["relational"], "tiers.relational", ("database_name",), ("embedding_model",)
        )
        database_name = _text(relational, "tiers.relational", "database_name")
        if "embedding_model" in relational:
            embedding_model = _embedding_model(
                relational["embedding_model"], "tiers.relational.embedding_model"
            )
    memory = _personalization(tiers["personalization"]) if "personalization" in tiers else None
    ranks_by_models = "relational" in tiers or "personalization" in tiers
    if ranks_by_models and "ranking" not in data:
        raise ConfigurationError(
            "[ranking] is required when a tier ranks by models' numbers: declare "
            "comparison_score, held_back_share, smallest_held_back_set and merging_constant"
        )
    ranking = _ranking(data["ranking"]) if "ranking" in data else None
    models = _models(data.get("models", []))
    supplier = _supplier(data["supplier"]) if "supplier" in data else None

    # Keep a socket symlink visible to the service's lstat-based refusal.
    socket_file = (base / _text(service, "service", "socket_path")).absolute()
    database_file = (base / _text(service, "service", "database_path")).resolve()
    if socket_file.resolve() == database_file or (
        socket_file.exists()
        and database_file.exists()
        and os.path.samefile(socket_file, database_file)
    ):
        raise ConfigurationError("service.socket_path and service.database_path name the same file")

    return RuntimeConfig(
        socket_path=socket_file,
        database_path=database_file,
        max_request_bytes=int(_positive(service, "service", "max_request_bytes", True)),
        drain_timeout_seconds=float(_positive(service, "service", "drain_timeout_seconds", False)),
        maintenance_interval_seconds=float(
            _positive(service, "service", "maintenance_interval_seconds", False)
        ),
        pool_scope_labels=frozenset(labels),
        episodic_organization=organization,
        temporal_enabled="temporal" in tiers,
        relational_database_name=database_name,
        relational_embedding_model=embedding_model,
        ranking=ranking,
        models=models,
        personalization=memory,
        supplier=supplier,
    )


SETTINGS_TABLE = "memory_runtime_settings"
HISTORY_TABLE = "memory_runtime_settings_history"
SET_COMMAND = (
    "python -m sovereign_memory set-setting <memory database> <setting>=<value> "
    '--describe <setting>="what it does"'
)


def _flatten(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Return (dotted path, value) for every leaf of a nested table; lists and empty tables are leaves.

    A key that is empty or holds a dot is refused, naming it, because the dotted path
    could not be read back unchanged.
    """
    if not isinstance(value, dict) or not value:
        return [(prefix, value)]
    leaves: list[tuple[str, Any]] = []
    for key, inner in value.items():
        if not key or "." in key:
            raise ConfigurationError(
                f"setting name {key!r} under {prefix or 'the top level'} must be non-empty "
                "and hold no dot"
            )
        leaves.extend(_flatten(inner, f"{prefix}.{key}" if prefix else key))
    return leaves


def _unflatten(rows: list[tuple[str, str]]) -> dict[str, Any]:
    """Rebuild the nested tables from (dotted path, JSON text) rows; a path under a leaf is refused."""
    data: dict[str, Any] = {}
    for path, text in rows:
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise ConfigurationError(
                f"setting {path} in {SETTINGS_TABLE} is not valid JSON: {error}"
            ) from error
        *parents, last = path.split(".")
        node = data
        for key in parents:
            node = node.setdefault(key, {})
            if not isinstance(node, dict):
                raise ConfigurationError(f"setting {path} sits under {key}, which holds a value")
        if last in node:
            raise ConfigurationError(f"setting {path} is stored twice or also holds sub-settings")
        node[last] = value
    return data


def _with_database(data: dict[str, Any], database: Path) -> dict[str, Any]:
    """Return data with service.database_path set to the file the settings live in.

    The database path is the one thing given from outside, so a settings table that
    also declares it is refused rather than silently overridden.
    """
    service = data.get("service")
    if not isinstance(service, dict):
        return data
    if "database_path" in service:
        raise ConfigurationError(
            "service.database_path is not a setting: the memory database is the file the "
            "settings are stored in, given on the command line"
        )
    return {**data, "service": {**service, "database_path": str(database)}}


def load(database_path: str | os.PathLike[str]) -> RuntimeConfig:
    """Read the settings table of the memory DuckDB file at database_path and validate it.

    Relative paths among the settings resolve against the database file's folder.
    Raises ConfigurationError, naming the problem, for a missing file, a file in use
    by another process, a missing or empty settings table, or any missing or invalid
    setting. It only reads, and always closes the file before returning.
    """
    path = Path(database_path).resolve()
    if not path.is_file():
        raise ConfigurationError(
            f"Memory database {path} does not exist; create its settings with: {SET_COMMAND}"
        )
    try:
        connection = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as error:
        raise ConfigurationError(
            f"Cannot open memory database {path} to read its settings "
            f"(is the memory service already running on it?): {error}"
        ) from error
    try:
        present = connection.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [SETTINGS_TABLE]
        ).fetchone()
        if present is None:
            raise ConfigurationError(
                f"Memory database {path} has no {SETTINGS_TABLE} table; set settings with: "
                f"{SET_COMMAND}"
            )
        rows = connection.execute(
            f"SELECT path, value FROM {SETTINGS_TABLE} ORDER BY path"
        ).fetchall()
    except duckdb.Error as error:
        raise ConfigurationError(f"Cannot read {SETTINGS_TABLE} in {path}: {error}") from error
    finally:
        connection.close()
    if not rows:
        raise ConfigurationError(
            f"{SETTINGS_TABLE} in {path} is empty; set settings with: {SET_COMMAND}"
        )
    return parse(_with_database(_unflatten(rows), path), path.parent)
