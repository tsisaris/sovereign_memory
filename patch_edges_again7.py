import re

with open("vendor/graphiti/graphiti_core/edges.py", "r") as f:
    content = f.read()

sub = """    @classmethod
    async def get_by_uuids(cls, driver: GraphDriver, uuids: list[str]):
        if hasattr(driver, "entity_edge_ops") and driver.entity_edge_ops is not None:
            return await driver.entity_edge_ops.get_by_uuids(driver, uuids)
"""

content = re.sub(r"    @classmethod\n    async def get_by_uuids\(cls, driver: GraphDriver, uuids: list\[str\]\):\n        if driver\.graph_operations_interface:[\s\S]*?except NotImplementedError:\s*pass", sub, content, count=1)

with open("vendor/graphiti/graphiti_core/edges.py", "w") as f:
    f.write(content)

