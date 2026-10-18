import re

filepath = "packages/cognee_duckdb/cognee_community_hybrid_adapter_duckdb/duckdb_adapter.py"
with open(filepath, "r") as f:
    content = f.read()

replacement = """    async def remove_belongs_to_set_tags(self, tags: list[str], node_ids: list[str] | None = None) -> None:
        \"\"\"Remove tags from belongs_to_set property array.\"\"\"
        if not tags:
            return
        if node_ids is not None and not node_ids:
            return

        tags_to_remove = set(tags)
        if node_ids is None:
            q = "SELECT id, properties FROM graph_node"
            res = await self._execute_query(q)
        else:
            p1 = ", ".join([f"${i + 1}" for i in range(len(node_ids))])
            q = f"SELECT id, properties FROM graph_node WHERE id IN ({p1})"
            res = await self._execute_query(q, node_ids)

        queries = []
        for r in res:
            props = json.loads(r[1]) if isinstance(r[1], str) else r[1]
            if not props or "belongs_to_set" not in props:
                continue
            belongs_to = props["belongs_to_set"]
            if isinstance(belongs_to, list):
                remaining = [t for t in belongs_to if t not in tags_to_remove]
                if len(remaining) != len(belongs_to):
                    props["belongs_to_set"] = remaining
                    props_json = json.dumps(serialize_for_json(props))
                    queries.append(("UPDATE graph_node SET properties = $1 WHERE id = $2", [props_json, r[0]]))

        if queries:
            await self._execute_transaction(queries)
"""

content = re.sub(
    r"    async def remove_belongs_to_set_tags\(self, tags: list\[str\], node_ids: list\[str\]\) -> None:.*?pass",
    replacement.strip(),
    content,
    flags=re.DOTALL,
)

with open(filepath, "w") as f:
    f.write(content)
