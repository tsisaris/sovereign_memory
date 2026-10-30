import re

with open("vendor/graphiti/graphiti_core/search/search_utils.py", "r") as f:
    content = f.read()

content = content.replace("relevant_nodes_dict: dict[str, list[EntityNode]] = {", "    relevant_nodes_dict: dict[str, list[EntityNode]] = {")
content = content.replace("relevant_edges_dict: dict[str, list[EntityEdge]] = {", "    relevant_edges_dict: dict[str, list[EntityEdge]] = {")
content = content.replace("invalidation_edges_dict: dict[str, list[EntityEdge]] = {", "    invalidation_edges_dict: dict[str, list[EntityEdge]] = {")

with open("vendor/graphiti/graphiti_core/search/search_utils.py", "w") as f:
    f.write(content)

