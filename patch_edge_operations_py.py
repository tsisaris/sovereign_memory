import re

def patch_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # filter_existing_duplicate_of_edges
    content = re.sub(
        r"if driver\.provider == GraphProvider\.NEPTUNE:[\s\S]*?# Remove duplicates that already have the IS_DUPLICATE_OF edge",
        """duplicate_node_uuids = [(src, dst) for src, dst in duplicate_nodes_map.keys()]
    records = await driver.entity_edge_ops.filter_existing_duplicate_of_edges(
        driver, duplicate_node_uuids
    )

    # Remove duplicates that already have the IS_DUPLICATE_OF edge""",
        content
    )

    with open(filepath, 'w') as f:
        f.write(content)

patch_file("vendor/graphiti/graphiti_core/utils/maintenance/edge_operations.py")
