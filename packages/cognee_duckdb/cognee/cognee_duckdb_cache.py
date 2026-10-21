"""Use Cognee's maintained SQL cache operations with the native DuckDB dialect."""

from sqlalchemy.engine import make_url

from cognee.infrastructure.databases.cache.sql.SqlCacheAdapter import SqlCacheAdapter


class DuckDBCacheAdapter(SqlCacheAdapter):
    """Keep QA, trace, context, TTL and KV behavior on an actual DuckDB engine."""

    def __init__(self, connection_string: str, **kwargs):
        """Forward declared cache settings and reject URLs for other database engines."""
        if make_url(connection_string).get_backend_name() != "duckdb":
            raise ValueError("DuckDBCacheAdapter requires a DuckDB connection URL")
        super().__init__(connection_string, **kwargs)
