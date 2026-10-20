import re

with open("vendor/graphiti/graphiti_core/edges.py", "r") as f:
    content = f.read()

# Let's inspect where get_by_uuids is defined for EntityEdge specifically.
# EntityEdge inherits from Edge. Wait, does EntityEdge have its own get_by_uuids?

