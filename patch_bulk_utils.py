import re

with open("vendor/graphiti/graphiti_core/utils/bulk_utils.py", "r") as f:
    content = f.read()

content = content.replace("await tx.run(get_episode_node_save_bulk_query(driver.provider), episodes=episodes)", "await driver.episode_node_ops.save_bulk(driver, episodic_nodes, tx)")
content = content.replace("await tx.run(\n            get_entity_node_save_bulk_query(driver.provider, nodes),\n            nodes=nodes,\n        )", "await driver.entity_node_ops.save_bulk(driver, entity_nodes, tx)")
content = content.replace("await tx.run(\n            get_episodic_edge_save_bulk_query(driver.provider),\n            episodic_edges=[edge.model_dump() for edge in episodic_edges],\n        )", "await driver.episodic_edge_ops.save_bulk(driver, episodic_edges, tx)")
content = content.replace("await tx.run(\n            get_entity_edge_save_bulk_query(driver.provider, edges),\n            edges=edges,\n        )", "await driver.entity_edge_ops.save_bulk(driver, entity_edges, tx)")

with open("vendor/graphiti/graphiti_core/utils/bulk_utils.py", "w") as f:
    f.write(content)
