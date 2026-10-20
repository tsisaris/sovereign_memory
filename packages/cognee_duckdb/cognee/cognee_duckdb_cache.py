import logging
from cognee.infrastructure.databases.cache.sql.SqlCacheAdapter import SqlCacheAdapter
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

logger = logging.getLogger(__name__)

class DuckDBCacheAdapter(SqlCacheAdapter):
    def __init__(self, connection_string: str):
        super().__init__(connection_string)
        self._is_postgres = False

    async def _ensure_initialized(self) -> None:
        if not getattr(self, "_initialized", False):
            import sqlalchemy
            from sqlalchemy.engine import Engine
            if sqlalchemy.event.contains(Engine, "connect", self._set_sqlite_pragmas_if_exists):
                sqlalchemy.event.remove(Engine, "connect", self._set_sqlite_pragmas_if_exists)
            await super()._ensure_initialized()
    
    def _set_sqlite_pragmas_if_exists(self, dbapi_connection, connection_record):
        pass

    def get_upsert_insert(self):
        return sqlite_insert
