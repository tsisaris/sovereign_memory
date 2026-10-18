"""Run Graphiti's native DuckDB operations on correctly owned transaction handles."""

from contextlib import asynccontextmanager
from typing import Any

import duckdb
from duckdb_sqlalchemy_async.native import connect_native, require_native_connection
from graphiti_core.driver.driver import GraphDriver, GraphDriverSession, GraphProvider
from graphiti_core.driver.query_executor import Transaction

from .graph_operations import DuckDBGraphOperations
from .operations import (
    DuckDBCommunityEdgeOps,
    DuckDBCommunityNodeOps,
    DuckDBEntityEdgeOps,
    DuckDBEntityNodeOps,
    DuckDBEpisodeNodeOps,
    DuckDBEpisodicEdgeOps,
    DuckDBGraphOps,
    DuckDBHasEpisodeEdgeOps,
    DuckDBNextEpisodeEdgeOps,
    DuckDBSagaNodeOps,
    DuckDBSearchOps,
)
from .search_interface import DuckDBSearchInterface

SCHEMA_QUERIES = (
    "CREATE SCHEMA IF NOT EXISTS graphiti",
    "CREATE TABLE IF NOT EXISTS graphiti.graph_node "
    "(uuid VARCHAR PRIMARY KEY, label VARCHAR, properties JSON)",
    "CREATE TABLE IF NOT EXISTS graphiti.graph_edge "
    "(uuid VARCHAR PRIMARY KEY, source_node_uuid VARCHAR, target_node_uuid VARCHAR, "
    "label VARCHAR, properties JSON)",
    "CREATE SEQUENCE IF NOT EXISTS graphiti.graph_fts_gen_seq START 1",
    "CREATE TABLE IF NOT EXISTS graphiti.graph_fts_generations "
    "(gen_id BIGINT PRIMARY KEY, created_at TIMESTAMP)",
)


def _run_native(connection, query, parameters):
    """Execute one bound native SQL statement and return its actual buffered records."""
    try:
        statements = connection.extract_statements(query)
    except duckdb.ParserException as error:
        raise duckdb.ParserException(
            "The DuckDB Graphiti executor requires valid native SQL; Cypher is unsupported"
        ) from error
    if len(statements) != 1:
        raise ValueError("The DuckDB Graphiti executor requires one SQL statement")
    result = connection.execute(statements[0], parameters)
    columns = [column[0] for column in result.description or []]
    records = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
    return records, None, None


class DuckDBTransaction(Transaction):
    """Bind native operations to the handle whose transaction the caller controls."""

    def __init__(self, connection):
        """Borrow an already selected handle without beginning or committing work."""
        self.connection = connection

    async def run(self, query: str, **kwargs: Any):
        """Execute actual parameterized SQL on this transaction's existing handle."""
        kwargs.pop("routing_", None)
        kwargs.pop("database_", None)
        parameters = kwargs.pop("parameters", None)
        if parameters is not None and kwargs:
            raise ValueError("Use either explicit parameters or named query arguments")
        return _run_native(
            self.connection, query, parameters if parameters is not None else kwargs or None
        )


class DuckDBDriverSession(GraphDriverSession):
    """Isolate an owned session or borrow the owner's explicitly supplied handle."""

    provider = GraphProvider.DUCKDB

    def __init__(self, driver):
        """Reuse the root database while retaining transaction and closure ownership."""
        self.driver = driver
        self.owns_handle = driver.owns_connection
        self.conn = driver.conn.cursor() if self.owns_handle else driver.conn

    async def __aenter__(self):
        """Return this session without changing the current transaction."""
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """Close only the session's owned handle after its caller exits."""
        await self.close()

    async def run(self, query: str, **kwargs: Any):
        """Run native SQL on this session rather than the driver's other handle."""
        return await DuckDBTransaction(self.conn).run(query, **kwargs)

    async def close(self):
        """Release an owned session connection, leaving a borrowed owner untouched."""
        if self.owns_handle:
            self.conn.close()

    @asynccontextmanager
    async def transaction(self):
        """Commit or roll back only an owned transaction on the same operation handle."""
        if not self.owns_handle:
            yield DuckDBTransaction(self.conn)
            return
        self.conn.execute("BEGIN TRANSACTION")
        try:
            yield DuckDBTransaction(self.conn)
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    async def execute_write(self, func, *args, **kwargs):
        """Pass the callback a real transaction bound to this session's selected handle."""
        async with self.transaction() as transaction:
            return await func(transaction, *args, **kwargs)


class DuckDBDriver(GraphDriver):
    """Keep Graphiti-owned storage separate from other engines in the same DuckDB file."""

    provider = GraphProvider.DUCKDB
    fulltext_syntax = ""

    def __init__(self, db: str = ":memory:", connection=None):
        """Open owned storage or borrow the caller's exclusively managed native handle."""
        super().__init__()
        self.db = db
        self._database = db
        self.owns_connection = connection is None
        if connection is not None:
            require_native_connection(connection)
        self.conn = connect_native(db) if connection is None else connection
        self.setup_schema()
        self.graph_operations_interface = DuckDBGraphOperations()
        self.search_interface = DuckDBSearchInterface()
        self._entity_node_ops = DuckDBEntityNodeOps()
        self._episode_node_ops = DuckDBEpisodeNodeOps()
        self._community_node_ops = DuckDBCommunityNodeOps()
        self._saga_node_ops = DuckDBSagaNodeOps()
        self._entity_edge_ops = DuckDBEntityEdgeOps()
        self._episodic_edge_ops = DuckDBEpisodicEdgeOps()
        self._community_edge_ops = DuckDBCommunityEdgeOps()
        self._has_episode_edge_ops = DuckDBHasEpisodeEdgeOps()
        self._next_episode_edge_ops = DuckDBNextEpisodeEdgeOps()
        self._search_ops = DuckDBSearchOps()
        self._graph_ops = DuckDBGraphOps()

    @property
    def entity_node_ops(self):
        """Return this driver's entity storage operations."""
        return self._entity_node_ops

    @property
    def episode_node_ops(self):
        """Return this driver's episode operation object."""
        return self._episode_node_ops

    @property
    def community_node_ops(self):
        """Return this driver's community operation object."""
        return self._community_node_ops

    @property
    def saga_node_ops(self):
        """Return this driver's saga operation object."""
        return self._saga_node_ops

    @property
    def entity_edge_ops(self):
        """Return this driver's entity-edge operation object."""
        return self._entity_edge_ops

    @property
    def episodic_edge_ops(self):
        """Return this driver's episodic-edge operation object."""
        return self._episodic_edge_ops

    @property
    def community_edge_ops(self):
        """Return this driver's community-edge operation object."""
        return self._community_edge_ops

    @property
    def has_episode_edge_ops(self):
        """Return this driver's saga membership operation object."""
        return self._has_episode_edge_ops

    @property
    def next_episode_edge_ops(self):
        """Return this driver's episode ordering operation object."""
        return self._next_episode_edge_ops

    @property
    def search_ops(self):
        """Return this driver's search operation object."""
        return self._search_ops

    @property
    def graph_ops(self):
        """Return this driver's schema-scoped graph maintenance operations."""
        return self._graph_ops

    async def execute_query(self, cypher_query_: str, **kwargs: Any):
        """Run native SQL with bound parameters, rejecting unsupported Cypher explicitly."""
        async with self.session() as session:
            return await session.run(cypher_query_, **kwargs)

    def session(self, database: str | None = None) -> GraphDriverSession:
        """Create a transaction-isolated session over the same actual database."""
        if database is not None and database != self._database:
            raise ValueError("A DuckDB session must use its configured database")
        return DuckDBDriverSession(self)

    @asynccontextmanager
    async def transaction(self):
        """Yield an actual same-handle transaction, retaining borrowed caller ownership."""
        async with self.session() as session:
            async with session.transaction() as transaction:
                yield transaction

    async def close(self):
        """Close only the root connection this driver opened."""
        if self.owns_connection:
            self.conn.close()

    async def delete_all_indexes(self):
        """Drop only explicit Graphiti indexes in the current catalog."""
        await self.graph_ops.delete_all_indexes(self)

    async def build_indices_and_constraints(self, delete_existing: bool = False):
        """Ensure native UUID keys and B-tree lookup indexes within the Graphiti schema."""
        self.setup_schema()
        await self.graph_ops.build_indices_and_constraints(self, delete_existing=delete_existing)

    def setup_schema(self):
        """Create only explicitly qualified Graphiti tables and generation metadata."""
        for query in SCHEMA_QUERIES:
            self.conn.execute(query)
