import re

with open("vendor/graphiti/graphiti_core/nodes.py", "r") as f:
    content = f.read()

# Replace graph_operations_interface checks with entity_node_ops and similar
content = content.replace("await driver.graph_operations_interface.episodic_node_get_by_uuid(\n                    cls, driver, uuid\n                )", "await driver.episode_node_ops.get_by_uuid(driver, uuid)")

content = content.replace("await driver.graph_operations_interface.node_get_by_uuid(cls, driver, uuid)", "await driver.entity_node_ops.get_by_uuid(driver, uuid)")

with open("vendor/graphiti/graphiti_core/nodes.py", "w") as f:
    f.write(content)
