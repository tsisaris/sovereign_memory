import re

with open("vendor/graphiti/graphiti_core/edges.py", "r") as f:
    content = f.read()

# I accidentally deleted the whole class definition. Let's checkout and try again.
