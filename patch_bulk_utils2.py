import re

with open("vendor/graphiti/graphiti_core/utils/bulk_utils.py", "r") as f:
    content = f.read()

content = content.replace("await tx.run(\n            get_entity_edge_save_bulk_query(driver.provider),\n            entity_edges=edges,\n        )", "await driver.entity_edge_ops.save_bulk(driver, entity_edges, tx)")

with open("vendor/graphiti/graphiti_core/utils/bulk_utils.py", "w") as f:
    f.write(content)
