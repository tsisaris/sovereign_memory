import re
import os

neo4j_episode_ops_kuzu = """
    async def remove_episode_and_singletons(
        self,
        executor: QueryExecutor,
        episode_uuid: str,
    ) -> None:
        query = \"\"\"
        MATCH (e:Episodic {uuid: $episode_uuid})
        OPTIONAL MATCH (e)-[:MENTIONS]->(n:Entity)
        WITH e, n
        OPTIONAL MATCH (e2:Episodic)-[:MENTIONS]->(n)
        WITH e, n, count(e2) AS episode_count
        WHERE episode_count = 1
        DETACH DELETE n
        \"\"\"
        await executor.execute_query(query, episode_uuid=episode_uuid)
        
        query2 = \"\"\"
        MATCH ()-[:RELATES_TO]->(r:RelatesToNode_)-[:RELATES_TO]->() WHERE list_contains(r.episodes, $episode_uuid)
        DETACH DELETE r
        \"\"\"
        await executor.execute_query(query2, episode_uuid=episode_uuid)

        query4 = \"\"\"
        MATCH (e:Episodic {uuid: $episode_uuid})-[r:MENTIONS]->()
        DELETE r
        \"\"\"
        await executor.execute_query(query4, episode_uuid=episode_uuid)
        
        query3 = \"\"\"
        MATCH (e:Episodic {uuid: $episode_uuid})
        DETACH DELETE e
        \"\"\"
        await executor.execute_query(query3, episode_uuid=episode_uuid)

"""

def replace_in_class(file_path, methods_str):
    with open(file_path, 'r') as f:
        content = f.read()
    
    start = content.find("async def remove_episode_and_singletons")
    if start != -1:
        content = content[:start] + methods_str + "\n"
        with open(file_path, 'w') as f:
            f.write(content)

for provider in ['kuzu']:
    base = f"vendor/graphiti/graphiti_core/driver/{provider}/operations"
    epi_path = f"{base}/episode_node_ops.py"
    if os.path.exists(epi_path):
        replace_in_class(epi_path, neo4j_episode_ops_kuzu)

