"""DuckDB Relational Adapter for Cognee."""

import asyncio
import logging
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from cognee.infrastructure.databases.relational.duckdb_reset import reset_relational_tables
from cognee.infrastructure.databases.relational.sqlalchemy.SqlAlchemyAdapter import (
    SQLAlchemyAdapter,
)

logger = logging.getLogger(__name__)


class DuckDBRelationalAdapter(SQLAlchemyAdapter):
    """Adapt Cognee's SQLAlchemy relational operations for DuckDB."""

    async def get_schema_list(self) -> list[str]:
        """Return a list of all schema names in the database."""
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT schema_name FROM information_schema.schemata
                    WHERE schema_name NOT IN ('information_schema', 'pg_catalog', 'main');
                    """
                )
            )
            return [schema[0] for schema in result.fetchall()]

    async def get_table_names(self) -> list[str]:
        """Return every base table of the current DuckDB catalog, ordered by schema and name.

        Tables in ``main`` come back unqualified and tables in other schemas as
        ``schema.table``, the same keys ``extract_schema`` uses. System schemas,
        views and tables of other attached catalogs are never listed.
        """
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT table_schema, table_name FROM information_schema.tables
                    WHERE table_catalog = current_database()
                      AND table_type = 'BASE TABLE'
                      AND table_schema NOT IN ('information_schema', 'pg_catalog')
                    ORDER BY table_schema, table_name;
                    """
                )
            )
            return [
                table_name if schema_name == "main" else f"{schema_name}.{table_name}"
                for schema_name, table_name in result.fetchall()
            ]

    async def delete_database(self):
        """Reset verified Cognee relational tables while preserving the shared database file."""
        async with self.engine.begin() as connection:
            await connection.run_sync(reset_relational_tables)
        await self.engine.dispose(close=True)
        logger.info("Cognee relational tables reset; shared DuckDB file preserved.")

    async def extract_schema(self):
        """Extract schema info (tables, columns, constraints)."""
        schema_list = await self.get_schema_list()
        schema_list.append("main")  # include default duckdb schema

        async with self.engine.begin() as connection:
            schema = {}
            for schema_name in schema_list:
                tables = await connection.run_sync(
                    lambda sync_conn, schema_name=schema_name: inspect(sync_conn).get_table_names(
                        schema=schema_name
                    )
                )
                for table_name in tables:
                    key = table_name if schema_name == "main" else f"{schema_name}.{table_name}"
                    schema[key] = {"columns": [], "primary_key": None, "foreign_keys": []}

                    def get_details(sync_conn, table, schema_name):
                        insp = inspect(sync_conn)
                        cols = insp.get_columns(table, schema=schema_name)
                        pk = insp.get_pk_constraint(table, schema=schema_name)
                        fks = insp.get_foreign_keys(table, schema=schema_name)
                        return cols, pk, fks

                    cols, pk, fks = await connection.run_sync(get_details, table_name, schema_name)

                    for column in cols:
                        schema[key]["columns"].append(
                            {"name": column["name"], "type": str(column["type"])}
                        )
                    pk_columns = pk.get("constrained_columns", [])
                    if pk_columns:
                        schema[key]["primary_key"] = pk_columns[0]
                    for fk in fks:
                        for col, ref_col in zip(
                            fk.get("constrained_columns", []), fk.get("referred_columns", [])
                        ):
                            if col and ref_col:
                                schema[key]["foreign_keys"].append(
                                    {
                                        "column": col,
                                        "ref_table": fk.get("referred_table"),
                                        "ref_column": ref_col,
                                    }
                                )
            return schema

    def _configure_sqlite_engine(self) -> None:
        """Override to prevent SQLite WAL/PRAGMA injection on DuckDB connections."""
        pass
