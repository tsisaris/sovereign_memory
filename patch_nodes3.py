import re

with open("vendor/graphiti/graphiti_core/nodes.py", "r") as f:
    content = f.read()

# EpisodicNode.get_by_uuid
sub_episodic_get_by_uuid = """
    @classmethod
    async def get_by_uuid(cls, driver: GraphDriver, uuid: str):
        if hasattr(driver, "episode_node_ops") and driver.episode_node_ops is not None:
            return await driver.episode_node_ops.get_by_uuid(driver, uuid)

        records, _, _ = await driver.execute_query(
"""
content = re.sub(r"    @classmethod\n    async def get_by_uuid\(cls, driver: GraphDriver, uuid: str\):\n        if driver\.graph_operations_interface:[\s\S]*?records, _, _ = await driver\.execute_query\(", sub_episodic_get_by_uuid, content, count=1)

with open("vendor/graphiti/graphiti_core/nodes.py", "w") as f:
    f.write(content)
