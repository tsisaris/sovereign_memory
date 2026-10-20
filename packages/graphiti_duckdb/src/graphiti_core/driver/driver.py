"""
Copyright 2024, Zep Software, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

import copy
import logging
import os
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from enum import Enum
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv

from graphiti_core.driver.graph_operations.graph_operations import GraphOperationsInterface
from graphiti_core.driver.query_executor import QueryExecutor, Transaction
from graphiti_core.driver.search_interface.search_interface import SearchInterface

if TYPE_CHECKING:
    from graphiti_core.driver.operations.community_edge_ops import CommunityEdgeOperations
    from graphiti_core.driver.operations.community_node_ops import CommunityNodeOperations
    from graphiti_core.driver.operations.entity_edge_ops import EntityEdgeOperations
    from graphiti_core.driver.operations.entity_node_ops import EntityNodeOperations
    from graphiti_core.driver.operations.episode_node_ops import EpisodeNodeOperations
    from graphiti_core.driver.operations.episodic_edge_ops import EpisodicEdgeOperations
    from graphiti_core.driver.operations.graph_ops import GraphMaintenanceOperations
    from graphiti_core.driver.operations.has_episode_edge_ops import HasEpisodeEdgeOperations
    from graphiti_core.driver.operations.next_episode_edge_ops import NextEpisodeEdgeOperations
    from graphiti_core.driver.operations.saga_node_ops import SagaNodeOperations
    from graphiti_core.driver.operations.search_ops import SearchOperations

logger = logging.getLogger(__name__)

DEFAULT_SIZE = 10

load_dotenv()

ENTITY_INDEX_NAME = os.environ.get("ENTITY_INDEX_NAME", "entities")
EPISODE_INDEX_NAME = os.environ.get("EPISODE_INDEX_NAME", "episodes")
COMMUNITY_INDEX_NAME = os.environ.get("COMMUNITY_INDEX_NAME", "communities")
ENTITY_EDGE_INDEX_NAME = os.environ.get("ENTITY_EDGE_INDEX_NAME", "entity_edges")


class GraphProvider(Enum):
    # Sovereign Memory fork: DuckDB is a distinct provider, never a Neo4j alias.
    """The graph databases Graphiti can run on: DuckDB (a provider of its own in this fork, never a Neo4j alias), Neo4j, FalkorDB, Kuzu and Neptune.

    It names providers only and does nothing.
    """

    DUCKDB = "duckdb"
    NEO4J = "neo4j"
    FALKORDB = "falkordb"
    KUZU = "kuzu"
    NEPTUNE = "neptune"


class GraphDriverSession(ABC):
    """The session a graph driver hands out for running queries.

    Subclasses say how to run a query, close the session and execute a write function. It holds no
    connection itself.
    """

    provider: GraphProvider

    async def __aenter__(self):
        """Enter the session's context and return the session.

        It takes nothing and opens nothing.
        """
        return self

    @abstractmethod
    async def __aexit__(self, exc_type, exc, tb):
        # No cleanup needed for Falkor, but method must exist
        """Leave the session's context.

        Goes in: the exception details (unused); comes out: nothing. Subclasses must provide it; the
        base does nothing, and no cleanup is needed for the Falkor driver.
        """
        pass

    @abstractmethod
    async def run(self, query: str, **kwargs: Any) -> Any:
        """Run one query on the session.

        Goes in: the query text and keyword parameters; comes out: the driver's result. The base
        class only raises NotImplementedError; a graph driver supplies the real operation.
        """
        raise NotImplementedError()

    @abstractmethod
    async def close(self):
        """Close the session.

        Goes in: nothing; comes out: nothing. The base class only raises NotImplementedError; a
        graph driver supplies the real operation.
        """
        raise NotImplementedError()

    @abstractmethod
    async def execute_write(self, func, *args, **kwargs):
        """Run a write function inside the session's write context.

        Goes in: the function and its arguments; comes out: what the function returns. The base
        class only raises NotImplementedError; a graph driver supplies the real operation.
        """
        raise NotImplementedError()


class GraphDriver(QueryExecutor, ABC):
    """The abstract graph driver.

    It defines how Graphiti runs queries, opens sessions, builds indexes and full-text queries and
    exposes the optional per-record operation sets. A concrete driver for one database subclasses
    it. It opens no connection itself.
    """

    provider: GraphProvider
    fulltext_syntax: str = (
        ""  # Neo4j (default) syntax does not require a prefix for fulltext queries
    )
    _database: str
    default_group_id: str = ""
    # Legacy interfaces (kept for backwards compatibility during Phase 1)
    search_interface: SearchInterface | None = None
    graph_operations_interface: GraphOperationsInterface | None = None

    @abstractmethod
    def execute_query(self, cypher_query_: str, **kwargs: Any) -> Coroutine:
        """Run one query against the graph.

        Goes in: the query text and keyword parameters; comes out: an awaitable result. The base
        class only raises NotImplementedError; a graph driver supplies the real operation.
        """
        raise NotImplementedError()

    @abstractmethod
    def session(self, database: str | None = None) -> GraphDriverSession:
        """Open a session on the named database (the default one when none is named).

        Goes in: an optional database name; comes out: a session. The base class only raises
        NotImplementedError; a graph driver supplies the real operation.
        """
        raise NotImplementedError()

    @abstractmethod
    def close(self):
        """Close the driver and release its connection.

        Goes in: nothing; comes out: nothing. The base class only raises NotImplementedError; a
        graph driver supplies the real operation.
        """
        raise NotImplementedError()

    @abstractmethod
    def delete_all_indexes(self) -> Coroutine:
        """Drop every index in the graph.

        Goes in: nothing; comes out: an awaitable. The base class only raises NotImplementedError; a
        graph driver supplies the real operation.
        """
        raise NotImplementedError()

    def with_database(self, database: str) -> GraphDriver:
        """
        Returns a shallow copy of this driver with a different default database.
        Reuses the same connection (e.g. FalkorDB, Neo4j).
        """
        cloned = copy.copy(self)
        cloned._database = database

        return cloned

    @abstractmethod
    async def build_indices_and_constraints(self, delete_existing: bool = False):
        """Create the indexes and constraints Graphiti needs, optionally removing existing ones first.

        Goes in: a flag to delete existing indexes; comes out: nothing. The base class only raises
        NotImplementedError; a graph driver supplies the real operation.
        """
        raise NotImplementedError()

    def clone(self, database: str) -> GraphDriver:
        """Clone the driver with a different database or graph name."""
        return self

    def build_fulltext_query(
        self, query: str, group_ids: list[str] | None = None, max_query_length: int = 128
    ) -> str:
        """
        Specific fulltext query builder for database providers.
        Only implemented by providers that need custom fulltext query building.
        """
        raise NotImplementedError(f"build_fulltext_query not implemented for {self.provider}")

    # --- New operations interfaces ---

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Transaction]:
        """Return a transaction context manager.

        Usage::

            async with driver.transaction() as tx:
                await ops.save(driver, node, tx=tx)

        Drivers with real transaction support (e.g., Neo4j) commit on clean exit
        and roll back on exception. Drivers without native transactions return a
        thin wrapper where queries execute immediately.

        The base implementation provides a no-op wrapper using the session. Drivers
        should override this to provide real transaction semantics where supported.
        """
        session = self.session()
        try:
            yield _SessionTransaction(session)
        finally:
            await session.close()

    @property
    def entity_node_ops(self) -> EntityNodeOperations | None:
        """The entity-node operation set of this driver, or None when the driver provides none.

        It takes nothing and returns None here; a driver overrides it.
        """
        return None

    @property
    def episode_node_ops(self) -> EpisodeNodeOperations | None:
        """The episode-node operation set of this driver, or None when the driver provides none.

        It returns None here; a driver overrides it.
        """
        return None

    @property
    def community_node_ops(self) -> CommunityNodeOperations | None:
        """The community-node operation set of this driver, or None when the driver provides none.

        It returns None here; a driver overrides it.
        """
        return None

    @property
    def saga_node_ops(self) -> SagaNodeOperations | None:
        """The saga-node operation set of this driver, or None when the driver provides none.

        It returns None here; a driver overrides it.
        """
        return None

    @property
    def entity_edge_ops(self) -> EntityEdgeOperations | None:
        """The entity-edge operation set of this driver, or None when the driver provides none.

        It returns None here; a driver overrides it.
        """
        return None

    @property
    def episodic_edge_ops(self) -> EpisodicEdgeOperations | None:
        """The episodic-edge operation set of this driver, or None when the driver provides none.

        It returns None here; a driver overrides it.
        """
        return None

    @property
    def community_edge_ops(self) -> CommunityEdgeOperations | None:
        """The community-edge operation set of this driver, or None when the driver provides none.

        It returns None here; a driver overrides it.
        """
        return None

    @property
    def has_episode_edge_ops(self) -> HasEpisodeEdgeOperations | None:
        """The has-episode-edge operation set of this driver, or None when the driver provides none.

        It returns None here; a driver overrides it.
        """
        return None

    @property
    def next_episode_edge_ops(self) -> NextEpisodeEdgeOperations | None:
        """The next-episode-edge operation set of this driver, or None when the driver provides none.

        It returns None here; a driver overrides it.
        """
        return None

    @property
    def search_ops(self) -> SearchOperations | None:
        """The search operation set of this driver, or None when the driver provides none.

        It returns None here; a driver overrides it.
        """
        return None

    @property
    def graph_ops(self) -> GraphMaintenanceOperations | None:
        """The graph-maintenance operation set of this driver, or None when the driver provides none.

        It returns None here; a driver overrides it.
        """
        return None


class _SessionTransaction(Transaction):
    """Fallback transaction that wraps a session — queries execute immediately."""

    def __init__(self, session: GraphDriverSession):
        self._session = session

    async def run(self, query: str, **kwargs: Any) -> Any:
        return await self._session.run(query, **kwargs)
