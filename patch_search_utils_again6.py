import re

with open("vendor/graphiti/graphiti_core/search/search_utils.py", "r") as f:
    content = f.read()

sub = """async def get_mentioned_nodes(driver: GraphDriver, episodes: list[EpisodicNode]) -> list[EntityNode]:
    if hasattr(driver, "graph_ops") and driver.graph_ops is not None:
        return await driver.graph_ops.get_mentioned_nodes(driver, episodes)

    episode_uuids = [episode.uuid for episode in episodes]
"""

# Let's replace the whole get_mentioned_nodes to be 100% sure we don't fall back to cypher
start_idx = content.find("async def get_mentioned_nodes")
end_idx = content.find("def labels(")

sub2 = """async def get_mentioned_nodes(driver: GraphDriver, episodes: list[EpisodicNode]) -> list[EntityNode]:
    if hasattr(driver, "graph_ops") and driver.graph_ops is not None:
        return await driver.graph_ops.get_mentioned_nodes(driver, episodes)

    episode_uuids = [episode.uuid for episode in episodes]
    records, _, _ = await driver.execute_query(
        \"\"\"
        MATCH (episode:Episodic)-[:MENTIONS]->(n:Entity)
        WHERE episode.uuid IN $uuids
        RETURN DISTINCT
        \"\"\"
        + get_entity_node_return_query(driver.provider),
        uuids=episode_uuids,
        routing_='r',
    )
    return [get_entity_node_from_record(r, driver.provider) for r in records]

"""

content = content[:start_idx] + sub2 + content[end_idx:]

with open("vendor/graphiti/graphiti_core/search/search_utils.py", "w") as f:
    f.write(content)
