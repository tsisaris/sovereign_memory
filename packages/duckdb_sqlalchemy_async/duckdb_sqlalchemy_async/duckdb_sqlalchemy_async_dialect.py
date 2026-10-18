"""Adapt the buffered asynchronous DuckDB bridge to SQLAlchemy's async engine."""

import json

from duckdb_engine import Dialect as DuckDBDialect
from sqlalchemy import text
from sqlalchemy.connectors.asyncio import AsyncAdapt_dbapi_connection, AsyncAdapt_dbapi_cursor
from sqlalchemy.engine.default import DefaultDialect
from sqlalchemy.engine.reflection import ObjectKind, ObjectScope
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool
from sqlalchemy.util.concurrency import await_only

from . import duckdb_sqlalchemy_async_bridge


class AsyncAdapt_duckdb_cursor(AsyncAdapt_dbapi_cursor):
    """Expose a buffered bridge cursor through SQLAlchemy's synchronous facade."""

    def _aenter_cursor(self, cursor):
        """Resolve the bridge's asynchronous logical-cursor factory."""
        return self.await_(cursor)

    @property
    def connection(self):
        """Expose the owner for inherited PostgreSQL notice processing."""
        return self._adapt_connection


class AsyncAdapt_duckdb_connection(AsyncAdapt_dbapi_connection):
    """Use the normal SQLAlchemy async adapter and the bridge's transaction gate."""

    _cursor_cls = AsyncAdapt_duckdb_cursor

    @property
    def notices(self):
        """Return this connection's notices to the inherited execution context."""
        return self._connection.notices

    def begin(self):
        """Begin on the same handle that all logical cursors execute against."""
        self.await_(self._connection.begin())

    def cursor(self, server_side=False):
        """Return a buffered cursor; reject unimplemented server-side streaming."""
        if server_side:
            raise duckdb_sqlalchemy_async_bridge.NotSupportedError("Streaming is not supported")
        return super().cursor()


class DuckDBAsyncDialect(DuckDBDialect):
    """Retain DuckDB SQL compilation while adapting connections asynchronously."""

    name = "duckdb"
    driver = "aio"
    # Inherit the upstream safety policy until compiler cache behavior is proven.
    supports_statement_cache = False
    is_async = True
    paramstyle = "qmark"

    @classmethod
    def import_dbapi(cls):
        """Return the bridge's DB-API exception and connection interface."""
        return duckdb_sqlalchemy_async_bridge

    @classmethod
    def get_pool_class(cls, url):
        """Keep memory connections independent and queue file-backed connections."""
        if not url.database or url.database == ":memory:":
            return NullPool
        return AsyncAdaptedQueuePool

    def connect(self, *cargs, **cparams):
        """Await the configured async creator inside SQLAlchemy's greenlet."""
        creator = cparams.pop("async_creator_fn", self.import_dbapi().connect)
        return AsyncAdapt_duckdb_connection(
            self.import_dbapi(), await_only(creator(*cargs, **cparams))
        )

    def do_ping(self, dbapi_connection):
        """Use SQLAlchemy's SELECT ping without PostgreSQL autocommit controls."""
        return DefaultDialect.do_ping(self, dbapi_connection)

    def get_indexes(self, connection, table_name, schema=None, **kw):
        """Reflect explicit indexes in the selected catalog and schema from DuckDB."""
        catalog, namespace = self.identifier_preparer._separate(schema)
        coordinates = connection.execute(
            text(
                "SELECT database_oid, schema_oid, table_oid FROM duckdb_tables() "
                "WHERE table_name = :table_name "
                "AND database_name = COALESCE(:catalog, current_database()) "
                "AND schema_name = COALESCE(:namespace, current_schema())"
            ),
            {"table_name": table_name, "catalog": catalog, "namespace": namespace},
        ).one_or_none()
        if coordinates is None:
            raise NoSuchTableError(table_name)
        rows = connection.execute(
            text(
                "SELECT index_name, is_unique, CAST(expressions AS VARCHAR[]) "
                "FROM duckdb_indexes() WHERE database_oid = :catalog_oid "
                "AND schema_oid = :schema_oid AND table_oid = :table_oid "
                "ORDER BY index_name"
            ),
            dict(zip(("catalog_oid", "schema_oid", "table_oid"), coordinates, strict=True)),
        ).all()
        indexes = []
        for name, unique, expressions in rows:
            parsed = json.loads(
                connection.execute(
                    text("SELECT CAST(json_serialize_sql(:statement) AS VARCHAR)"),
                    {"statement": "SELECT " + ", ".join(expressions)},
                ).scalar_one()
            )
            if parsed["error"]:
                raise ValueError(f"Cannot parse DuckDB index expressions for {name}: {parsed}")
            nodes = parsed["statements"][0]["node"]["select_list"]
            columns = [
                node["column_names"][0]
                if node["class"] == "COLUMN_REF" and len(node["column_names"]) == 1
                else None
                for node in nodes
            ]
            index = {"name": name, "unique": unique, "column_names": columns}
            if None in columns:
                index["expressions"] = expressions
            indexes.append(index)
        return indexes

    def get_multi_indexes(
        self,
        connection,
        schema=None,
        filter_names=None,
        scope=ObjectScope.DEFAULT,
        kind=ObjectKind.TABLE,
        **kw,
    ):
        """Reuse SQLAlchemy's bulk reflection over the actual single-table results."""
        return DefaultDialect.get_multi_indexes(
            self,
            connection,
            schema=schema,
            filter_names=filter_names,
            scope=scope,
            kind=kind,
            **kw,
        )

    def get_foreign_keys(
        self, connection, table_name, schema=None, postgresql_ignore_search_path=False, **kw
    ):
        """Return actual DuckDB foreign-key names and ordered column relationships."""
        catalog, namespace = self.identifier_preparer._separate(schema)
        coordinates = connection.execute(
            text(
                "SELECT database_oid, schema_oid, table_oid, schema_name FROM duckdb_tables() "
                "WHERE table_name = :table_name "
                "AND database_name = COALESCE(:catalog, current_database()) "
                "AND schema_name = COALESCE(:namespace, current_schema())"
            ),
            {"table_name": table_name, "catalog": catalog, "namespace": namespace},
        ).one_or_none()
        if coordinates is None:
            raise NoSuchTableError(table_name)
        rows = connection.execute(
            text(
                "SELECT constraint_name, constraint_column_names, referenced_table, "
                "referenced_column_names FROM duckdb_constraints() "
                "WHERE database_oid = :catalog_oid AND schema_oid = :schema_oid "
                "AND table_oid = :table_oid AND constraint_type = 'FOREIGN KEY' "
                "ORDER BY constraint_index"
            ),
            dict(zip(("catalog_oid", "schema_oid", "table_oid"), coordinates[:3], strict=True)),
        ).all()
        # DuckDB permits references only within the owning schema and catalog.
        return [
            {
                "name": name,
                "constrained_columns": columns,
                "referred_schema": schema
                or (coordinates[3] if postgresql_ignore_search_path else None),
                "referred_table": target,
                "referred_columns": target_columns,
                "options": {},
            }
            for name, columns, target, target_columns in rows
        ]

    def get_multi_foreign_keys(
        self,
        connection,
        schema=None,
        filter_names=None,
        scope=ObjectScope.DEFAULT,
        kind=ObjectKind.TABLE,
        postgresql_ignore_search_path=False,
        **kw,
    ):
        """Use SQLAlchemy's bulk reflection with native single-table foreign keys."""
        return DefaultDialect.get_multi_foreign_keys(
            self,
            connection,
            schema=schema,
            filter_names=filter_names,
            scope=scope,
            kind=kind,
            postgresql_ignore_search_path=postgresql_ignore_search_path,
            **kw,
        )
