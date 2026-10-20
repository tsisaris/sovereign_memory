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

from enum import Enum

from pydantic import BaseModel, Field

from graphiti_core.edges import EntityEdge
from graphiti_core.nodes import CommunityNode, EntityNode, EpisodicNode
from graphiti_core.search.search_utils import (
    DEFAULT_MIN_SCORE,
    DEFAULT_MMR_LAMBDA,
    MAX_SEARCH_DEPTH,
)

DEFAULT_SEARCH_LIMIT = 10


class EdgeSearchMethod(Enum):
    """The ways to search entity edges: by cosine similarity, by BM25 text match or by breadth-first walk.

    It names methods only.
    """

    cosine_similarity = "cosine_similarity"
    bm25 = "bm25"
    bfs = "breadth_first_search"


class NodeSearchMethod(Enum):
    """The ways to search entity nodes: by cosine similarity, by BM25 text match or by breadth-first walk.

    It names methods only.
    """

    cosine_similarity = "cosine_similarity"
    bm25 = "bm25"
    bfs = "breadth_first_search"


class EpisodeSearchMethod(Enum):
    """The ways to search episodes: BM25 text match only. It names methods only."""

    bm25 = "bm25"


class CommunitySearchMethod(Enum):
    """The ways to search communities: by cosine similarity or by BM25 text match.

    It names methods only.
    """

    cosine_similarity = "cosine_similarity"
    bm25 = "bm25"


class EdgeReranker(Enum):
    """The ways to re-rank edge search results: reciprocal rank fusion, node distance, episode mentions, MMR or a cross-encoder.

    It names methods only.
    """

    rrf = "reciprocal_rank_fusion"
    node_distance = "node_distance"
    episode_mentions = "episode_mentions"
    mmr = "mmr"
    cross_encoder = "cross_encoder"


class NodeReranker(Enum):
    """The ways to re-rank node search results: reciprocal rank fusion, node distance, episode mentions, MMR or a cross-encoder.

    It names methods only.
    """

    rrf = "reciprocal_rank_fusion"
    node_distance = "node_distance"
    episode_mentions = "episode_mentions"
    mmr = "mmr"
    cross_encoder = "cross_encoder"


class EpisodeReranker(Enum):
    """The ways to re-rank episode search results: reciprocal rank fusion or a cross-encoder.

    It names methods only.
    """

    rrf = "reciprocal_rank_fusion"
    cross_encoder = "cross_encoder"


class CommunityReranker(Enum):
    """The ways to re-rank community search results: reciprocal rank fusion, MMR or a cross-encoder.

    It names methods only.
    """

    rrf = "reciprocal_rank_fusion"
    mmr = "mmr"
    cross_encoder = "cross_encoder"


class EdgeSearchConfig(BaseModel):
    """The settings for one edge search: which search methods to use, the re-ranker, the minimum similarity, the MMR weight and the breadth-first depth.

    It holds values only.
    """

    search_methods: list[EdgeSearchMethod]
    reranker: EdgeReranker = Field(default=EdgeReranker.rrf)
    sim_min_score: float = Field(default=DEFAULT_MIN_SCORE)
    mmr_lambda: float = Field(default=DEFAULT_MMR_LAMBDA)
    bfs_max_depth: int = Field(default=MAX_SEARCH_DEPTH)


class NodeSearchConfig(BaseModel):
    """The settings for one node search: which search methods to use, the re-ranker, the minimum similarity, the MMR weight and the breadth-first depth.

    It holds values only.
    """

    search_methods: list[NodeSearchMethod]
    reranker: NodeReranker = Field(default=NodeReranker.rrf)
    sim_min_score: float = Field(default=DEFAULT_MIN_SCORE)
    mmr_lambda: float = Field(default=DEFAULT_MMR_LAMBDA)
    bfs_max_depth: int = Field(default=MAX_SEARCH_DEPTH)


class EpisodeSearchConfig(BaseModel):
    """The settings for one episode search: which search methods to use, the re-ranker, the minimum similarity, the MMR weight and the breadth-first depth.

    It holds values only.
    """

    search_methods: list[EpisodeSearchMethod]
    reranker: EpisodeReranker = Field(default=EpisodeReranker.rrf)
    sim_min_score: float = Field(default=DEFAULT_MIN_SCORE)
    mmr_lambda: float = Field(default=DEFAULT_MMR_LAMBDA)
    bfs_max_depth: int = Field(default=MAX_SEARCH_DEPTH)


class CommunitySearchConfig(BaseModel):
    """The settings for one community search: which search methods to use, the re-ranker, the minimum similarity, the MMR weight and the breadth-first depth.

    It holds values only.
    """

    search_methods: list[CommunitySearchMethod]
    reranker: CommunityReranker = Field(default=CommunityReranker.rrf)
    sim_min_score: float = Field(default=DEFAULT_MIN_SCORE)
    mmr_lambda: float = Field(default=DEFAULT_MMR_LAMBDA)
    bfs_max_depth: int = Field(default=MAX_SEARCH_DEPTH)


class SearchConfig(BaseModel):
    """The settings for a whole search: the edge, node, episode and community configurations, the result limit and the minimum re-ranker score.

    It holds values only.
    """

    edge_config: EdgeSearchConfig | None = Field(default=None)
    node_config: NodeSearchConfig | None = Field(default=None)
    episode_config: EpisodeSearchConfig | None = Field(default=None)
    community_config: CommunitySearchConfig | None = Field(default=None)
    limit: int = Field(default=DEFAULT_SEARCH_LIMIT)
    reranker_min_score: float = Field(default=0)


class SearchResults(BaseModel):
    """What a search returns: edges, nodes, episodes and communities, each with its re-ranker scores.

    It carries data only and does nothing.
    """

    edges: list[EntityEdge] = Field(default_factory=list)
    edge_reranker_scores: list[float] = Field(default_factory=list)
    nodes: list[EntityNode] = Field(default_factory=list)
    node_reranker_scores: list[float] = Field(default_factory=list)
    episodes: list[EpisodicNode] = Field(default_factory=list)
    episode_reranker_scores: list[float] = Field(default_factory=list)
    communities: list[CommunityNode] = Field(default_factory=list)
    community_reranker_scores: list[float] = Field(default_factory=list)

    @classmethod
    def merge(cls, results_list: list["SearchResults"]) -> "SearchResults":
        """
        Merge multiple SearchResults objects into a single SearchResults object.

        Parameters
        ----------
        results_list : list[SearchResults]
            List of SearchResults objects to merge

        Returns
        -------
        SearchResults
            A single SearchResults object containing all results
        """
        if not results_list:
            return cls()

        merged = cls()
        for result in results_list:
            merged.edges.extend(result.edges)
            merged.edge_reranker_scores.extend(result.edge_reranker_scores)
            merged.nodes.extend(result.nodes)
            merged.node_reranker_scores.extend(result.node_reranker_scores)
            merged.episodes.extend(result.episodes)
            merged.episode_reranker_scores.extend(result.episode_reranker_scores)
            merged.communities.extend(result.communities)
            merged.community_reranker_scores.extend(result.community_reranker_scores)

        return merged
