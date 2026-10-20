import re

def patch_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # get_relevant_nodes
    content = re.sub(
        r"if driver\.provider == GraphProvider\.KUZU:[\s\S]*?relevant_nodes_dict: dict\[str, list\[EntityNode\]\] = {",
        """results = await driver.search_operations_interface.get_relevant_nodes(
        driver, query_nodes, search_filter, group_id, min_score, limit
    )

    relevant_nodes_dict: dict[str, list[EntityNode]] = {""",
        content
    )

    # get_relevant_edges
    content = re.sub(
        r"if driver\.provider == GraphProvider\.NEPTUNE:[\s\S]*?relevant_edges_dict: dict\[str, list\[EntityEdge\]\] = {",
        """results = await driver.search_operations_interface.get_relevant_edges(
        driver, [edge.model_dump() for edge in edges], search_filter, min_score, limit
    )

    relevant_edges_dict: dict[str, list[EntityEdge]] = {""",
        content
    )

    # get_edge_invalidation_candidates
    content = re.sub(
        r"if driver\.provider == GraphProvider\.NEPTUNE:[\s\S]*?invalidation_edges_dict: dict\[str, list\[EntityEdge\]\] = {",
        """results = await driver.search_operations_interface.get_edge_invalidation_candidates(
        driver, [edge.model_dump() for edge in edges], search_filter, min_score, limit
    )

    invalidation_edges_dict: dict[str, list[EntityEdge]] = {""",
        content
    )

    with open(filepath, 'w') as f:
        f.write(content)

patch_file("vendor/graphiti/graphiti_core/search/search_utils.py")
