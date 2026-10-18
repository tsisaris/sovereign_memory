"""Pure graph-shape helpers shared by the SQL graph adapters.

These functions work on plain node ids and (source, target) pairs that an
adapter has already read; they never open a connection or touch storage.
They were first written for the Postgres demo adapter and are shared so the
DuckDB hybrid adapter computes the same metrics and nodeset neighbours.
"""


def _component_sizes(node_ids: list[str], edge_pairs: list[tuple[str, str]]) -> list[int]:
    """Return the sizes of undirected connected components, largest first."""
    neighbors: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for source_id, target_id in edge_pairs:
        neighbors.setdefault(source_id, set()).add(target_id)
        neighbors.setdefault(target_id, set()).add(source_id)

    unseen = set(node_ids)
    sizes = []
    while unseen:
        pending = [unseen.pop()]
        size = 0
        while pending:
            node_id = pending.pop()
            size += 1
            new_neighbors = neighbors.get(node_id, set()) & unseen
            unseen.difference_update(new_neighbors)
            pending.extend(new_neighbors)
        sizes.append(size)

    return sorted(sizes, reverse=True)


def _select_nodeset_neighbor_ids(
    primary_ids: set[str], edge_pairs: list[tuple[str, str]], operator: str
) -> set[str]:
    """Select neighbors connected to any or every primary node."""
    if operator not in {"OR", "AND"}:
        raise ValueError("node_name_filter_operator must be 'OR' or 'AND'")
    if not primary_ids:
        return set()

    connected_primaries: dict[str, set[str]] = {}
    for source_id, target_id in edge_pairs:
        if source_id in primary_ids:
            connected_primaries.setdefault(target_id, set()).add(source_id)
        if target_id in primary_ids:
            connected_primaries.setdefault(source_id, set()).add(target_id)

    if operator == "OR":
        return set(connected_primaries)
    return {
        node_id
        for node_id, connections in connected_primaries.items()
        if connections >= primary_ids
    }
