"""Connect Graphiti's original entity API to the same native operation objects."""

from graphiti_core.driver.graph_operations.graph_operations import GraphOperationsInterface
from graphiti_core.edges import (
    CommunityEdge,
    EntityEdge,
    EpisodicEdge,
    HasEpisodeEdge,
    NextEpisodeEdge,
)
from graphiti_core.nodes import CommunityNode, EntityNode, EpisodicNode, SagaNode


class DuckDBGraphOperations(GraphOperationsInterface):
    """Delegate supported legacy entity methods without guessing other node families."""

    async def node_save(self, node, driver):
        """Persist a real entity through the driver's shared entity operations."""
        if not isinstance(node, EntityNode):
            raise NotImplementedError("This operation requires an EntityNode")
        await driver.entity_node_ops.save(driver, node)

    async def episodic_node_save(self, node, driver):
        """Persist an actual episode through the driver's shared operation object."""
        await driver.episode_node_ops.save(driver, node)

    async def remove_episode(self, driver, episode_uuid):
        """Use the same native episode lifecycle as the typed operation interface."""
        await driver.episode_node_ops.remove_episode_and_singletons(driver, episode_uuid)

    async def episodic_node_delete(self, node, driver):
        """Detach the selected episode while leaving its mentioned entities intact."""
        await driver.episode_node_ops.delete(driver, node)

    async def episodic_node_save_bulk(self, _cls, driver, transaction, nodes, batch_size=100):
        """Normalize maintained episode dictionaries without changing transaction ownership."""
        prepared = [
            node if isinstance(node, EpisodicNode) else EpisodicNode.model_validate(node)
            for node in nodes
        ]
        await driver.episode_node_ops.save_bulk(
            driver, prepared, tx=transaction, batch_size=batch_size
        )

    async def episodic_node_delete_by_group_id(self, _cls, driver, group_id, batch_size=100):
        """Delete episodes in the requested group through the shared operation object."""
        await driver.episode_node_ops.delete_by_group_id(driver, group_id, batch_size=batch_size)

    async def episodic_node_delete_by_uuids(
        self, _cls, driver, uuids, group_id=None, batch_size=100
    ):
        """Preserve optional group restrictions while deleting requested episodes."""
        await driver.episode_node_ops.delete_by_uuids(
            driver, uuids, group_id=group_id, batch_size=batch_size
        )

    async def episodic_node_get_by_uuid(self, _cls, driver, uuid):
        """Read an actual persisted episode or propagate its missing-node exception."""
        return await driver.episode_node_ops.get_by_uuid(driver, uuid)

    async def episodic_node_get_by_uuids(self, _cls, driver, uuids):
        """Return only episodes with matching UUIDs."""
        return await driver.episode_node_ops.get_by_uuids(driver, uuids)

    async def episodic_node_get_by_group_ids(
        self, _cls, driver, group_ids, limit=None, uuid_cursor=None
    ):
        """Delegate group filtering and pagination to the real episode operations."""
        return await driver.episode_node_ops.get_by_group_ids(
            driver, group_ids, limit=limit, uuid_cursor=uuid_cursor
        )

    async def episodic_node_get_by_entity_node_uuid(self, _cls, driver, entity_node_uuid):
        """Return episodes connected by an actual persisted MENTIONS edge."""
        return await driver.episode_node_ops.get_by_entity_node_uuid(driver, entity_node_uuid)

    async def retrieve_episodes(
        self, driver, reference_time, last_n=3, group_ids=None, source=None, saga=None
    ):
        """Apply maintained temporal, group, source and saga filters in native storage."""
        return await driver.episode_node_ops.retrieve_episodes(
            driver, reference_time, last_n=last_n, group_ids=group_ids, source=source, saga=saga
        )

    async def node_delete(self, node, driver):
        """Delete only the concrete entity family and its incident edges."""
        if isinstance(node, EpisodicNode):
            return await driver.episode_node_ops.delete(driver, node)
        if isinstance(node, CommunityNode):
            return await driver.community_node_ops.delete(driver, node)
        if isinstance(node, SagaNode):
            return await driver.saga_node_ops.delete(driver, node)
        if not isinstance(node, EntityNode):
            raise NotImplementedError("This operation requires an EntityNode")
        await driver.entity_node_ops.delete(driver, node)

    async def node_save_bulk(self, _cls, driver, transaction, nodes, batch_size=100):
        """Pass the existing caller transaction unchanged to the entity bulk operation."""
        if _cls is not None and not issubclass(_cls, EntityNode):
            raise NotImplementedError("This operation requires EntityNode models")
        prepared = []
        for node in nodes:
            if isinstance(node, EntityNode):
                prepared.append(node)
            else:
                # The maintained bulk utility flattens custom attributes into this dictionary.
                data = {key: value for key, value in node.items() if key in EntityNode.model_fields}
                data["attributes"] = {
                    **(node.get("attributes") or {}),
                    **{
                        key: value
                        for key, value in node.items()
                        if key not in EntityNode.model_fields
                    },
                }
                prepared.append(EntityNode.model_validate(data))
        await driver.entity_node_ops.save_bulk(
            driver, prepared, tx=transaction, batch_size=batch_size
        )

    async def node_delete_by_group_id(self, _cls, driver, group_id, batch_size=100):
        """Delete only entity nodes in the requested group through native operations."""
        if issubclass(_cls, EpisodicNode):
            return await driver.episode_node_ops.delete_by_group_id(
                driver, group_id, batch_size=batch_size
            )
        if issubclass(_cls, CommunityNode):
            return await driver.community_node_ops.delete_by_group_id(
                driver, group_id, batch_size=batch_size
            )
        if issubclass(_cls, SagaNode):
            return await driver.saga_node_ops.delete_by_group_id(
                driver, group_id, batch_size=batch_size
            )
        if not issubclass(_cls, EntityNode):
            raise NotImplementedError("This operation requires EntityNode models")
        await driver.entity_node_ops.delete_by_group_id(driver, group_id, batch_size=batch_size)

    async def node_delete_by_uuids(self, _cls, driver, uuids, group_id=None, batch_size=100):
        """Retain supplied UUID and group restrictions in the actual deletion."""
        if issubclass(_cls, EpisodicNode):
            return await driver.episode_node_ops.delete_by_uuids(
                driver, uuids, group_id=group_id, batch_size=batch_size
            )
        if issubclass(_cls, CommunityNode):
            return await driver.community_node_ops.delete_by_uuids(
                driver, uuids, group_id=group_id, batch_size=batch_size
            )
        if issubclass(_cls, SagaNode):
            return await driver.saga_node_ops.delete_by_uuids(
                driver, uuids, group_id=group_id, batch_size=batch_size
            )
        if not issubclass(_cls, EntityNode):
            raise NotImplementedError("This operation requires EntityNode models")
        await driver.entity_node_ops.delete_by_uuids(
            driver, uuids, group_id=group_id, batch_size=batch_size
        )

    async def node_get_by_uuid(self, _cls, driver, uuid):
        """Return the persisted entity or its maintained missing-node exception."""
        if not issubclass(_cls, EntityNode):
            raise NotImplementedError("This operation requires EntityNode models")
        return await driver.entity_node_ops.get_by_uuid(driver, uuid)

    async def node_get_by_uuids(self, _cls, driver, uuids, group_id=None):
        """Read the requested entity UUIDs without dropping the caller's group filter."""
        if not issubclass(_cls, EntityNode):
            raise NotImplementedError("This operation requires EntityNode models")
        return await driver.entity_node_ops.get_by_uuids(driver, uuids, group_id=group_id)

    async def node_get_by_group_ids(self, _cls, driver, group_ids, limit=None, uuid_cursor=None):
        """Preserve the original group, pagination and limit arguments."""
        if not issubclass(_cls, EntityNode):
            raise NotImplementedError("This operation requires EntityNode models")
        return await driver.entity_node_ops.get_by_group_ids(
            driver, group_ids, limit=limit, uuid_cursor=uuid_cursor
        )

    async def node_get_by_names(self, _cls, driver, names, group_ids):
        """Perform the original exact-name lookup within explicit groups."""
        if not issubclass(_cls, EntityNode):
            raise NotImplementedError("This operation requires EntityNode models")
        return await driver.entity_node_ops.get_by_names(driver, names, group_ids)

    async def node_load_embeddings(self, node, driver):
        """Populate an entity from its stored embedding without calling a model."""
        if not isinstance(node, EntityNode):
            raise NotImplementedError("This operation requires an EntityNode")
        await driver.entity_node_ops.load_embeddings(driver, node)

    async def node_load_embeddings_bulk(self, driver, nodes, batch_size=100):
        """Return only actual stored entity embeddings after the shared bulk read."""
        if any(not isinstance(node, EntityNode) for node in nodes):
            raise NotImplementedError("This operation requires EntityNode models")
        embeddings = await driver.entity_node_ops.load_embedding_map(driver, nodes)
        return {uuid: embedding for uuid, embedding in embeddings.items() if embedding is not None}

    async def clear_data(self, driver, group_ids=None):
        """Delegate explicit graph cleanup to the schema-scoped maintenance operation."""
        await driver.graph_ops.clear_data(driver, group_ids)

    async def edge_save(self, edge, driver):
        """Persist the actual entity relationship through the shared operation object."""
        if not isinstance(edge, EntityEdge):
            raise NotImplementedError("This operation requires an EntityEdge")
        await driver.entity_edge_ops.save(driver, edge)

    async def edge_delete(self, edge, driver):
        """Delete only the supplied entity relationship."""
        if isinstance(edge, EpisodicEdge):
            return await driver.episodic_edge_ops.delete(driver, edge)
        if isinstance(edge, CommunityEdge):
            return await driver.community_edge_ops.delete(driver, edge)
        if isinstance(edge, HasEpisodeEdge):
            return await driver.has_episode_edge_ops.delete(driver, edge)
        if isinstance(edge, NextEpisodeEdge):
            return await driver.next_episode_edge_ops.delete(driver, edge)
        if not isinstance(edge, EntityEdge):
            raise NotImplementedError("This operation requires an EntityEdge")
        await driver.entity_edge_ops.delete(driver, edge)

    async def edge_save_bulk(self, _cls, driver, transaction, edges, batch_size=100):
        """Normalize maintained bulk dictionaries and preserve the caller's transaction."""
        if _cls is not None and not issubclass(_cls, EntityEdge):
            raise NotImplementedError("This operation requires EntityEdge models")
        prepared = []
        for edge in edges:
            if isinstance(edge, EntityEdge):
                prepared.append(edge)
            else:
                data = {key: value for key, value in edge.items() if key in EntityEdge.model_fields}
                data["attributes"] = {
                    **(edge.get("attributes") or {}),
                    **{
                        key: value
                        for key, value in edge.items()
                        if key not in EntityEdge.model_fields
                    },
                }
                prepared.append(EntityEdge.model_validate(data))
        await driver.entity_edge_ops.save_bulk(
            driver, prepared, tx=transaction, batch_size=batch_size
        )

    async def edge_delete_by_uuids(self, _cls, driver, uuids, group_id=None):
        """Retain UUID and optional group restrictions in entity-edge deletion."""
        if issubclass(_cls, EpisodicEdge):
            return await driver.episodic_edge_ops.delete_by_uuids(driver, uuids, group_id=group_id)
        if issubclass(_cls, CommunityEdge):
            return await driver.community_edge_ops.delete_by_uuids(driver, uuids, group_id=group_id)
        if issubclass(_cls, HasEpisodeEdge):
            return await driver.has_episode_edge_ops.delete_by_uuids(
                driver, uuids, group_id=group_id
            )
        if issubclass(_cls, NextEpisodeEdge):
            return await driver.next_episode_edge_ops.delete_by_uuids(
                driver, uuids, group_id=group_id
            )
        if not issubclass(_cls, EntityEdge):
            raise NotImplementedError("This operation requires EntityEdge models")
        await driver.entity_edge_ops.delete_by_uuids(driver, uuids, group_id=group_id)

    async def edge_get_by_uuid(self, _cls, driver, uuid):
        """Return the persisted entity relationship or its maintained missing-edge error."""
        if not issubclass(_cls, EntityEdge):
            raise NotImplementedError("This operation requires EntityEdge models")
        return await driver.entity_edge_ops.get_by_uuid(driver, uuid)

    async def edge_get_by_uuids(self, _cls, driver, uuids):
        """Read actual relationships through the same entity-edge operation object."""
        if not issubclass(_cls, EntityEdge):
            raise NotImplementedError("This operation requires EntityEdge models")
        return await driver.entity_edge_ops.get_by_uuids(driver, uuids)

    async def edge_get_by_group_ids(self, _cls, driver, group_ids, limit=None, uuid_cursor=None):
        """Keep group filters and pagination intact at the public SDK boundary."""
        if not issubclass(_cls, EntityEdge):
            raise NotImplementedError("This operation requires EntityEdge models")
        return await driver.entity_edge_ops.get_by_group_ids(
            driver, group_ids, limit=limit, uuid_cursor=uuid_cursor
        )

    async def edge_get_between_nodes(self, _cls, driver, source_node_uuid, target_node_uuid):
        """Delegate the directional endpoint lookup without swapping source and target."""
        if not issubclass(_cls, EntityEdge):
            raise NotImplementedError("This operation requires EntityEdge models")
        return await driver.entity_edge_ops.get_between_nodes(
            driver, source_node_uuid, target_node_uuid
        )

    async def edge_get_by_node_uuid(self, _cls, driver, node_uuid):
        """Read incident entity relationships with their persisted direction unchanged."""
        if not issubclass(_cls, EntityEdge):
            raise NotImplementedError("This operation requires EntityEdge models")
        return await driver.entity_edge_ops.get_by_node_uuid(driver, node_uuid)

    async def edge_load_embeddings(self, edge, driver):
        """Load the stored fact embedding through the shared entity-edge operation."""
        if not isinstance(edge, EntityEdge):
            raise NotImplementedError("This operation requires an EntityEdge")
        await driver.entity_edge_ops.load_embeddings(driver, edge)

    async def edge_load_embeddings_bulk(self, driver, edges, batch_size=100):
        """Return only actually stored entity-edge embeddings."""
        if any(not isinstance(edge, EntityEdge) for edge in edges):
            raise NotImplementedError("This operation requires EntityEdge models")
        embeddings = await driver.entity_edge_ops.load_embedding_map(driver, edges)
        return {uuid: embedding for uuid, embedding in embeddings.items() if embedding is not None}

    async def episodic_edge_save(self, edge, driver):
        """Persist a real episode-to-entity mention through the shared native operation."""
        await driver.episodic_edge_ops.save(driver, edge)

    async def episodic_edge_save_bulk(self, _cls, driver, transaction, edges, batch_size=100):
        """Normalize maintained mention dictionaries and preserve the supplied transaction."""
        prepared = [
            edge if isinstance(edge, EpisodicEdge) else EpisodicEdge.model_validate(edge)
            for edge in edges
        ]
        await driver.episodic_edge_ops.save_bulk(
            driver, prepared, tx=transaction, batch_size=batch_size
        )

    async def episodic_edge_delete(self, edge, driver):
        """Remove only the supplied persisted mention."""
        await driver.episodic_edge_ops.delete(driver, edge)

    async def episodic_edge_delete_by_uuids(self, _cls, driver, uuids, group_id=None):
        """Keep explicit group restrictions when deleting selected mentions."""
        await driver.episodic_edge_ops.delete_by_uuids(driver, uuids, group_id=group_id)

    async def episodic_edge_get_by_uuid(self, _cls, driver, uuid):
        """Return an actual mention or propagate its maintained missing-edge exception."""
        return await driver.episodic_edge_ops.get_by_uuid(driver, uuid)

    async def episodic_edge_get_by_uuids(self, _cls, driver, uuids):
        """Read matching persisted mentions without fabricating records."""
        return await driver.episodic_edge_ops.get_by_uuids(driver, uuids)

    async def episodic_edge_get_by_group_ids(
        self, _cls, driver, group_ids, limit=None, uuid_cursor=None
    ):
        """Preserve group filtering and UUID pagination for the public mention API."""
        return await driver.episodic_edge_ops.get_by_group_ids(
            driver, group_ids, limit=limit, uuid_cursor=uuid_cursor
        )

    async def community_node_save(self, node, driver):
        await driver.community_node_ops.save(driver, node)

    async def community_node_delete(self, node, driver):
        await driver.community_node_ops.delete(driver, node)

    async def community_node_save_bulk(self, _cls, driver, transaction, nodes, batch_size=100):
        prepared = [
            node if isinstance(node, CommunityNode) else CommunityNode.model_validate(node)
            for node in nodes
        ]
        await driver.community_node_ops.save_bulk(
            driver, prepared, tx=transaction, batch_size=batch_size
        )

    async def community_node_delete_by_group_id(self, _cls, driver, group_id, batch_size=100):
        await driver.community_node_ops.delete_by_group_id(driver, group_id, batch_size=batch_size)

    async def community_node_delete_by_uuids(
        self, _cls, driver, uuids, group_id=None, batch_size=100
    ):
        await driver.community_node_ops.delete_by_uuids(
            driver, uuids, group_id=group_id, batch_size=batch_size
        )

    async def community_node_get_by_uuid(self, _cls, driver, uuid):
        return await driver.community_node_ops.get_by_uuid(driver, uuid)

    async def community_node_get_by_uuids(self, _cls, driver, uuids):
        return await driver.community_node_ops.get_by_uuids(driver, uuids)

    async def community_node_get_by_group_ids(
        self, _cls, driver, group_ids, limit=None, uuid_cursor=None
    ):
        return await driver.community_node_ops.get_by_group_ids(
            driver, group_ids, limit=limit, uuid_cursor=uuid_cursor
        )

    async def community_node_load_name_embedding(self, node, driver):
        await driver.community_node_ops.load_name_embedding(driver, node)

    async def get_communities_by_entity_uuids(self, driver, nodes):
        return await driver.community_node_ops.get_communities_by_entity_uuids(driver, nodes)

    async def get_community_clusters(self, driver, group_ids):
        return await driver.community_node_ops.get_community_clusters(driver, group_ids)

    async def clear_communities(self, driver):
        return await driver.community_node_ops.clear_communities(driver)

    async def determine_entity_community(self, driver, entity):
        return await driver.community_node_ops.determine_entity_community(driver, entity)

    async def saga_node_save(self, node, driver):
        await driver.saga_node_ops.save(driver, node)

    async def saga_node_delete(self, node, driver):
        await driver.saga_node_ops.delete(driver, node)

    async def saga_node_save_bulk(self, _cls, driver, transaction, nodes, batch_size=100):
        prepared = [
            node if isinstance(node, SagaNode) else SagaNode.model_validate(node) for node in nodes
        ]
        await driver.saga_node_ops.save_bulk(
            driver, prepared, tx=transaction, batch_size=batch_size
        )

    async def saga_node_delete_by_group_id(self, _cls, driver, group_id, batch_size=100):
        await driver.saga_node_ops.delete_by_group_id(driver, group_id, batch_size=batch_size)

    async def saga_node_delete_by_uuids(self, _cls, driver, uuids, group_id=None, batch_size=100):
        await driver.saga_node_ops.delete_by_uuids(
            driver, uuids, group_id=group_id, batch_size=batch_size
        )

    async def saga_node_get_by_uuid(self, _cls, driver, uuid):
        return await driver.saga_node_ops.get_by_uuid(driver, uuid)

    async def saga_node_get_by_uuids(self, _cls, driver, uuids):
        return await driver.saga_node_ops.get_by_uuids(driver, uuids)

    async def saga_node_get_by_group_ids(
        self, _cls, driver, group_ids, limit=None, uuid_cursor=None
    ):
        return await driver.saga_node_ops.get_by_group_ids(
            driver, group_ids, limit=limit, uuid_cursor=uuid_cursor
        )

    async def saga_get_previous_episode_uuid(self, driver, saga_uuid, current_episode_uuid):
        return await driver.saga_node_ops.get_previous_episode_uuid(
            driver, saga_uuid, current_episode_uuid
        )

    async def saga_get_episode_contents(self, driver, saga_uuid, since=None):
        return await driver.saga_node_ops.get_episode_contents(driver, saga_uuid, since=since)

    async def community_edge_save(self, edge, driver):
        await driver.community_edge_ops.save(driver, edge)

    async def community_edge_delete(self, edge, driver):
        await driver.community_edge_ops.delete(driver, edge)

    async def community_edge_delete_by_uuids(self, _cls, driver, uuids, group_id=None):
        await driver.community_edge_ops.delete_by_uuids(driver, uuids, group_id=group_id)

    async def community_edge_get_by_uuid(self, _cls, driver, uuid):
        return await driver.community_edge_ops.get_by_uuid(driver, uuid)

    async def community_edge_get_by_uuids(self, _cls, driver, uuids):
        return await driver.community_edge_ops.get_by_uuids(driver, uuids)

    async def community_edge_get_by_group_ids(
        self, _cls, driver, group_ids, limit=None, uuid_cursor=None
    ):
        return await driver.community_edge_ops.get_by_group_ids(
            driver, group_ids, limit=limit, uuid_cursor=uuid_cursor
        )

    async def has_episode_edge_save(self, edge, driver):
        await driver.has_episode_edge_ops.save(driver, edge)

    async def has_episode_edge_delete(self, edge, driver):
        await driver.has_episode_edge_ops.delete(driver, edge)

    async def has_episode_edge_delete_by_uuids(self, _cls, driver, uuids, group_id=None):
        await driver.has_episode_edge_ops.delete_by_uuids(driver, uuids, group_id=group_id)

    async def has_episode_edge_get_by_uuid(self, _cls, driver, uuid):
        return await driver.has_episode_edge_ops.get_by_uuid(driver, uuid)

    async def has_episode_edge_get_by_uuids(self, _cls, driver, uuids):
        return await driver.has_episode_edge_ops.get_by_uuids(driver, uuids)

    async def has_episode_edge_get_by_group_ids(
        self, _cls, driver, group_ids, limit=None, uuid_cursor=None
    ):
        return await driver.has_episode_edge_ops.get_by_group_ids(
            driver, group_ids, limit=limit, uuid_cursor=uuid_cursor
        )

    async def next_episode_edge_save(self, edge, driver):
        await driver.next_episode_edge_ops.save(driver, edge)

    async def next_episode_edge_delete(self, edge, driver):
        await driver.next_episode_edge_ops.delete(driver, edge)

    async def next_episode_edge_delete_by_uuids(self, _cls, driver, uuids, group_id=None):
        await driver.next_episode_edge_ops.delete_by_uuids(driver, uuids, group_id=group_id)

    async def next_episode_edge_get_by_uuid(self, _cls, driver, uuid):
        return await driver.next_episode_edge_ops.get_by_uuid(driver, uuid)

    async def next_episode_edge_get_by_uuids(self, _cls, driver, uuids):
        return await driver.next_episode_edge_ops.get_by_uuids(driver, uuids)

    async def next_episode_edge_get_by_group_ids(
        self, _cls, driver, group_ids, limit=None, uuid_cursor=None
    ):
        return await driver.next_episode_edge_ops.get_by_group_ids(
            driver, group_ids, limit=limit, uuid_cursor=uuid_cursor
        )
