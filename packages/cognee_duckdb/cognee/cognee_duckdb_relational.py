"""DuckDB Relational Adapter for Cognee."""

import asyncio
import gc
import logging
from os import path

from sqlalchemy import MetaData, inspect, text

from cognee.infrastructure.databases.relational.sqlalchemy.SqlAlchemyAdapter import (
    SQLAlchemyAdapter,
)
from cognee.infrastructure.files.storage import get_file_storage

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

    async def delete_database(self):
        """Delete the entire database, including all tables and files."""
        try:
            await self.engine.dispose(close=True)
            # Drop the cached engine so file handle gets released
            from cognee.infrastructure.databases.relational.create_relational_engine import (
                create_relational_engine,
            )

            create_relational_engine.cache_clear()
            gc.collect()
            await asyncio.sleep(2)
            db_directory = path.dirname(self.db_path)
            file_name = path.basename(self.db_path)
            file_storage = get_file_storage(db_directory)
            for attempt in range(10):
                try:
                    await file_storage.remove(file_name)
                    break
                except (PermissionError, OSError) as remove_error:
                    if attempt == 9:
                        logger.warning(
                            "Could not remove duckdb database file %s after retries: %s",
                            file_name,
                            remove_error,
                        )
                        break
                    gc.collect()
                    await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error deleting database: {e}")
            raise
        logger.info("Database deleted successfully.")

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
