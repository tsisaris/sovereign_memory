import re

def patch_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # get_relevant_nodes starts around async def get_relevant_nodes
    # We want to replace from `if driver.provider == GraphProvider.KUZU:` to `relevant_nodes_dict: dict[str, list[EntityNode]] = {` ONLY inside get_relevant_nodes.
    # The simplest way is to find the function, slice the string and then replace.

    start_idx = content.find("async def get_relevant_nodes")
    end_idx = content.find("async def get_relevant_edges")
    
    sub = content[start_idx:end_idx]
    
    sub = re.sub(
        r"if driver\.provider == GraphProvider\.KUZU:[\s\S]*?relevant_nodes_dict: dict\[str, list\[EntityNode\]\] = {",
        """results = await driver.search_ops.get_relevant_nodes(
        driver, query_nodes, search_filter, group_id, min_score, limit
    )

    relevant_nodes_dict: dict[str, list[EntityNode]] = {""",
        sub
    )
    
    content = content[:start_idx] + sub + content[end_idx:]

    # get_relevant_edges
    start_idx = content.find("async def get_relevant_edges")
    end_idx = content.find("async def get_edge_invalidation_candidates")
    sub = content[start_idx:end_idx]

    sub = re.sub(
        r"if driver\.provider == GraphProvider\.NEPTUNE:[\s\S]*?relevant_edges_dict: dict\[str, list\[EntityEdge\]\] = {",
        """results = await driver.search_ops.get_relevant_edges(
        driver, [edge.model_dump() for edge in edges], search_filter, min_score, limit
    )

    relevant_edges_dict: dict[str, list[EntityEdge]] = {""",
        sub
    )
    
    content = content[:start_idx] + sub + content[end_idx:]

    # get_edge_invalidation_candidates
    start_idx = content.find("async def get_edge_invalidation_candidates")
    end_idx = content.find("def rrf(")
    sub = content[start_idx:end_idx]

    sub = re.sub(
        r"if driver\.provider == GraphProvider\.NEPTUNE:[\s\S]*?invalidation_edges_dict: dict\[str, list\[EntityEdge\]\] = {",
        """results = await driver.search_ops.get_edge_invalidation_candidates(
        driver, [edge.model_dump() for edge in edges], search_filter, min_score, limit
    )

    invalidation_edges_dict: dict[str, list[EntityEdge]] = {""",
        sub
    )

    content = content[:start_idx] + sub + content[end_idx:]

    with open(filepath, 'w') as f:
        f.write(content)

patch_file("vendor/graphiti/graphiti_core/search/search_utils.py")
