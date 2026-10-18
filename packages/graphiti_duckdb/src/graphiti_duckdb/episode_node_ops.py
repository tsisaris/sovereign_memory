"""Persist maintained episodic models and retrieve their actual temporal context."""

from graphiti_core.driver.operations.episode_node_ops import EpisodeNodeOperations
from graphiti_core.errors import NodeNotFoundError
from graphiti_core.nodes import EpisodeType, EpisodicNode

from .entity_node_ops import _write_transaction


class DuckDBEpisodeNodeOps(EpisodeNodeOperations):
    """Keep episodes, their groups and their provenance inside the Graphiti schema."""

    async def save(self, executor, node, tx=None):
        """Upsert an actual episode without overwriting another node family."""
        if not isinstance(node, EpisodicNode):
            raise TypeError("Episode storage requires an EpisodicNode")
        async with _write_transaction(executor, tx) as transaction:
            records, _, _ = await transaction.run(
                "INSERT INTO graphiti.graph_node (uuid, label, properties) "
                "VALUES (?, 'Episodic', ?) ON CONFLICT (uuid) DO UPDATE "
                "SET properties = excluded.properties WHERE graph_node.label = 'Episodic' "
                "RETURNING uuid",
                parameters=[node.uuid, node.model_dump_json()],
            )
            if not records:
                raise ValueError(f"Node UUID {node.uuid} belongs to a different node family")

    async def save_bulk(self, executor, nodes, tx=None, batch_size=100):
        """Persist all episodes on one owned or unchanged caller transaction."""
        async with _write_transaction(executor, tx) as transaction:
            for node in nodes:
                await self.save(executor, node, tx=transaction)

    async def _delete_matching(self, executor, predicate, parameters, tx):
        """Detach only selected episodes and their incident edges atomically."""
        selected = "SELECT uuid FROM graphiti.graph_node WHERE label = 'Episodic' AND "
        selected += predicate
        async with _write_transaction(executor, tx) as transaction:
            await transaction.run(
                "DELETE FROM graphiti.graph_edge WHERE source_node_uuid IN ("
                + selected
                + ") OR target_node_uuid IN ("
                + selected
                + ")",
                parameters=parameters + parameters,
            )
            await transaction.run(
                "DELETE FROM graphiti.graph_node WHERE label = 'Episodic' AND " + predicate,
                parameters=parameters,
            )

    async def delete(self, executor, node, tx=None):
        """Delete the episode and its incident edges while retaining entity nodes."""
        if not isinstance(node, EpisodicNode):
            raise TypeError("Episode storage requires an EpisodicNode")
        await self._delete_matching(executor, "uuid = ?", [node.uuid], tx)

    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100):
        """Delete episodes belonging to exactly the supplied group."""
        await self._delete_matching(
            executor, "json_extract_string(properties, 'group_id') = ?", [group_id], tx
        )

    async def delete_by_uuids(self, executor, uuids, tx=None, batch_size=100, group_id=None):
        """Delete requested episode UUIDs with an optional explicit group restriction."""
        predicate = "uuid IN (SELECT unnest(?))"
        parameters = [uuids]
        if group_id is not None:
            predicate += " AND json_extract_string(properties, 'group_id') = ?"
            parameters.append(group_id)
        await self._delete_matching(executor, predicate, parameters, tx)

    async def _read(self, executor, predicate, parameters, limit=None, uuid_cursor=None):
        """Read persisted episodes with bound filtering and descending UUID pagination."""
        query = "SELECT properties FROM graphiti.graph_node WHERE label = 'Episodic' AND ("
        query += predicate + ")"
        parameters = list(parameters)
        if uuid_cursor:
            query += " AND uuid < ?"
            parameters.append(uuid_cursor)
        query += " ORDER BY uuid DESC"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)
        records, _, _ = await executor.execute_query(query, parameters=parameters)
        return [EpisodicNode.model_validate_json(record["properties"]) for record in records]

    async def get_by_uuid(self, executor, uuid):
        """Return the actual persisted episode or its maintained missing-node error."""
        nodes = await self.get_by_uuids(executor, [uuid])
        if not nodes:
            raise NodeNotFoundError(uuid)
        return nodes[0]

    async def get_by_uuids(self, executor, uuids):
        """Read only episodes with one of the requested UUIDs."""
        return await self._read(executor, "uuid IN (SELECT unnest(?))", [uuids])

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        """Preserve group restrictions and the public pagination contract."""
        return await self._read(
            executor,
            "json_extract_string(properties, 'group_id') IN (SELECT unnest(?))",
            [group_ids],
            limit=limit,
            uuid_cursor=uuid_cursor,
        )

    async def get_by_entity_node_uuid(self, executor, entity_node_uuid):
        """Find actual episodes with a persisted outgoing MENTIONS edge to the entity."""
        return await self._read(
            executor,
            "uuid IN (SELECT source_node_uuid FROM graphiti.graph_edge "
            "WHERE label = 'MENTIONS' AND target_node_uuid = ?)",
            [entity_node_uuid],
        )

    async def retrieve_episodes(
        self, executor, reference_time, last_n=3, group_ids=None, source=None, saga=None
    ):
        """Return the latest eligible episodes using actual time, group, source and saga filters."""
        query = (
            "SELECT properties FROM graphiti.graph_node WHERE label = 'Episodic' "
            "AND CAST(json_extract_string(properties, 'valid_at') AS TIMESTAMPTZ) <= ?"
        )
        parameters = [reference_time]
        if saga is not None:
            query += (
                " AND uuid IN (SELECT e.target_node_uuid FROM graphiti.graph_edge e "
                "JOIN graphiti.graph_node s ON s.uuid = e.source_node_uuid "
                "WHERE e.label = 'HAS_EPISODE' AND s.label = 'Saga' "
                "AND json_extract_string(s.properties, 'name') = ? "
                "AND json_extract_string(s.properties, 'group_id') = ?)"
            )
            parameters.extend([saga, group_ids[0] if group_ids else None])
        elif group_ids:
            query += " AND json_extract_string(properties, 'group_id') IN (SELECT unnest(?))"
            parameters.append(group_ids)
        if source is not None:
            query += " AND json_extract_string(properties, 'source') = ?"
            parameters.append(source.value if isinstance(source, EpisodeType) else source)
        query += (
            " ORDER BY CAST(json_extract_string(properties, 'valid_at') AS TIMESTAMPTZ) DESC "
            "LIMIT ?"
        )
        parameters.append(last_n)
        records, _, _ = await executor.execute_query(query, parameters=parameters)
        return [EpisodicNode.model_validate_json(record["properties"]) for record in records]

    async def remove_episode_and_singletons(self, executor, episode_uuid, tx=None):
        """Atomically apply maintained creation-provenance and singleton removal rules."""
        async with _write_transaction(executor, tx) as transaction:
            episodes, _, _ = await transaction.run(
                "SELECT properties FROM graphiti.graph_node WHERE label = 'Episodic' AND uuid = ?",
                parameters=[episode_uuid],
            )
            if not episodes:
                raise NodeNotFoundError(episode_uuid)
            episode = EpisodicNode.model_validate_json(episodes[0]["properties"])
            # Creation is the FIRST provenance entry, not mere membership in the array.
            await transaction.run(
                "DELETE FROM graphiti.graph_edge WHERE label = 'RELATES_TO' "
                "AND uuid IN (SELECT unnest(?)) "
                "AND json_extract_string(properties, 'group_id') = ? "
                "AND json_extract_string(properties, '$.episodes[0]') = ?",
                parameters=[episode.entity_edges, episode.group_id, episode.uuid],
            )
            nodes, _, _ = await transaction.run(
                "SELECT n.uuid FROM graphiti.graph_node n WHERE n.label = 'Entity' "
                "AND json_extract_string(n.properties, 'group_id') = ? "
                "AND n.uuid IN (SELECT target_node_uuid FROM graphiti.graph_edge "
                "WHERE label = 'MENTIONS' AND source_node_uuid = ?) "
                "AND (SELECT count(*) FROM graphiti.graph_edge e "
                "JOIN graphiti.graph_node p ON p.uuid = e.source_node_uuid "
                "WHERE e.label = 'MENTIONS' AND p.label = 'Episodic' "
                "AND e.target_node_uuid = n.uuid) = 1",
                parameters=[episode.group_id, episode.uuid],
            )
            selected = [node["uuid"] for node in nodes]
            await executor.entity_node_ops.delete_by_uuids(
                executor, selected, tx=transaction, group_id=episode.group_id
            )
            await self.delete(executor, episode, tx=transaction)
