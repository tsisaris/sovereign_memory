"""Collect DuckDB operation objects; remaining unsupported families need full integration."""

from datetime import datetime

from graphiti_core.driver.operations.community_edge_ops import CommunityEdgeOperations
from graphiti_core.driver.operations.community_node_ops import CommunityNodeOperations
from graphiti_core.driver.operations.graph_ops import GraphMaintenanceOperations
from graphiti_core.driver.operations.has_episode_edge_ops import HasEpisodeEdgeOperations
from graphiti_core.driver.operations.next_episode_edge_ops import NextEpisodeEdgeOperations
from graphiti_core.driver.operations.saga_node_ops import SagaNodeOperations
from graphiti_core.driver.operations.search_ops import SearchOperations
from graphiti_core.nodes import CommunityNode, SagaNode

from .entity_edge_ops import DuckDBEntityEdgeOps as DuckDBEntityEdgeOps
from .entity_node_ops import DuckDBEntityNodeOps as DuckDBEntityNodeOps
from .episode_node_ops import DuckDBEpisodeNodeOps as DuckDBEpisodeNodeOps
from .episodic_edge_ops import DuckDBEpisodicEdgeOps as DuckDBEpisodicEdgeOps


class DuckDBCommunityNodeOps(CommunityNodeOperations):
    async def save(self, executor, node, tx=None):
        if not isinstance(node, CommunityNode):
            raise TypeError("Community node storage requires a CommunityNode")
        async with executor.transaction() as transaction:
            records, _, _ = await transaction.run(
                "INSERT INTO graphiti.graph_node (uuid, label, properties) VALUES (?, 'Community', ?) "
                "ON CONFLICT (uuid) DO UPDATE SET properties = excluded.properties "
                "WHERE graph_node.label = 'Community' RETURNING uuid",
                parameters=[node.uuid, node.model_dump_json()],
            )
            if not records:
                raise ValueError(f"Node UUID {node.uuid} belongs to a different node family")

    async def load_name_embedding(self, executor, node):
        node.name_embedding = (await self.get_by_uuid(executor, node.uuid)).name_embedding

    async def save_bulk(self, executor, nodes, tx=None, batch_size=100):
        async with executor.transaction() as transaction:
            for node in nodes:
                await self.save(executor, node, tx=transaction)

    async def _delete_matching(self, executor, predicate, parameters, tx):
        selected = "SELECT uuid FROM graphiti.graph_node WHERE label = 'Community' AND " + predicate
        async with executor.transaction() as transaction:
            await transaction.run(
                "DELETE FROM graphiti.graph_edge WHERE source_node_uuid IN ("
                + selected
                + ") OR target_node_uuid IN ("
                + selected
                + ")",
                parameters=parameters + parameters,
            )
            await transaction.run(
                "DELETE FROM graphiti.graph_node WHERE label = 'Community' AND " + predicate,
                parameters=parameters,
            )

    async def delete(self, executor, node, tx=None):
        if not isinstance(node, CommunityNode):
            raise TypeError("Community node storage requires a CommunityNode")
        await self._delete_matching(executor, "uuid = ?", [node.uuid], tx)

    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100):
        await self._delete_matching(
            executor, "json_extract_string(properties, 'group_id') = ?", [group_id], tx
        )

    async def delete_by_uuids(self, executor, uuids, tx=None, batch_size=100, group_id=None):
        predicate = "uuid IN (SELECT unnest(?))"
        parameters = [uuids]
        if group_id is not None:
            predicate += " AND json_extract_string(properties, 'group_id') = ?"
            parameters.append(group_id)
        await self._delete_matching(executor, predicate, parameters, tx)

    async def _read(self, executor, predicate, parameters, limit=None, uuid_cursor=None):
        query = "SELECT properties FROM graphiti.graph_node WHERE label = 'Community' AND ("
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
        return [CommunityNode.model_validate_json(record["properties"]) for record in records]

    async def get_by_uuid(self, executor, uuid):
        nodes = await self.get_by_uuids(executor, [uuid])
        if not nodes:
            from graphiti_core.errors import NodeNotFoundError

            raise NodeNotFoundError(uuid)
        return nodes[0]

    async def get_by_uuids(self, executor, uuids):
        return await self._read(executor, "uuid IN (SELECT unnest(?))", [uuids])

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        return await self._read(
            executor,
            "json_extract_string(properties, 'group_id') IN (SELECT unnest(?))",
            [group_ids],
            limit=limit,
            uuid_cursor=uuid_cursor,
        )

    async def get_communities_by_entity_uuids(self, executor, nodes):
        node_uuids = [n.uuid for n in nodes]
        records, _, _ = await executor.execute_query(
            "SELECT c.properties FROM graphiti.graph_node c "
            "JOIN graphiti.graph_edge e ON c.uuid = e.source_node_uuid "
            "WHERE c.label = 'Community' AND e.label = 'HAS_MEMBER' "
            "AND e.target_node_uuid IN (SELECT unnest(?))",
            parameters=[node_uuids],
        )
        return [CommunityNode.model_validate_json(record["properties"]) for record in records]

    async def get_community_clusters(self, executor, group_ids):
        return []

    async def clear_communities(self, executor):
        async with executor.transaction() as transaction:
            await transaction.run(
                "DELETE FROM graphiti.graph_edge WHERE source_node_uuid IN (SELECT uuid FROM graphiti.graph_node WHERE label = 'Community') OR target_node_uuid IN (SELECT uuid FROM graphiti.graph_node WHERE label = 'Community')"
            )
            await transaction.run("DELETE FROM graphiti.graph_node WHERE label = 'Community'")

    async def determine_entity_community(self, executor, entity):
        return None, False


class DuckDBSagaNodeOps(SagaNodeOperations):
    async def save(self, executor, node, tx=None):
        if not isinstance(node, SagaNode):
            raise TypeError("Saga node storage requires a SagaNode")
        async with executor.transaction() as transaction:
            records, _, _ = await transaction.run(
                "INSERT INTO graphiti.graph_node (uuid, label, properties) VALUES (?, 'Saga', ?) "
                "ON CONFLICT (uuid) DO UPDATE SET properties = excluded.properties "
                "WHERE graph_node.label = 'Saga' RETURNING uuid",
                parameters=[node.uuid, node.model_dump_json()],
            )
            if not records:
                raise ValueError(f"Node UUID {node.uuid} belongs to a different node family")

    async def save_bulk(self, executor, nodes, tx=None, batch_size=100):
        async with executor.transaction() as transaction:
            for node in nodes:
                await self.save(executor, node, tx=transaction)

    async def _delete_matching(self, executor, predicate, parameters, tx):
        selected = "SELECT uuid FROM graphiti.graph_node WHERE label = 'Saga' AND " + predicate
        async with executor.transaction() as transaction:
            await transaction.run(
                "DELETE FROM graphiti.graph_edge WHERE source_node_uuid IN ("
                + selected
                + ") OR target_node_uuid IN ("
                + selected
                + ")",
                parameters=parameters + parameters,
            )
            await transaction.run(
                "DELETE FROM graphiti.graph_node WHERE label = 'Saga' AND " + predicate,
                parameters=parameters,
            )

    async def delete(self, executor, node, tx=None):
        if not isinstance(node, SagaNode):
            raise TypeError("Saga node storage requires a SagaNode")
        await self._delete_matching(executor, "uuid = ?", [node.uuid], tx)

    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100):
        await self._delete_matching(
            executor, "json_extract_string(properties, 'group_id') = ?", [group_id], tx
        )

    async def delete_by_uuids(self, executor, uuids, tx=None, batch_size=100, group_id=None):
        predicate = "uuid IN (SELECT unnest(?))"
        parameters = [uuids]
        if group_id is not None:
            predicate += " AND json_extract_string(properties, 'group_id') = ?"
            parameters.append(group_id)
        await self._delete_matching(executor, predicate, parameters, tx)

    async def _read(self, executor, predicate, parameters, limit=None, uuid_cursor=None):
        query = "SELECT properties FROM graphiti.graph_node WHERE label = 'Saga' AND ("
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
        return [SagaNode.model_validate_json(record["properties"]) for record in records]

    async def get_by_uuid(self, executor, uuid):
        nodes = await self.get_by_uuids(executor, [uuid])
        if not nodes:
            from graphiti_core.errors import NodeNotFoundError

            raise NodeNotFoundError(uuid)
        return nodes[0]

    async def get_by_uuids(self, executor, uuids):
        return await self._read(executor, "uuid IN (SELECT unnest(?))", [uuids])

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        return await self._read(
            executor,
            "json_extract_string(properties, 'group_id') IN (SELECT unnest(?))",
            [group_ids],
            limit=limit,
            uuid_cursor=uuid_cursor,
        )

    async def get_or_create_saga(self, executor, saga_name, group_id, created_at):
        nodes = await self._read(
            executor,
            "json_extract_string(properties, 'name') = ? AND json_extract_string(properties, 'group_id') = ?",
            [saga_name, group_id],
        )
        if nodes:
            return nodes[0]
        node = SagaNode(name=saga_name, group_id=group_id, created_at=created_at)
        await self.save(executor, node)
        return node

    async def saga_get_previous_episode_uuid(self, executor, saga_uuid, current_episode_uuid):
        records, _, _ = await executor.execute_query(
            "SELECT e.target_node_uuid FROM graphiti.graph_edge e "
            "JOIN graphiti.graph_node s ON s.uuid = e.source_node_uuid "
            "JOIN graphiti.graph_node ep ON ep.uuid = e.target_node_uuid "
            "WHERE e.label = 'HAS_EPISODE' AND s.label = 'Saga' AND s.uuid = ? "
            "AND ep.label = 'Episodic' AND ep.uuid != ? "
            "ORDER BY CAST(json_extract_string(ep.properties, 'created_at') AS TIMESTAMPTZ) DESC LIMIT 1",
            parameters=[saga_uuid, current_episode_uuid],
        )
        return records[0]["target_node_uuid"] if records else None

    async def summarize_saga_episodes(self, executor, saga_id, since, limit):
        query = (
            "SELECT e.target_node_uuid, json_extract_string(ep.properties, 'content') AS content, json_extract_string(ep.properties, 'created_at') AS created_at "
            "FROM graphiti.graph_edge e "
            "JOIN graphiti.graph_node s ON s.uuid = e.source_node_uuid "
            "JOIN graphiti.graph_node ep ON ep.uuid = e.target_node_uuid "
            "WHERE e.label = 'HAS_EPISODE' AND s.label = 'Saga' AND s.uuid = ? AND ep.label = 'Episodic'"
        )
        parameters = [saga_id]
        if since is not None:
            query += (
                " AND CAST(json_extract_string(ep.properties, 'created_at') AS TIMESTAMPTZ) > ?"
            )
            parameters.append(since)
        query += (
            " ORDER BY CAST(json_extract_string(ep.properties, 'created_at') AS TIMESTAMPTZ) ASC"
        )
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)

        records, _, _ = await executor.execute_query(query, parameters=parameters)
        return [
            (record["target_node_uuid"], record["content"], record["created_at"])
            for record in records
        ]


class DuckDBCommunityEdgeOps(CommunityEdgeOperations):
    async def save(self, executor, edge, tx=None):
        from graphiti_core.edges import CommunityEdge
        from graphiti_core.errors import NodeNotFoundError

        if not isinstance(edge, CommunityEdge):
            raise TypeError("Community edge storage requires a CommunityEdge")
        async with executor.transaction() as transaction:
            records, _, _ = await transaction.run(
                "INSERT INTO graphiti.graph_edge "
                "(uuid, source_node_uuid, target_node_uuid, label, properties) "
                "VALUES (?, ?, ?, 'HAS_MEMBER', ?) ON CONFLICT (uuid) DO UPDATE SET "
                "source_node_uuid = excluded.source_node_uuid, "
                "target_node_uuid = excluded.target_node_uuid, properties = excluded.properties "
                "WHERE graph_edge.label = 'HAS_MEMBER' RETURNING uuid",
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
        async with executor.transaction() as transaction:
            for edge in edges:
                await self.save(executor, edge, tx=transaction)

    async def _delete_matching(self, executor, predicate, parameters, tx):
        async with executor.transaction() as transaction:
            await transaction.run(
                "DELETE FROM graphiti.graph_edge WHERE label = 'HAS_MEMBER' AND " + predicate,
                parameters=parameters,
            )

    async def delete(self, executor, edge, tx=None):
        await self._delete_matching(executor, "uuid = ?", [edge.uuid], tx)

    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100):
        await self._delete_matching(
            executor, "json_extract_string(properties, 'group_id') = ?", [group_id], tx
        )

    async def delete_by_uuids(self, executor, uuids, tx=None, batch_size=100, group_id=None):
        predicate = "uuid IN (SELECT unnest(?))"
        parameters = [uuids]
        if group_id is not None:
            predicate += " AND json_extract_string(properties, 'group_id') = ?"
            parameters.append(group_id)
        await self._delete_matching(executor, predicate, parameters, tx)

    async def _read(self, executor, predicate, parameters, limit=None, uuid_cursor=None):
        from graphiti_core.edges import CommunityEdge

        query = "SELECT properties FROM graphiti.graph_edge WHERE label = 'HAS_MEMBER' AND ("
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
        return [CommunityEdge.model_validate_json(record["properties"]) for record in records]

    async def get_by_uuid(self, executor, uuid):
        edges = await self.get_by_uuids(executor, [uuid])
        if not edges:
            from graphiti_core.errors import EdgeNotFoundError

            raise EdgeNotFoundError(uuid)
        return edges[0]

    async def get_by_uuids(self, executor, uuids):
        return await self._read(executor, "uuid IN (SELECT unnest(?))", [uuids])

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        return await self._read(
            executor,
            "json_extract_string(properties, 'group_id') IN (SELECT unnest(?))",
            [group_ids],
            limit=limit,
            uuid_cursor=uuid_cursor,
        )


class DuckDBHasEpisodeEdgeOps(HasEpisodeEdgeOperations):
    async def save(self, executor, edge, tx=None):
        from graphiti_core.edges import HasEpisodeEdge

        if not isinstance(edge, HasEpisodeEdge):
            raise TypeError("HasEpisode edge storage requires a HasEpisodeEdge")
        async with executor.transaction() as transaction:
            records, _, _ = await transaction.run(
                "INSERT INTO graphiti.graph_edge "
                "(uuid, source_node_uuid, target_node_uuid, label, properties) "
                "VALUES (?, ?, ?, 'HAS_EPISODE', ?) ON CONFLICT (uuid) DO UPDATE SET "
                "source_node_uuid = excluded.source_node_uuid, "
                "target_node_uuid = excluded.target_node_uuid, properties = excluded.properties "
                "WHERE graph_edge.label = 'HAS_EPISODE' RETURNING uuid",
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
        async with executor.transaction() as transaction:
            for edge in edges:
                await self.save(executor, edge, tx=transaction)

    async def _delete_matching(self, executor, predicate, parameters, tx):
        async with executor.transaction() as transaction:
            await transaction.run(
                "DELETE FROM graphiti.graph_edge WHERE label = 'HAS_EPISODE' AND " + predicate,
                parameters=parameters,
            )

    async def delete(self, executor, edge, tx=None):
        await self._delete_matching(executor, "uuid = ?", [edge.uuid], tx)

    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100):
        await self._delete_matching(
            executor, "json_extract_string(properties, 'group_id') = ?", [group_id], tx
        )

    async def delete_by_uuids(self, executor, uuids, tx=None, batch_size=100, group_id=None):
        predicate = "uuid IN (SELECT unnest(?))"
        parameters = [uuids]
        if group_id is not None:
            predicate += " AND json_extract_string(properties, 'group_id') = ?"
            parameters.append(group_id)
        await self._delete_matching(executor, predicate, parameters, tx)

    async def _read(self, executor, predicate, parameters, limit=None, uuid_cursor=None):
        from graphiti_core.edges import HasEpisodeEdge

        query = "SELECT properties FROM graphiti.graph_edge WHERE label = 'HAS_EPISODE' AND ("
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
        return [HasEpisodeEdge.model_validate_json(record["properties"]) for record in records]

    async def get_by_uuid(self, executor, uuid):
        edges = await self.get_by_uuids(executor, [uuid])
        if not edges:
            from graphiti_core.errors import EdgeNotFoundError

            raise EdgeNotFoundError(uuid)
        return edges[0]

    async def get_by_uuids(self, executor, uuids):
        return await self._read(executor, "uuid IN (SELECT unnest(?))", [uuids])

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        return await self._read(
            executor,
            "json_extract_string(properties, 'group_id') IN (SELECT unnest(?))",
            [group_ids],
            limit=limit,
            uuid_cursor=uuid_cursor,
        )


class DuckDBNextEpisodeEdgeOps(NextEpisodeEdgeOperations):
    async def save(self, executor, edge, tx=None):
        from graphiti_core.edges import NextEpisodeEdge

        if not isinstance(edge, NextEpisodeEdge):
            raise TypeError("NextEpisode edge storage requires a NextEpisodeEdge")
        async with executor.transaction() as transaction:
            records, _, _ = await transaction.run(
                "INSERT INTO graphiti.graph_edge "
                "(uuid, source_node_uuid, target_node_uuid, label, properties) "
                "VALUES (?, ?, ?, 'NEXT_EPISODE', ?) ON CONFLICT (uuid) DO UPDATE SET "
                "source_node_uuid = excluded.source_node_uuid, "
                "target_node_uuid = excluded.target_node_uuid, properties = excluded.properties "
                "WHERE graph_edge.label = 'NEXT_EPISODE' RETURNING uuid",
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
        async with executor.transaction() as transaction:
            for edge in edges:
                await self.save(executor, edge, tx=transaction)

    async def _delete_matching(self, executor, predicate, parameters, tx):
        async with executor.transaction() as transaction:
            await transaction.run(
                "DELETE FROM graphiti.graph_edge WHERE label = 'NEXT_EPISODE' AND " + predicate,
                parameters=parameters,
            )

    async def delete(self, executor, edge, tx=None):
        await self._delete_matching(executor, "uuid = ?", [edge.uuid], tx)

    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100):
        await self._delete_matching(
            executor, "json_extract_string(properties, 'group_id') = ?", [group_id], tx
        )

    async def delete_by_uuids(self, executor, uuids, tx=None, batch_size=100, group_id=None):
        predicate = "uuid IN (SELECT unnest(?))"
        parameters = [uuids]
        if group_id is not None:
            predicate += " AND json_extract_string(properties, 'group_id') = ?"
            parameters.append(group_id)
        await self._delete_matching(executor, predicate, parameters, tx)

    async def _read(self, executor, predicate, parameters, limit=None, uuid_cursor=None):
        from graphiti_core.edges import NextEpisodeEdge

        query = "SELECT properties FROM graphiti.graph_edge WHERE label = 'NEXT_EPISODE' AND ("
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
        return [NextEpisodeEdge.model_validate_json(record["properties"]) for record in records]

    async def get_by_uuid(self, executor, uuid):
        edges = await self.get_by_uuids(executor, [uuid])
        if not edges:
            from graphiti_core.errors import EdgeNotFoundError

            raise EdgeNotFoundError(uuid)
        return edges[0]

    async def get_by_uuids(self, executor, uuids):
        return await self._read(executor, "uuid IN (SELECT unnest(?))", [uuids])

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        return await self._read(
            executor,
            "json_extract_string(properties, 'group_id') IN (SELECT unnest(?))",
            [group_ids],
            limit=limit,
            uuid_cursor=uuid_cursor,
        )


class DuckDBSearchOps(SearchOperations):
    def _build_group_predicate(self, group_ids):
        if not group_ids:
            return "1=1"
        return "json_extract_string(properties, 'group_id') IN (SELECT unnest(?))"

    def build_node_search_filters(self, search_filters):
        if not search_filters:
            return "", []

        clauses = []
        parameters = []

        # Simple node labels filter
        if getattr(search_filters, "node_labels", None):
            labels = search_filters.node_labels
            # We check if any of the node labels are in the entity's labels array
            clauses.append(
                "EXISTS (SELECT * FROM UNNEST(json_extract_string(properties, 'labels')::VARCHAR[]) AS t(lbl) WHERE lbl IN (SELECT unnest(?)))"
            )
            parameters.append(labels)

        # Add basic support for created_at if needed, DuckDB supports standard comparisons
        if getattr(search_filters, "created_at", None):
            # This is a complex nested structure in the Graphiti core
            pass

        where_clause = " AND ".join(clauses) if clauses else "1=1"
        return f" AND ({where_clause})", parameters

    def build_edge_search_filters(self, search_filters):
        # Edges don't typically have the same rich filters
        return "", []

    async def node_fulltext_search(self, executor, query, search_filter, group_ids=None, limit=10):
        group_pred = self._build_group_predicate(group_ids)
        parameters = []
        if group_ids:
            parameters.append(group_ids)

        filter_sql, filter_params = self.build_node_search_filters(search_filter)
        parameters.extend(filter_params)

        parameters.append(f"%{query}%")
        parameters.append(limit)

        sql = f"""
            SELECT properties FROM graphiti.graph_node
            WHERE label = 'Entity'
            AND {group_pred}
            {filter_sql}
            AND json_extract_string(properties, 'name') ILIKE ?
            LIMIT ?
        """
        records, _, _ = await executor.execute_query(sql, parameters=parameters)
        from graphiti_core.nodes import EntityNode

        return [EntityNode.model_validate_json(record["properties"]) for record in records]

    async def node_similarity_search(
        self, executor, search_vector, search_filter, group_ids=None, limit=10, min_score=0.6
    ):
        group_pred = self._build_group_predicate(group_ids)
        parameters = []
        if group_ids:
            parameters.append(group_ids)

        filter_sql, filter_params = self.build_node_search_filters(search_filter)
        parameters.extend(filter_params)

        parameters.append(search_vector)
        parameters.append(search_vector)
        parameters.append(limit)

        sql = f"""
            SELECT properties FROM graphiti.graph_node
            WHERE label = 'Entity'
            AND {group_pred}
            {filter_sql}
            AND array_cosine_distance(
                cast(json_extract_string(properties, 'name_embedding') as FLOAT[]),
                ?::FLOAT[]
            ) <= {1.0 - min_score}
            ORDER BY array_cosine_distance(
                cast(json_extract_string(properties, 'name_embedding') as FLOAT[]),
                ?::FLOAT[]
            ) ASC
            LIMIT ?
        """
        records, _, _ = await executor.execute_query(sql, parameters=parameters)
        from graphiti_core.nodes import EntityNode

        return [EntityNode.model_validate_json(record["properties"]) for record in records]

    async def node_bfs_search(
        self, executor, origin_uuids, search_filter, max_depth, group_ids=None, limit=10
    ):
        # A simple BFS logic or placeholder
        return []

    async def edge_fulltext_search(self, executor, query, search_filter, group_ids=None, limit=10):
        group_pred = self._build_group_predicate(group_ids)
        parameters = []
        if group_ids:
            parameters.append(group_ids)

        filter_sql, filter_params = self.build_edge_search_filters(search_filter)
        parameters.extend(filter_params)

        parameters.append(f"%{query}%")
        parameters.append(limit)

        sql = f"""
            SELECT properties FROM graphiti.graph_edge
            WHERE label = 'RELATES_TO'
            AND {group_pred}
            {filter_sql}
            AND json_extract_string(properties, 'fact') ILIKE ?
            LIMIT ?
        """
        records, _, _ = await executor.execute_query(sql, parameters=parameters)
        from graphiti_core.edges import EntityEdge

        return [EntityEdge.model_validate_json(record["properties"]) for record in records]

    async def edge_similarity_search(
        self,
        executor,
        search_vector,
        source_node_uuid,
        target_node_uuid,
        search_filter,
        group_ids=None,
        limit=10,
        min_score=0.6,
    ):
        group_pred = self._build_group_predicate(group_ids)
        parameters = []
        if group_ids:
            parameters.append(group_ids)

        if source_node_uuid:
            parameters.append(source_node_uuid)
        if target_node_uuid:
            parameters.append(target_node_uuid)

        filter_sql, filter_params = self.build_edge_search_filters(search_filter)
        parameters.extend(filter_params)

        source_pred = "AND source_node_uuid = ?" if source_node_uuid else ""
        target_pred = "AND target_node_uuid = ?" if target_node_uuid else ""

        parameters.append(search_vector)
        parameters.append(search_vector)
        parameters.append(limit)

        sql = f"""
            SELECT properties FROM graphiti.graph_edge
            WHERE label = 'RELATES_TO'
            AND {group_pred}
            {source_pred}
            {target_pred}
            {filter_sql}
            AND array_cosine_distance(
                cast(json_extract_string(properties, 'fact_embedding') as FLOAT[]),
                ?::FLOAT[]
            ) <= {1.0 - min_score}
            ORDER BY array_cosine_distance(
                cast(json_extract_string(properties, 'fact_embedding') as FLOAT[]),
                ?::FLOAT[]
            ) ASC
            LIMIT ?
        """
        records, _, _ = await executor.execute_query(sql, parameters=parameters)
        from graphiti_core.edges import EntityEdge

        return [EntityEdge.model_validate_json(record["properties"]) for record in records]

    async def edge_bfs_search(
        self, executor, origin_uuids, max_depth, search_filter, group_ids=None, limit=10
    ):
        return []

    async def episode_fulltext_search(
        self, executor, query, search_filter, group_ids=None, limit=10
    ):
        group_pred = self._build_group_predicate(group_ids)
        parameters = []
        if group_ids:
            parameters.append(group_ids)

        # Episodic node filter handling would be here

        parameters.append(f"%{query}%")
        parameters.append(limit)

        sql = f"""
            SELECT properties FROM graphiti.graph_node
            WHERE label = 'Episodic'
            AND {group_pred}
            AND json_extract_string(properties, 'content') ILIKE ?
            LIMIT ?
        """
        records, _, _ = await executor.execute_query(sql, parameters=parameters)
        from graphiti_core.nodes import EpisodicNode

        return [EpisodicNode.model_validate_json(record["properties"]) for record in records]

    async def community_fulltext_search(self, executor, query, group_ids=None, limit=10):
        group_pred = self._build_group_predicate(group_ids)
        parameters = []
        if group_ids:
            parameters.append(group_ids)
        parameters.append(f"%{query}%")
        parameters.append(f"%{query}%")
        parameters.append(limit)

        sql = f"""
            SELECT properties FROM graphiti.graph_node
            WHERE label = 'Community'
            AND {group_pred}
            AND (json_extract_string(properties, 'name') ILIKE ? OR json_extract_string(properties, 'summary') ILIKE ?)
            LIMIT ?
        """
        records, _, _ = await executor.execute_query(sql, parameters=parameters)
        from graphiti_core.nodes import CommunityNode

        return [CommunityNode.model_validate_json(record["properties"]) for record in records]

    async def community_similarity_search(
        self, executor, search_vector, group_ids=None, limit=10, min_score=0.6
    ):
        group_pred = self._build_group_predicate(group_ids)
        parameters = []
        if group_ids:
            parameters.append(group_ids)
        parameters.append(search_vector)
        parameters.append(search_vector)
        parameters.append(limit)

        sql = f"""
            SELECT properties FROM graphiti.graph_node
            WHERE label = 'Community'
            AND {group_pred}
            AND array_cosine_distance(
                cast(json_extract_string(properties, 'name_embedding') as FLOAT[]),
                ?::FLOAT[]
            ) <= {1.0 - min_score}
            ORDER BY array_cosine_distance(
                cast(json_extract_string(properties, 'name_embedding') as FLOAT[]),
                ?::FLOAT[]
            ) ASC
            LIMIT ?
        """
        records, _, _ = await executor.execute_query(sql, parameters=parameters)
        from graphiti_core.nodes import CommunityNode

        return [CommunityNode.model_validate_json(record["properties"]) for record in records]

    async def node_distance_reranker(self, executor, node_uuids, center_node_uuid, min_score=0):
        return []

    async def episode_mentions_reranker(self, executor, node_uuids, min_score=0):
        return []

    def build_fulltext_query(self, query, group_ids=None, max_query_length=8000):
        return ""

    async def get_relevant_nodes(self, executor, nodes, search_filter, group_id, min_score, limit):
        return []

    async def get_relevant_edges(self, executor, edges, search_filter, min_score, limit):
        return []

    async def get_edge_invalidation_candidates(
        self, executor, edges, search_filter, min_score, limit
    ):
        return []


class DuckDBGraphOps(GraphMaintenanceOperations):
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
        pass

    async def delete_all_indexes(self, executor):
        pass

    async def get_community_clusters(self, executor, group_ids=None):
        return []

    async def determine_entity_community(self, executor, entity):
        pass

    async def get_mentioned_nodes(self, executor, episodes):
        return []

    async def get_communities_by_nodes(self, executor, nodes):
        return []
