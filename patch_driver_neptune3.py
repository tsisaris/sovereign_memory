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
    start_idx = content.find("async def clear_data")
    if start_idx != -1 and content[start_idx-4:start_idx] == "    ":
        start_idx -= 4
        
    end_idx = content.find("    async def build_indices_and_constraints")
    
    if provider == 'kuzu':
        sub_kuzu = sub.replace("['Entity', 'Episodic', 'Community', 'Saga']", "['Entity', 'Episodic', 'Community', 'Saga', 'RelatesToNode_']")
        content = content[:start_idx] + sub_kuzu + content[end_idx:]
    else:
        content = content[:start_idx] + sub + content[end_idx:]

    with open(filepath, "w") as f:
        f.write(content)
