import re

with open("vendor/graphiti/graphiti_core/driver/neptune/operations/graph_ops.py", "r") as f:
    content = f.read()

sub = """
    async def clear_data(
        self,
        executor: QueryExecutor,
        group_ids: list[str] | None = None,
    ) -> None:
        if group_ids is None:
            await executor.execute_query('MATCH (n) DETACH DELETE n')
        else:
            labels = ['Entity', 'Episodic', 'Community', 'Saga']
            for label in labels:
                await executor.execute_query(
                    f\"\"\"
                    MATCH (n:{label})
                    WHERE n.group_id IN $group_ids
                    DETACH DELETE n
                    \"\"\",
                    group_ids=group_ids,
                )
"""

start_idx = content.find("async def clear_data")
end_idx = content.find("async def build_indices_and_constraints")

content = content[:start_idx] + sub + content[end_idx:]

with open("vendor/graphiti/graphiti_core/driver/neptune/operations/graph_ops.py", "w") as f:
    f.write(content)

with open("vendor/graphiti/graphiti_core/driver/falkordb/operations/graph_ops.py", "r") as f:
    content = f.read()
start_idx = content.find("async def clear_data")
end_idx = content.find("async def build_indices_and_constraints")
content = content[:start_idx] + sub + content[end_idx:]
with open("vendor/graphiti/graphiti_core/driver/falkordb/operations/graph_ops.py", "w") as f:
    f.write(content)

with open("vendor/graphiti/graphiti_core/driver/neo4j/operations/graph_ops.py", "r") as f:
    content = f.read()
start_idx = content.find("async def clear_data")
end_idx = content.find("async def build_indices_and_constraints")
content = content[:start_idx] + sub + content[end_idx:]
with open("vendor/graphiti/graphiti_core/driver/neo4j/operations/graph_ops.py", "w") as f:
    f.write(content)


