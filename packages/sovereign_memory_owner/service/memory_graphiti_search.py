"""Graphiti's own keyword search over the stored graph, for the context block, with no numbers.

It is for finding the facts, entities, episodes and communities that match a question by
words alone, using Graphiti's search with only its keyword (BM25) method and rank fusion, so
no number and no model is needed. It takes an open handle on the memory database, the group
ids to search and a question. It gives back Graphiti's SearchResults. It never writes, never
calls a model, and answers an empty result when the graph has never been written.
"""

import asyncio
from typing import Any

from graphiti_core.graphiti_types import GraphitiClients
from graphiti_core.llm_client.client import LLMClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.search.search import search
from graphiti_core.search.search_config import (
    CommunityReranker,
    CommunitySearchConfig,
    CommunitySearchMethod,
    EdgeReranker,
    EdgeSearchConfig,
    EdgeSearchMethod,
    EpisodeReranker,
    EpisodeSearchConfig,
    EpisodeSearchMethod,
    NodeReranker,
    NodeSearchConfig,
    NodeSearchMethod,
    SearchConfig,
    SearchResults,
)
from graphiti_core.search.search_filters import SearchFilters
from graphiti_core.tracer import NoOpTracer
from graphiti_duckdb.duckdb_driver import DuckDBDriver

from service.memory_graphiti_episodes import _has_graphiti_schema
from service.memory_model_graphiti import NoNumbersCrossEncoder, NoNumbersEmbedder


class NoModelClient(LLMClient):
    """A language-model slot that refuses, because keyword search never asks a model.

    It is for filling the model slot Graphiti's client bundle requires. It takes nothing it
    would answer. It never returns text.
    """

    def __init__(self) -> None:
        """Build the empty configuration the base class needs; no model is named."""
        super().__init__(LLMConfig())

    async def _generate_response(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Refuse: a search that reaches the model has been wired wrongly."""
        raise RuntimeError("keyword search must not call a language model")


def keyword_search(handle: Any, groups: list[str] | None, query: str, limit: int) -> SearchResults:
    """Return the facts, entities, episodes and communities matching a question by words.

    It takes the owner's open handle, the group ids to search (None for every group), the
    question and the largest number of results of each kind. It runs Graphiti's search with
    its keyword method and reciprocal rank fusion only, driving the database coroutines to
    completion on the calling worker thread. It returns an empty result when the Graphiti
    schema does not exist yet, and it writes nothing.
    """
    if not _has_graphiti_schema(handle):
        return SearchResults()
    clients = GraphitiClients(
        driver=DuckDBDriver(connection=handle),
        llm_client=NoModelClient(),
        embedder=NoNumbersEmbedder(),
        cross_encoder=NoNumbersCrossEncoder(),
        tracer=NoOpTracer(),
    )
    config = SearchConfig(
        edge_config=EdgeSearchConfig(
            search_methods=[EdgeSearchMethod.bm25], reranker=EdgeReranker.rrf
        ),
        node_config=NodeSearchConfig(
            search_methods=[NodeSearchMethod.bm25], reranker=NodeReranker.rrf
        ),
        episode_config=EpisodeSearchConfig(
            search_methods=[EpisodeSearchMethod.bm25], reranker=EpisodeReranker.rrf
        ),
        community_config=CommunitySearchConfig(
            search_methods=[CommunitySearchMethod.bm25], reranker=CommunityReranker.rrf
        ),
        limit=limit,
    )
    return asyncio.run(search(clients, query, groups, config, SearchFilters()))
