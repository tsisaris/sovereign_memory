"""Persist real episode-to-entity mentions through Graphiti's maintained edge contract."""

from graphiti_core.driver.operations.episodic_edge_ops import EpisodicEdgeOperations
from graphiti_core.edges import EpisodicEdge
from graphiti_core.errors import EdgeNotFoundError, NodeNotFoundError

from .entity_node_ops import _write_transaction


class DuckDBEpisodicEdgeOps(EpisodicEdgeOperations):
    """Keep directional MENTIONS records and reject missing or wrongly typed endpoints."""

    async def save(self, executor, edge, tx=None):
        """Upsert a real mention on one transaction without overwriting another edge family."""
        if not isinstance(edge, EpisodicEdge):
            raise TypeError("Mention storage requires an EpisodicEdge")
        async with _write_transaction(executor, tx) as transaction:
            for uuid, label in (
                (edge.source_node_uuid, "Episodic"),
                (edge.target_node_uuid, "Entity"),
            ):
                nodes, _, _ = await transaction.run(
                    "SELECT uuid FROM graphiti.graph_node WHERE uuid = ? AND label = ?",
                    parameters=[uuid, label],
                )
                if not nodes:
                    raise NodeNotFoundError(uuid)
            records, _, _ = await transaction.run(
                "INSERT INTO graphiti.graph_edge "
                "(uuid, source_node_uuid, target_node_uuid, label, properties) "
                "VALUES (?, ?, ?, 'MENTIONS', ?) ON CONFLICT (uuid) DO UPDATE SET "
                "source_node_uuid = excluded.source_node_uuid, "
                "target_node_uuid = excluded.target_node_uuid, properties = excluded.properties "
                "WHERE graph_edge.label = 'MENTIONS' RETURNING uuid",
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
        """Save all mentions on the caller's transaction or one complete owned transaction."""
        async with _write_transaction(executor, tx) as transaction:
            for edge in edges:
                await self.save(executor, edge, tx=transaction)

    async def delete(self, executor, edge, tx=None):
        """Remove the selected mention while retaining its episode and entity."""
        if not isinstance(edge, EpisodicEdge):
            raise TypeError("Mention storage requires an EpisodicEdge")
        await self.delete_by_uuids(executor, [edge.uuid], tx=tx)

    async def delete_by_uuids(self, executor, uuids, tx=None, group_id=None):
        """Delete only matching mentions within an optional group restriction."""
        query = (
            "DELETE FROM graphiti.graph_edge WHERE label = 'MENTIONS' "
            "AND uuid IN (SELECT unnest(?))"
        )
        parameters = [uuids]
        if group_id is not None:
            query += " AND json_extract_string(properties, 'group_id') = ?"
            parameters.append(group_id)
        async with _write_transaction(executor, tx) as transaction:
            await transaction.run(query, parameters=parameters)

    async def get_by_uuid(self, executor, uuid):
        """Return the actual mention or Graphiti's maintained missing-edge exception."""
        edges = await self.get_by_uuids(executor, [uuid])
        if not edges:
            raise EdgeNotFoundError(uuid)
        return edges[0]

    async def get_by_uuids(self, executor, uuids):
        """Load only persisted mentions with matching UUIDs."""
        records, _, _ = await executor.execute_query(
            "SELECT properties FROM graphiti.graph_edge WHERE label = 'MENTIONS' "
            "AND uuid IN (SELECT unnest(?))",
            parameters=[uuids],
        )
        return [EpisodicEdge.model_validate_json(record["properties"]) for record in records]

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        """Read actual mentions with group filters and descending UUID pagination."""
        query = (
            "SELECT properties FROM graphiti.graph_edge WHERE label = 'MENTIONS' "
            "AND json_extract_string(properties, 'group_id') IN (SELECT unnest(?))"
        )
        parameters = [group_ids]
        if uuid_cursor:
            query += " AND uuid < ?"
            parameters.append(uuid_cursor)
        query += " ORDER BY uuid DESC"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)
        records, _, _ = await executor.execute_query(query, parameters=parameters)
        return [EpisodicEdge.model_validate_json(record["properties"]) for record in records]
