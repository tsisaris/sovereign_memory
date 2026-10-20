import re

sub = """    async def clear_data(
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

for provider in ['neptune', 'falkordb', 'neo4j', 'kuzu']:
    filepath = f"vendor/graphiti/graphiti_core/driver/{provider}/operations/graph_ops.py"
    with open(filepath, "r") as f:
        content = f.read()

    # The previous patch missed the fact that start_idx matched `async def clear_data(` not `    async def clear_data(` 
    content = content.replace("        async def clear_data(", "    async def clear_data(")

    with open(filepath, "w") as f:
        f.write(content)
