import re

with open("vendor/graphiti/graphiti_core/search/search_utils.py", "r") as f:
    content = f.read()

# patch get_relevant_nodes
start_idx = content.find("async def get_relevant_nodes")
end_idx = content.find("async def get_relevant_edges")
sub = content[start_idx:end_idx]
sub = re.sub(
    r"if driver\.provider == GraphProvider\.KUZU:[\s\S]*?relevant_nodes_dict: dict\[str, list\[EntityNode\]\] = {",
    """results = await driver.search_ops.get_relevant_nodes(
    driver, query_nodes, search_filter, group_id, min_score, limit
)

relevant_nodes_dict: dict[str, list[EntityNode]] = {""",
    sub
)
content = content[:start_idx] + sub + content[end_idx:]

# patch get_relevant_edges
start_idx = content.find("async def get_relevant_edges")
end_idx = content.find("async def get_edge_invalidation_candidates")
sub = content[start_idx:end_idx]
sub = re.sub(
    r"if driver\.provider == GraphProvider\.NEPTUNE:[\s\S]*?relevant_edges_dict: dict\[str, list\[EntityEdge\]\] = {",
    """results = await driver.search_ops.get_relevant_edges(
    driver, [edge.model_dump() for edge in edges], search_filter, min_score, limit
)

relevant_edges_dict: dict[str, list[EntityEdge]] = {""",
    sub
)
content = content[:start_idx] + sub + content[end_idx:]

# patch get_edge_invalidation_candidates
start_idx = content.find("async def get_edge_invalidation_candidates")
end_idx = content.find("def rrf(")
sub = content[start_idx:end_idx]
sub = re.sub(
    r"if driver\.provider == GraphProvider\.NEPTUNE:[\s\S]*?invalidation_edges_dict: dict\[str, list\[EntityEdge\]\] = {",
    """results = await driver.search_ops.get_edge_invalidation_candidates(
    driver, [edge.model_dump() for edge in edges], search_filter, min_score, limit
)

invalidation_edges_dict: dict[str, list[EntityEdge]] = {""",
    sub
)
content = content[:start_idx] + sub + content[end_idx:]

# patch get_mentioned_nodes
sub = """async def get_mentioned_nodes(driver: GraphDriver, episodes: list[EpisodicNode]) -> list[EntityNode]:
    if hasattr(driver, "graph_ops") and driver.graph_ops is not None:
        return await driver.graph_ops.get_mentioned_nodes(driver, episodes)

    episode_uuids = [episode.uuid for episode in episodes]
"""
content = re.sub(r"async def get_mentioned_nodes\(driver: GraphDriver, episodes: list\[EpisodicNode\]\) -> list\[EntityNode\]:\n    episode_uuids = \[episode\.uuid for episode in episodes\]", sub, content, count=1)

with open("vendor/graphiti/graphiti_core/search/search_utils.py", "w") as f:
    f.write(content)
