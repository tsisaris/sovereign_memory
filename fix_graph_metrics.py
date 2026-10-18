import re

filepath = "packages/cognee_duckdb/cognee_community_hybrid_adapter_duckdb/duckdb_adapter.py"
with open(filepath, "r") as f:
    content = f.read()

metrics_rep = """    async def get_graph_metrics(self, include_optional: bool = False) -> dict[str, Any]:
        \"\"\"Documentation.\"\"\"
        nodes_raw = await self._execute_query("SELECT id FROM graph_node")
        edges_raw = await self._execute_query("SELECT source_id, target_id FROM graph_edge")
        node_count = len(nodes_raw)
        edge_count = len(edges_raw)

        from collections import defaultdict
        graph = defaultdict(list)
        for e in edges_raw:
            graph[e[0]].append(e[1])
            graph[e[1]].append(e[0])

        visited = set()
        components = 0
        for node in nodes_raw:
            nid = node[0]
            if nid not in visited:
                components += 1
                queue = [nid]
                while queue:
                    curr = queue.pop(0)
                    if curr not in visited:
                        visited.add(curr)
                        queue.extend(graph[curr])

        density = (edge_count / (node_count * (node_count - 1))) if node_count > 1 else 0.0
        average_degree = (2 * edge_count / node_count) if node_count > 0 else 0.0

        return {
            "node_count": node_count,
            "edge_count": edge_count,
            "density": density,
            "number_of_connected_components": components,
            "average_degree": average_degree,
        }
"""
content = re.sub(
    r"    async def get_graph_metrics\(self, include_optional: bool = False\) -> dict\[str, Any\]:.*?        \}",
    metrics_rep.strip(),
    content,
    flags=re.DOTALL,
)

with open(filepath, "w") as f:
    f.write(content)
