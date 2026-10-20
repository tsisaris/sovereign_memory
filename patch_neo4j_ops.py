import os

# Neo4j implementations

neo4j_saga_ops = """
    async def get_or_create_saga(
        self,
        executor: QueryExecutor,
        saga_name: str,
        group_id: str,
        created_at: Any,
    ) -> SagaNode | None:
        records, _, _ = await executor.execute_query(
            \"\"\"
            MATCH (s:Saga {name: $name, group_id: $group_id})
            RETURN s.uuid AS uuid, s.name AS name, s.group_id AS group_id, s.created_at AS created_at,
                   s.first_episode_uuid AS first_episode_uuid, s.last_episode_uuid AS last_episode_uuid
            \"\"\",
            name=saga_name,
            group_id=group_id,
            routing_='r',
        )

        from graphiti_core.helpers import parse_db_date
        if records:
            record = records[0]
            return SagaNode(
                uuid=record['uuid'],
                name=record['name'],
                group_id=record['group_id'],
                created_at=parse_db_date(record['created_at']),
                first_episode_uuid=record['first_episode_uuid'],
                last_episode_uuid=record['last_episode_uuid'],
            )
        saga = SagaNode(name=saga_name, group_id=group_id, created_at=created_at)
        await self.save(executor, saga)
        return saga

    async def saga_get_previous_episode_uuid(
        self,
        executor: QueryExecutor,
        saga_uuid: str,
        current_episode_uuid: str,
    ) -> str | None:
        records, _, _ = await executor.execute_query(
            \"\"\"
            MATCH (s:Saga {uuid: $saga_uuid})-[:HAS_EPISODE]->(e:Episodic)
            WHERE e.uuid <> $current_episode_uuid
            RETURN e.uuid AS uuid
            ORDER BY e.created_at DESC
            LIMIT 1
            \"\"\",
            saga_uuid=saga_uuid,
            current_episode_uuid=current_episode_uuid,
            routing_='r',
        )
        if records:
            return records[0]['uuid']
        return None

    async def summarize_saga_episodes(
        self,
        executor: QueryExecutor,
        saga_id: str,
        since: Any,
        limit: int,
    ) -> list[tuple[str, Any, Any]]:
        if since is not None:
            records, _, _ = await executor.execute_query(
                \"\"\"
                MATCH (s:Saga {uuid: $saga_uuid})-[:HAS_EPISODE]->(e:Episodic)
                WHERE e.created_at > $since
                RETURN e.content AS content, e.valid_at AS valid_at, e.created_at AS created_at
                ORDER BY e.created_at ASC, e.valid_at ASC
                LIMIT $limit
                \"\"\",
                saga_uuid=saga_id,
                since=since,
                limit=limit,
                routing_='r',
            )
        else:
            records, _, _ = await executor.execute_query(
                \"\"\"
                MATCH (s:Saga {uuid: $saga_uuid})-[:HAS_EPISODE]->(e:Episodic)
                WHERE e.created_at IS NOT NULL
                RETURN e.content AS content, e.valid_at AS valid_at, e.created_at AS created_at
                ORDER BY e.created_at ASC, e.valid_at ASC
                LIMIT $limit
                \"\"\",
                saga_uuid=saga_id,
                limit=limit,
                routing_='r',
            )
        from graphiti_core.helpers import parse_db_date
        episodes_data = []
        for r in records:
            content = r.get('content')
            created_at = parse_db_date(r.get('created_at'))
            if content and created_at is not None:
                episodes_data.append((content, parse_db_date(r.get('valid_at')), created_at))
        return episodes_data
"""

neo4j_episode_ops = """
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
        WITH e, collect(CASE WHEN episode_count = 1 THEN n END) AS nodes_to_delete
        OPTIONAL MATCH ()-[r:RELATES_TO]->() WHERE $episode_uuid IN r.episodes
        WITH e, nodes_to_delete, collect(r) AS edges_to_delete
        FOREACH (edge IN edges_to_delete | DELETE edge)
        FOREACH (node IN nodes_to_delete | DETACH DELETE node)
        DETACH DELETE e
        \"\"\"
        await executor.execute_query(query, episode_uuid=episode_uuid)
"""

def append_to_class(file_path, methods_str):
    with open(file_path, 'r') as f:
        content = f.read()
    with open(file_path, 'w') as f:
        f.write(content.rstrip() + "\n" + methods_str + "\n")

import glob
for provider in ['neo4j', 'kuzu', 'falkordb', 'neptune']:
    base = f"vendor/graphiti/graphiti_core/driver/{provider}/operations"
    if not os.path.exists(base): continue
    
    saga_path = f"{base}/saga_node_ops.py"
    if os.path.exists(saga_path):
        append_to_class(saga_path, neo4j_saga_ops)
        
    epi_path = f"{base}/episode_node_ops.py"
    if os.path.exists(epi_path):
        append_to_class(epi_path, neo4j_episode_ops)

