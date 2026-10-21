"""Answer shapes and result formatting for the Graphiti MCP server.

Ported from Graphiti's mcp_server/src/models/response_types.py and utils/formatting.py. Goes
in: graph nodes and edges. Comes out: the plain dictionaries the tools return. It never
returns an embedding vector.
"""

from typing import Any, TypedDict

from graphiti_core.edges import EntityEdge
from graphiti_core.nodes import EntityNode


class ErrorResponse(TypedDict):
    """A tool call that failed, with the reason."""

    error: str


class SuccessResponse(TypedDict):
    """A tool call that worked, with a sentence saying what happened."""

    message: str


class NodeResult(TypedDict):
    """One entity as returned to the caller, without its embedding."""

    uuid: str
    name: str
    labels: list[str]
    created_at: str | None
    summary: str | None
    group_id: str
    attributes: dict[str, Any]


class NodeSearchResponse(TypedDict):
    """The answer to a node search."""

    message: str
    nodes: list[NodeResult]


class FactSearchResponse(TypedDict):
    """The answer to a fact search."""

    message: str
    facts: list[dict[str, Any]]


class EpisodeSearchResponse(TypedDict):
    """The answer to an episode listing."""

    message: str
    episodes: list[dict[str, Any]]


class StatusResponse(TypedDict):
    """The server's health: ``ok`` or ``error`` with a sentence."""

    status: str
    message: str


class SagaSummaryResponse(TypedDict):
    """A saga with its refreshed summary."""

    message: str
    uuid: str
    name: str
    summary: str


class CommunityResult(TypedDict):
    """One community as returned to the caller."""

    uuid: str
    name: str
    group_id: str
    summary: str | None


class BuildCommunitiesResponse(TypedDict):
    """The answer to a community build."""

    message: str
    community_count: int
    edge_count: int
    communities: list[CommunityResult]


class EdgeResult(TypedDict):
    """One fact as returned to the caller, without its embedding."""

    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    group_id: str
    created_at: str | None
    valid_at: str | None
    invalid_at: str | None


class TripletResponse(TypedDict):
    """The nodes and edges written by one add_triplet call."""

    message: str
    nodes: list[NodeResult]
    edges: list[EdgeResult]


class EpisodeEntitiesResponse(TypedDict):
    """The nodes and edges that specific episodes produced."""

    message: str
    nodes: list[NodeResult]
    edges: list[EdgeResult]


def to_node_result(node: EntityNode) -> NodeResult:
    """Build a NodeResult from an entity node, dropping every embedding.

    Goes in: the node. Comes out: the result dictionary. It never copies an attribute whose
    name mentions an embedding.
    """
    attributes = node.attributes if node.attributes else {}
    attributes = {key: value for key, value in attributes.items() if "embedding" not in key.lower()}
    return NodeResult(
        uuid=node.uuid,
        name=node.name,
        labels=node.labels if node.labels else [],
        created_at=node.created_at.isoformat() if node.created_at else None,
        summary=node.summary,
        group_id=node.group_id,
        attributes=attributes,
    )


def to_edge_result(edge: EntityEdge) -> EdgeResult:
    """Build an EdgeResult from an entity edge.

    Goes in: the edge. Comes out: the result dictionary with dates as text. It never includes
    the fact's embedding.
    """
    return EdgeResult(
        uuid=edge.uuid,
        name=edge.name,
        fact=edge.fact,
        source_node_uuid=edge.source_node_uuid,
        target_node_uuid=edge.target_node_uuid,
        group_id=edge.group_id,
        created_at=edge.created_at.isoformat() if edge.created_at else None,
        valid_at=edge.valid_at.isoformat() if edge.valid_at else None,
        invalid_at=edge.invalid_at.isoformat() if edge.invalid_at else None,
    )


def format_node_result(node: EntityNode) -> dict[str, Any]:
    """Format an entity node as a full plain dictionary without its name embedding.

    Goes in: the node. Comes out: its serialised form with dates as text. It never keeps the
    name embedding, at the top level or inside the attributes.
    """
    result = node.model_dump(mode="json", exclude={"name_embedding"})
    result.get("attributes", {}).pop("name_embedding", None)
    return result


def format_fact_result(edge: EntityEdge) -> dict[str, Any]:
    """Format an entity edge as a full plain dictionary without its fact embedding.

    Goes in: the edge. Comes out: its serialised form with dates as text. It never keeps the
    fact embedding, at the top level or inside the attributes.
    """
    result = edge.model_dump(mode="json", exclude={"fact_embedding"})
    result.get("attributes", {}).pop("fact_embedding", None)
    return result
