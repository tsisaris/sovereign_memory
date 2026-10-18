"""Maintain the Graphiti schema and answer its community and mention questions natively.

Everything stays inside the ``graphiti`` schema of the shared DuckDB file, on the caller's
transaction when it is borrowed. Community detection reuses Graphiti's own
label-propagation function; only the neighbour counts come from SQL here.
"""

from collections import Counter

from graphiti_core.driver.operations.graph_ops import GraphMaintenanceOperations
from graphiti_core.nodes import CommunityNode, EntityNode
from graphiti_core.utils.maintenance.community_operations import Neighbor, label_propagation

from .entity_node_ops import _write_transaction

INDEX_QUERIES = (
    "CREATE INDEX IF NOT EXISTS graph_node_label_idx ON graphiti.graph_node(label)",
    "CREATE INDEX IF NOT EXISTS graph_edge_source_idx ON graphiti.graph_edge(source_node_uuid)",
    "CREATE INDEX IF NOT EXISTS graph_edge_target_idx ON graphiti.graph_edge(target_node_uuid)",
)


class DuckDBGraphOps(GraphMaintenanceOperations):
    """Clear, index and analyse Graphiti data without touching other engines' schemas."""

    async def clear_data(self, executor, group_ids=None):
        """Delete selected Graphiti groups and incident edges without touching other engines."""
        async with executor.transaction() as transaction:
            if group_ids is None:
                await transaction.run("DELETE FROM graphiti.graph_edge")
                await transaction.run("DELETE FROM graphiti.graph_node")
            else:
                selected = (
                    "SELECT uuid FROM graphiti.graph_node "
                    "WHERE json_extract_string(properties, 'group_id') IN (SELECT unnest(?))"
                )
                await transaction.run(
                    "DELETE FROM graphiti.graph_edge "
                    "WHERE json_extract_string(properties, 'group_id') IN (SELECT unnest(?)) "
                    "OR source_node_uuid IN (" + selected + ") "
                    "OR target_node_uuid IN (" + selected + ")",
                    parameters=[group_ids, group_ids, group_ids],
                )
                await transaction.run(
                    "DELETE FROM graphiti.graph_node "
                    "WHERE json_extract_string(properties, 'group_id') IN (SELECT unnest(?))",
                    parameters=[group_ids],
                )
            await transaction.run("DELETE FROM graphiti.graph_fts_generations")

    async def build_indices_and_constraints(self, executor, delete_existing=False):
        """Create the native lookup indexes; first drop Graphiti's own when asked."""
        if delete_existing:
            await self.delete_all_indexes(executor)
        async with executor.transaction() as transaction:
            for query in INDEX_QUERIES:
                await transaction.run(query)

    async def delete_all_indexes(self, executor):
        """Drop only explicit indexes inside the ``graphiti`` schema of the current catalog."""
        records, _, _ = await executor.execute_query(
            "SELECT index_name FROM duckdb_indexes() "
            "WHERE database_name = current_database() AND schema_name = 'graphiti'"
        )
        async with executor.transaction() as transaction:
            for record in records:
                name = record["index_name"].replace('"', '""')
                await transaction.run(f'DROP INDEX graphiti."{name}"')

    async def get_community_clusters(self, executor, group_ids=None):
        """Return entity clusters per group from Graphiti's label propagation.

        Neighbour weights count RELATES_TO edges in either direction between entities of
        the same group, exactly as the maintained projection query does. With no groups
        given, every group holding entities is clustered. Isolated entities form their
        own cluster.
        """
        if group_ids is None:
            records, _, _ = await executor.execute_query(
                "SELECT DISTINCT json_extract_string(properties, 'group_id') AS group_id "
                "FROM graphiti.graph_node WHERE label = 'Entity' "
                "AND json_extract_string(properties, 'group_id') IS NOT NULL ORDER BY group_id"
            )
            group_ids = [record["group_id"] for record in records]
        clusters = []
        for group_id in group_ids:
            nodes = await executor.entity_node_ops.get_by_group_ids(executor, [group_id])
            records, _, _ = await executor.execute_query(
                "SELECT pair.n AS node_uuid, pair.m AS neighbor_uuid, count(*) AS edge_count FROM ("
                "SELECT source_node_uuid AS n, target_node_uuid AS m FROM graphiti.graph_edge "
                "WHERE label = 'RELATES_TO' UNION ALL "
                "SELECT target_node_uuid AS n, source_node_uuid AS m FROM graphiti.graph_edge "
                "WHERE label = 'RELATES_TO') pair "
                "JOIN graphiti.graph_node a ON a.uuid = pair.n AND a.label = 'Entity' "
                "JOIN graphiti.graph_node b ON b.uuid = pair.m AND b.label = 'Entity' "
                "WHERE json_extract_string(a.properties, 'group_id') = ? "
                "AND json_extract_string(b.properties, 'group_id') = ? "
                "GROUP BY pair.n, pair.m ORDER BY pair.n, pair.m",
                parameters=[group_id, group_id],
            )
            projection = {node.uuid: [] for node in nodes}
            for record in records:
                projection[record["node_uuid"]].append(
                    Neighbor(node_uuid=record["neighbor_uuid"], edge_count=record["edge_count"])
                )
            by_uuid = {node.uuid: node for node in nodes}
            for cluster in label_propagation(projection):
                clusters.append([by_uuid[uuid] for uuid in cluster])
        return clusters

    async def remove_communities(self, executor, group_ids=None):
        """Delete communities, optionally only in the given groups, with their edges."""
        ops = executor.community_node_ops
        if group_ids:
            async with _write_transaction(executor, None) as transaction:
                for group_id in group_ids:
                    await ops.delete_by_group_id(executor, group_id, tx=transaction)
        else:
            await ops.delete_all(executor)

    async def determine_entity_community(self, executor, entity):
        """Return ``(community, is_new)`` for an entity using Graphiti's maintained rule.

        An existing HAS_MEMBER membership wins and is not new. Otherwise the community
        most often held by the entity's RELATES_TO neighbours is proposed as new. With
        neither, ``(None, False)``.
        """
        records, _, _ = await executor.execute_query(
            "SELECT c.properties FROM graphiti.graph_edge e "
            "JOIN graphiti.graph_node c ON c.uuid = e.source_node_uuid AND c.label = 'Community' "
            "WHERE e.label = 'HAS_MEMBER' AND e.target_node_uuid = ? ORDER BY c.uuid LIMIT 1",
            parameters=[entity.uuid],
        )
        if records:
            return CommunityNode.model_validate_json(records[0]["properties"]), False
        records, _, _ = await executor.execute_query(
            "SELECT c.properties FROM graphiti.graph_edge r "
            "JOIN graphiti.graph_node m ON m.label = 'Entity' AND m.uuid = CASE "
            "WHEN r.source_node_uuid = ? THEN r.target_node_uuid ELSE r.source_node_uuid END "
            "JOIN graphiti.graph_edge h ON h.label = 'HAS_MEMBER' AND h.target_node_uuid = m.uuid "
            "JOIN graphiti.graph_node c ON c.uuid = h.source_node_uuid AND c.label = 'Community' "
            "WHERE r.label = 'RELATES_TO' AND (r.source_node_uuid = ? OR r.target_node_uuid = ?) "
            "ORDER BY c.uuid",
            parameters=[entity.uuid, entity.uuid, entity.uuid],
        )
        communities = [
            CommunityNode.model_validate_json(record["properties"]) for record in records
        ]
        if not communities:
            return None, False
        counts = Counter(community.uuid for community in communities)
        best = max(counts.values())
        winner = next(community for community in communities if counts[community.uuid] == best)
        return winner, True

    async def get_mentioned_nodes(self, executor, episodes):
        """Return the distinct entities that the given episodes mention."""
        records, _, _ = await executor.execute_query(
            "SELECT DISTINCT n.uuid, n.properties FROM graphiti.graph_edge e "
            "JOIN graphiti.graph_node ep ON ep.uuid = e.source_node_uuid AND ep.label = 'Episodic' "
            "JOIN graphiti.graph_node n ON n.uuid = e.target_node_uuid AND n.label = 'Entity' "
            "WHERE e.label = 'MENTIONS' AND ep.uuid IN (SELECT unnest(?::VARCHAR[])) "
            "ORDER BY n.uuid",
            parameters=[[episode.uuid for episode in episodes]],
        )
        return [EntityNode.model_validate_json(record["properties"]) for record in records]

    async def get_communities_by_nodes(self, executor, nodes):
        """Return the distinct communities that have any of the given entities as members."""
        records, _, _ = await executor.execute_query(
            "SELECT DISTINCT c.uuid, c.properties FROM graphiti.graph_edge e "
            "JOIN graphiti.graph_node c ON c.uuid = e.source_node_uuid AND c.label = 'Community' "
            "JOIN graphiti.graph_node m ON m.uuid = e.target_node_uuid AND m.label = 'Entity' "
            "WHERE e.label = 'HAS_MEMBER' AND m.uuid IN (SELECT unnest(?::VARCHAR[])) "
            "ORDER BY c.uuid",
            parameters=[[node.uuid for node in nodes]],
        )
        return [CommunityNode.model_validate_json(record["properties"]) for record in records]
