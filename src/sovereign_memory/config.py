"""Deployment configuration of the Sovereign Memory Owner runtime, read and checked early.

The runtime takes every deployment choice from one TOML file: where the socket
and database live, request and shutdown bounds, who shares the memory pool, and
which tier engines to run with the models they use. Nothing here has a default
for a model, a bound or a tuning number: a missing or unknown key, a wrong type
or a tier that cannot be built is a ConfigurationError that names the key, and
the service never starts on a half-valid file. It reads only the file and
environment variable names the file points to; it never opens the database,
calls a model or touches the network.
"""

import math
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

if TYPE_CHECKING:
    from mem0.configs.base import MemoryConfig

TIER_KEYS = ("episodic", "temporal", "relational", "personalization")


class ConfigurationError(ValueError):
    """The configuration file is missing, unreadable or invalid; the message names the key."""


@dataclass(frozen=True)
class EmbeddingSettings:
    """The embedding model of the relational tier, exactly as the deployment named it."""

    provider: str
    model: str
    dimensions: int
    endpoint: str | None
    api_key: str | None


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
    relational_embedding: EmbeddingSettings | None
    personalization: "MemoryConfig | None"


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


def _embedding(table: Any, where: str) -> EmbeddingSettings:
    """Read the embedding model table; api_key_env names an environment variable that must be set."""
    table = _take(table, where, ("provider", "model", "dimensions"), ("endpoint", "api_key_env"))
    api_key = None
    if "api_key_env" in table:
        name = _text(table, where, "api_key_env")
        api_key = os.environ.get(name)
        if not api_key:
            raise ConfigurationError(f"{where}.api_key_env names {name}, which is not set")
    endpoint = _text(table, where, "endpoint") if "endpoint" in table else None
    return EmbeddingSettings(
        _text(table, where, "provider"),
        _text(table, where, "model"),
        int(_positive(table, where, "dimensions", True)),
        endpoint,
        api_key,
    )


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
        settings = _take(memory[engine], engine_path, ("provider", "config"))
        _text(settings, engine_path, "provider")
        if not isinstance(settings["config"], dict):
            raise ConfigurationError(f"{engine_path}.config must be a table")
        if "model" not in settings["config"]:
            raise ConfigurationError(f"{engine_path}.config is missing: model")
        _text(settings["config"], f"{engine_path}.config", "model")
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
    return config


def parse(data: dict[str, Any], base: Path) -> RuntimeConfig:
    """Validate parsed TOML and return the RuntimeConfig; relative paths resolve against base."""
    data = _take(data, "file", ("service", "tiers"), ("visibility",))
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
    database_name, embedding = None, None
    if "relational" in tiers:
        relational = _take(tiers["relational"], "tiers.relational", ("database_name", "embedding"))
        database_name = _text(relational, "tiers.relational", "database_name")
        embedding = _embedding(relational["embedding"], "tiers.relational.embedding")
    memory = _personalization(tiers["personalization"]) if "personalization" in tiers else None

    socket_file = (base / _text(service, "service", "socket_path")).resolve()
    database_file = (base / _text(service, "service", "database_path")).resolve()
    if socket_file == database_file or (
        socket_file.exists()
        and database_file.exists()
        and os.path.samefile(socket_file, database_file)
    ):
        raise ConfigurationError("service.socket_path and service.database_path name the same file")

    def path(key: str) -> Path:
        """Resolve one configured path against the configuration file's folder."""
        return (base / _text(service, "service", key)).resolve()

    return RuntimeConfig(
        socket_path=path("socket_path"),
        database_path=path("database_path"),
        max_request_bytes=int(_positive(service, "service", "max_request_bytes", True)),
        drain_timeout_seconds=float(_positive(service, "service", "drain_timeout_seconds", False)),
        maintenance_interval_seconds=float(
            _positive(service, "service", "maintenance_interval_seconds", False)
        ),
        pool_scope_labels=frozenset(labels),
        episodic_organization=organization,
        temporal_enabled="temporal" in tiers,
        relational_database_name=database_name,
        relational_embedding=embedding,
        personalization=memory,
    )


def load(config_path: str | os.PathLike[str]) -> RuntimeConfig:
    """Read and validate the TOML file at config_path.

    Raises ConfigurationError for a missing file, invalid TOML or any invalid key.
    """
    path = Path(config_path).resolve()
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as error:
        raise ConfigurationError(f"Cannot read configuration {path}: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigurationError(f"Configuration {path} is not valid TOML: {error}") from error
    return parse(data, path.parent)
