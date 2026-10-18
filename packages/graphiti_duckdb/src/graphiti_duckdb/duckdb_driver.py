import copy
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Coroutine

import duckdb
from graphiti_core.driver.driver import (
    GraphDriver,
    GraphDriverSession,
    GraphProvider,
)
from graphiti_core.driver.query_executor import QueryExecutor, Transaction

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

logger = logging.getLogger(__name__)

SCHEMA_QUERIES = [
    "CREATE TABLE IF NOT EXISTS graph_node (uuid VARCHAR PRIMARY KEY, label VARCHAR, properties JSON)",
    "CREATE TABLE IF NOT EXISTS graph_edge (uuid VARCHAR PRIMARY KEY, source_node_uuid VARCHAR, target_node_uuid VARCHAR, label VARCHAR, properties JSON)",
    "CREATE SEQUENCE IF NOT EXISTS graph_fts_gen_seq START 1",
    "CREATE TABLE IF NOT EXISTS graph_fts_generations (gen_id BIGINT PRIMARY KEY, created_at TIMESTAMP)",
]


class DuckDBTransaction(Transaction):
    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self._conn = conn

    async def run(self, query: str, **kwargs: Any) -> tuple[list[dict[str, Any]], Any, Any]:
        return [], None, None


class DuckDBDriverSession(GraphDriverSession):
    provider = GraphProvider.DUCKDB

    def __init__(self, driver: "DuckDBDriver"):
        self.driver = driver
        self.conn = duckdb.connect(self.driver.db)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.conn.close()

    async def run(self, query: str, **kwargs: Any) -> Any:
        return await self.driver.execute_query(query, **kwargs)

    async def close(self):
        self.conn.close()

    async def execute_write(self, func, *args, **kwargs):
        self.conn.execute("BEGIN TRANSACTION")
        try:
            res = await func(self, *args, **kwargs)
            self.conn.execute("COMMIT")
            return res
        except Exception as e:
            self.conn.execute("ROLLBACK")
            raise e


class DuckDBDriver(GraphDriver):
    provider = GraphProvider.DUCKDB
    fulltext_syntax = ""

    def __init__(self, db: str = ":memory:"):
        super().__init__()
        self.db = db
        self.conn = duckdb.connect(self.db)
        self.setup_schema()

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
        return self._entity_node_ops

    @property
    def episode_node_ops(self):
        return self._episode_node_ops

    @property
    def community_node_ops(self):
        return self._community_node_ops

    @property
    def saga_node_ops(self):
        return self._saga_node_ops

    @property
    def entity_edge_ops(self):
        return self._entity_edge_ops

    @property
    def episodic_edge_ops(self):
        return self._episodic_edge_ops

    @property
    def community_edge_ops(self):
        return self._community_edge_ops

    @property
    def has_episode_edge_ops(self):
        return self._has_episode_edge_ops

    @property
    def next_episode_edge_ops(self):
        return self._next_episode_edge_ops

    @property
    def search_ops(self):
        return self._search_ops

    @property
    def graph_ops(self):
        return self._graph_ops

    async def execute_query(
        self, cypher_query_: str, **kwargs: Any
    ) -> tuple[list[dict[str, Any]], Any, Any]:
        cypher = cypher_query_.strip()
        if "RETURN COUNT(n) as count" in cypher:
            if "WHERE n.uuid IN $uuids" in cypher:
                conn = self.conn
                uuids = kwargs.get("uuids", [])
                if not uuids:
                    return [{"count": 0}], None, None
                uuids_str = ",".join([f"'{u}'" for u in uuids])
                res_nodes = conn.execute(
                    f"SELECT COUNT(*) FROM graph_node WHERE uuid IN ({uuids_str})"
                ).fetchone()[0]
                res_edges = conn.execute(
                    f"SELECT COUNT(*) FROM graph_edge WHERE uuid IN ({uuids_str})"
                ).fetchone()[0]
                return [{"count": res_nodes + res_edges}], None, None

        if "RETURN COUNT(e) as count" in cypher:
            if "WHERE e.uuid IN $uuids" in cypher:
                conn = self.conn
                uuids = kwargs.get("uuids", [])
                if not uuids:
                    return [{"count": 0}], None, None
                uuids_str = ",".join([f"'{u}'" for u in uuids])
                res = conn.execute(
                    f"SELECT COUNT(*) FROM graph_edge WHERE uuid IN ({uuids_str})"
                ).fetchone()[0]
                return [{"count": res}], None, None

        raise NotImplementedError(f"DuckDB backend does not support raw Cypher queries: {cypher}")

    def session(self, _database: str | None = None) -> GraphDriverSession:
        return DuckDBDriverSession(self)

    async def close(self):
        self.conn.close()

    def delete_all_indexes(self):
        pass

    async def build_indices_and_constraints(self, delete_existing: bool = False):
        pass

    def setup_schema(self):
        for q in SCHEMA_QUERIES:
            self.conn.execute(q)
