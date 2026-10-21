"""Settings for the Graphiti MCP server, ported from Graphiti's mcp_server/src/config/schema.py.

Goes in: a YAML file whose values may name environment variables as ``${NAME}`` or
``${NAME:fallback}``, plus optional command-line overrides. Comes out: one validated
``GraphitiConfig``. Never supplies a value of its own for anything operational: the
transport, host, port, concurrency limit, default group and database file must all be
declared in the file or on the command line, and a missing one is refused by name.

The original reads the language-model and embedder provider sections and the Neo4j and
FalkorDB sections here. Those are left out on purpose: models are plugged in by the owner
(requirement R1) and the only database is the project's one DuckDB file.
"""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

_ENV_PATTERN = r"\$\{([^:}]+)(:([^}]*))?\}"
_TRUE_WORDS = ("true", "1", "yes", "on")
_FALSE_WORDS = ("false", "0", "no", "off")
TRANSPORTS = ("http", "stdio", "sse")


def expand_env_vars(value: Any) -> Any:
    """Replace ``${NAME}`` and ``${NAME:fallback}`` inside nested settings values.

    Goes in: any value read from YAML. Comes out: the same shape with variables replaced. A
    value that is exactly one expression becomes a real true or false when the text says so,
    and None when it is empty; it never invents a value for a variable that has no fallback.
    """
    if isinstance(value, str):

        def replacer(match: re.Match[str]) -> str:
            """Look the named variable up, using the fallback text when it is unset."""
            fallback = match.group(3) if match.group(3) is not None else ""
            return os.environ.get(match.group(1), fallback)

        whole = re.fullmatch(_ENV_PATTERN, value)
        if whole:
            result = replacer(whole)
            lowered = result.lower().strip()
            if lowered in _TRUE_WORDS:
                return True
            if lowered in _FALSE_WORDS:
                return False
            if lowered == "":
                return None
            return result
        return re.sub(_ENV_PATTERN, replacer, value)
    if isinstance(value, dict):
        return {key: expand_env_vars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_env_vars(item) for item in value]
    return value


class ServerConfig(BaseModel):
    """How the MCP server is reached and how much work it does at once.

    Goes in: the transport, host, port and episode concurrency limit. Comes out: those
    values, validated. Never defaults any of them.
    """

    model_config = ConfigDict(extra="forbid")

    transport: str = Field(description="Transport type: http, stdio or sse (deprecated)")
    host: str = Field(description="Server host")
    port: int = Field(gt=0, description="Server port")
    semaphore_limit: int = Field(
        gt=0, description="How many episodes may be processed at the same time"
    )


class DatabaseConfig(BaseModel):
    """Where the one DuckDB file lives.

    Goes in: the path of the file. Comes out: that path. Never defaults it and never creates
    a second database.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Path of the DuckDB file that holds the Graphiti graph")


class EntityTypeConfig(BaseModel):
    """One entity type the server asks the extraction model to look for.

    If ``name`` matches a model in the type registry, the rich model is used; otherwise a
    documentation-only model is built from the description.
    """

    name: str
    description: str


class EdgeTypeConfig(BaseModel):
    """One edge (fact) type, treated the same way as an entity type."""

    name: str
    description: str


class EdgeTypeMapEntry(BaseModel):
    """The edge type names allowed between one ordered pair of entity types.

    ``Entity`` stands for any entity type, as in graphiti-core's ``edge_type_map``.
    """

    source: str = Field(default="Entity", description="Source entity type name")
    target: str = Field(default="Entity", description="Target entity type name")
    edge_types: list[str] = Field(
        default_factory=list, description="Edge type names allowed for this pair"
    )


class GraphitiAppConfig(BaseModel):
    """Graphiti-specific settings: the default group, the user and the custom types.

    Goes in: the default group id and user id, plus optional type lists. Comes out: those
    values. Never defaults the group id or the user id.
    """

    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(description="Group ID used when a tool call names none")
    user_id: str = Field(description="User ID for tracking operations")
    entity_types: list[EntityTypeConfig] = Field(default_factory=list)
    edge_types: list[EdgeTypeConfig] = Field(default_factory=list)
    edge_type_map: list[EdgeTypeMapEntry] = Field(default_factory=list)


class GraphitiConfig(BaseModel):
    """The whole server configuration.

    Goes in: the server, database and Graphiti sections. Comes out: one validated object. It
    never reads a model provider section: models are supplied by the owner, not configured here.
    """

    model_config = ConfigDict(extra="forbid")

    server: ServerConfig
    database: DatabaseConfig
    graphiti: GraphitiAppConfig

    @classmethod
    def from_yaml(cls, path: Path) -> "GraphitiConfig":
        """Read, expand and validate one YAML settings file.

        Goes in: the file path. Comes out: the validated settings. It refuses a missing file
        instead of falling back to defaults.
        """
        if not path.is_file():
            raise FileNotFoundError(f"Graphiti MCP settings file not found: {path}")
        with path.open() as handle:
            raw = yaml.safe_load(handle) or {}
        config = cls.model_validate(expand_env_vars(raw))
        if config.server.transport not in TRANSPORTS:
            raise ValueError(
                f"server.transport must be one of {', '.join(TRANSPORTS)}, "
                f"not {config.server.transport!r}"
            )
        return config

    def apply_cli_overrides(self, args: Any) -> None:
        """Let command-line values replace the file's values.

        Goes in: the parsed arguments. Comes out: nothing; the settings change in place. Only
        values the person actually typed replace anything.
        """
        if getattr(args, "transport", None):
            self.server.transport = args.transport
        if getattr(args, "host", None):
            self.server.host = args.host
        if getattr(args, "port", None) is not None:
            self.server.port = args.port
        if getattr(args, "semaphore_limit", None) is not None:
            self.server.semaphore_limit = args.semaphore_limit
        if getattr(args, "database_path", None):
            self.database.path = args.database_path
        if getattr(args, "group_id", None):
            self.graphiti.group_id = args.group_id
        if getattr(args, "user_id", None):
            self.graphiti.user_id = args.user_id
