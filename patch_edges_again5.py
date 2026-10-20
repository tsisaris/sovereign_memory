import re

with open("vendor/graphiti/graphiti_core/edges.py", "r") as f:
    content = f.read()

# Make sure EntityEdge get_by_uuids correctly dispatches to entity_edge_ops
# Let's inspect where it's at right now

with open("temp.py", "w") as f:
    f.write(content)

