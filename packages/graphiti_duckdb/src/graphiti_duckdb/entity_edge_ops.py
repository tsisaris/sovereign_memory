"""Persist maintained Graphiti entity edges with native, transaction-bound SQL."""

from graphiti_core.driver.operations.entity_edge_ops import EntityEdgeOperations
from graphiti_core.edges import EntityEdge
from graphiti_core.errors import EdgeNotFoundError, NodeNotFoundError

from .entity_node_ops import _write_transaction


class DuckDBEntityEdgeOps(EntityEdgeOperations):
    """Store real directional relationships without inventing missing records or embeddings."""

    async def save(self, executor, edge, tx=None):
        """Upsert an entity relationship, rejecting UUIDs owned by another edge family."""
        if not isinstance(edge, EntityEdge):
            raise TypeError("Entity edge storage requires an EntityEdge")
        async with _write_transaction(executor, tx) as transaction:
            endpoints = {edge.source_node_uuid, edge.target_node_uuid}
            nodes, _, _ = await transaction.run(
                "SELECT uuid FROM graphiti.graph_node WHERE label = 'Entity' "
                "AND uuid IN (SELECT unnest(?))",
                parameters=[sorted(endpoints)],
            )
            missing = endpoints - {node["uuid"] for node in nodes}
            if missing:
                raise NodeNotFoundError(sorted(missing)[0])
            records, _, _ = await transaction.run(
                "INSERT INTO graphiti.graph_edge "
                "(uuid, source_node_uuid, target_node_uuid, label, properties) "
                "VALUES (?, ?, ?, 'RELATES_TO', ?) ON CONFLICT (uuid) DO UPDATE SET "
                "source_node_uuid = excluded.source_node_uuid, "
                "target_node_uuid = excluded.target_node_uuid, properties = excluded.properties "
                "WHERE graph_edge.label = 'RELATES_TO' RETURNING uuid",
                parameters=[
                    edge.uuid,
                    edge.source_node_uuid,
                    edge.target_node_uuid,
                    edge.model_dump_json(),
                ],
            )
            if not records:
                raise ValueError(f"Edge UUID {edge.uuid} belongs to a different edge family")

    async def save_bulk(self, executor, edges, tx=None, batch_size=100):
        """Save every supplied edge on one owned or unchanged caller transaction."""
        async with _write_transaction(executor, tx) as transaction:
            for edge in edges:
                await self.save(executor, edge, tx=transaction)

    async def delete(self, executor, edge, tx=None):
        """Delete this entity relationship without removing either endpoint."""
        if not isinstance(edge, EntityEdge):
            raise TypeError("Entity edge storage requires an EntityEdge")
        await self.delete_by_uuids(executor, [edge.uuid], tx=tx)

    async def delete_by_uuids(self, executor, uuids, tx=None, group_id=None):
        """Delete only matching relationship UUIDs with an optional group restriction."""
        query = (
            "DELETE FROM graphiti.graph_edge WHERE label = 'RELATES_TO' "
            "AND uuid IN (SELECT unnest(?))"
        )
        parameters = [uuids]
        if group_id is not None:
            query += " AND json_extract_string(properties, 'group_id') = ?"
            parameters.append(group_id)
        async with _write_transaction(executor, tx) as transaction:
            await transaction.run(query, parameters=parameters)

    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100):
        """Remove entity relationships belonging to exactly the requested group."""
        async with _write_transaction(executor, tx) as transaction:
            await transaction.run(
                "DELETE FROM graphiti.graph_edge WHERE label = 'RELATES_TO' "
                "AND json_extract_string(properties, 'group_id') = ?",
                parameters=[group_id],
            )

    async def _read(self, executor, predicate, parameters, limit=None, uuid_cursor=None):
        """Load actual entity relationships with bound filtering and UUID pagination."""
        query = "SELECT properties FROM graphiti.graph_edge WHERE label = 'RELATES_TO' AND ("
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
        return [EntityEdge.model_validate_json(record["properties"]) for record in records]

    async def get_by_uuid(self, executor, uuid):
        """Return the persisted relationship or Graphiti's actual missing-edge exception."""
        edges = await self.get_by_uuids(executor, [uuid])
        if not edges:
            raise EdgeNotFoundError(uuid)
        return edges[0]

    async def get_by_uuids(self, executor, uuids):
        """Read only matching entity relationship UUIDs."""
        return await self._read(executor, "uuid IN (SELECT unnest(?))", [uuids])

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        """Preserve requested groups, descending UUID pagination and the caller's limit."""
        return await self._read(
            executor,
            "json_extract_string(properties, 'group_id') IN (SELECT unnest(?))",
            [group_ids],
            limit=limit,
            uuid_cursor=uuid_cursor,
        )

    async def get_between_nodes(self, executor, source_node_uuid, target_node_uuid):
        """Return only relationships in the explicitly requested source-to-target direction."""
        return await self._read(
            executor,
            "source_node_uuid = ? AND target_node_uuid = ?",
            [source_node_uuid, target_node_uuid],
        )

    async def get_by_node_uuid(self, executor, node_uuid):
        """Read incident relationships while preserving their stored endpoint directions."""
        return await self._read(
            executor,
            "source_node_uuid = ? OR target_node_uuid = ?",
            [node_uuid, node_uuid],
        )

    async def get_by_source_target(self, executor, source_node_uuid, target_node_uuid):
        """Use the same directional relationship lookup for the typed operation caller."""
        return await self.get_between_nodes(executor, source_node_uuid, target_node_uuid)

    async def get_by_source(self, executor, source_node_uuid):
        """Read relationships originating at the supplied entity UUID."""
        return await self._read(executor, "source_node_uuid = ?", [source_node_uuid])

    async def get_by_target(self, executor, target_node_uuid):
        """Read relationships terminating at the supplied entity UUID."""
        return await self._read(executor, "target_node_uuid = ?", [target_node_uuid])

    async def load_embeddings(self, executor, edge):
        """Load the persisted fact embedding without invoking an embedding model."""
        edge.fact_embedding = (await self.get_by_uuid(executor, edge.uuid)).fact_embedding

    async def load_embeddings_bulk(self, executor, edges, batch_size=100):
        """Populate existing relationships from their actual stored embeddings."""
        await self.load_embedding_map(executor, edges)

    async def load_embedding_map(self, executor, edges):
        """Return only persisted embeddings and update matching requested edge objects."""
        stored = await self.get_by_uuids(executor, [edge.uuid for edge in edges])
        embeddings = {edge.uuid: edge.fact_embedding for edge in stored}
        for edge in edges:
            if edge.uuid in embeddings:
                edge.fact_embedding = embeddings[edge.uuid]
        return embeddings
