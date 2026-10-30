import re

with open("vendor/graphiti/graphiti_core/search/search_utils.py", "r") as f:
    content = f.read()

sub = """async def get_mentioned_nodes(driver: GraphDriver, episodes: list[EpisodicNode]) -> list[EntityNode]:
    if hasattr(driver, "graph_ops") and driver.graph_ops is not None:
        return await driver.graph_ops.get_mentioned_nodes(driver, episodes)
"""

content = re.sub(r"async def get_mentioned_nodes\(driver: GraphDriver, episodes: list\[EpisodicNode\]\) -> list\[EntityNode\]:\n    if hasattr\(driver, \"graph_ops\"\) and driver\.graph_ops is not None:\n        return await driver\.graph_ops\.get_mentioned_nodes\(driver, episodes\)[\s\S]*?return \[get_entity_node_from_record\(r, driver\.provider\) for r in records\]", sub, content)

with open("vendor/graphiti/graphiti_core/search/search_utils.py", "w") as f:
    f.write(content)

