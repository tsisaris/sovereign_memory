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

# Node and edge model families that own their storage through a dedicated operation object.
_NODE_FAMILIES = (
    (EpisodicNode, "episode_node_ops"),
    (CommunityNode, "community_node_ops"),
    (SagaNode, "saga_node_ops"),
)
_EDGE_FAMILIES = (
    (EpisodicEdge, "episodic_edge_ops"),
    (CommunityEdge, "community_edge_ops"),
    (HasEpisodeEdge, "has_episode_edge_ops"),
    (NextEpisodeEdge, "next_episode_edge_ops"),
)


def _family_ops(families, driver, model):
    """Return the driver's operation object for a non-entity model class, else None."""
    for family, attribute in families:
        if isinstance(model, type) and issubclass(model, family):
            return getattr(driver, attribute)
    return None


class DuckDBGraphOperations(GraphOperationsInterface):
    """Route Graphiti's legacy model methods to the driver's native operation objects.

    Every hook forwards to exactly one typed operation object so the public model API and
    the ``graphiti.nodes``/``graphiti.edges`` namespaces share one implementation. A
    model family without a native operation raises NotImplementedError rather than
    pretending to succeed.
    """

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
        ops = _family_ops(_NODE_FAMILIES, driver, type(node))
        if ops is not None:
            return await ops.delete(driver, node)
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
        ops = _family_ops(_NODE_FAMILIES, driver, _cls)
        if ops is not None:
            return await ops.delete_by_group_id(driver, group_id, batch_size=batch_size)
        if not issubclass(_cls, EntityNode):
            raise NotImplementedError("This operation requires EntityNode models")
        await driver.entity_node_ops.delete_by_group_id(driver, group_id, batch_size=batch_size)

    async def node_delete_by_uuids(self, _cls, driver, uuids, group_id=None, batch_size=100):
        """Retain supplied UUID and group restrictions in the actual deletion."""
        ops = _family_ops(_NODE_FAMILIES, driver, _cls)
        if ops is not None:
            return await ops.delete_by_uuids(
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
        ops = _family_ops(_EDGE_FAMILIES, driver, type(edge))
        if ops is not None:
            return await ops.delete(driver, edge)
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
        ops = _family_ops(_EDGE_FAMILIES, driver, _cls)
        if ops is not None:
            return await ops.delete_by_uuids(driver, uuids, group_id=group_id)
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
        """Persist a community through the driver's community operations."""
        await driver.community_node_ops.save(driver, node)

    async def community_node_delete(self, node, driver):
        """Delete a community and its incident membership edges."""
        await driver.community_node_ops.delete(driver, node)

    async def community_node_save_bulk(self, _cls, driver, transaction, nodes, batch_size=100):
        """Save communities on the caller's transaction, accepting model dictionaries."""
        prepared = [CommunityNode.model_validate(node) for node in nodes]
        await driver.community_node_ops.save_bulk(
            driver, prepared, tx=transaction, batch_size=batch_size
        )

    async def community_node_delete_by_group_id(self, _cls, driver, group_id, batch_size=100):
        """Delete communities of exactly one group."""
        await driver.community_node_ops.delete_by_group_id(driver, group_id, batch_size=batch_size)

    async def community_node_delete_by_uuids(
        self, _cls, driver, uuids, group_id=None, batch_size=100
    ):
        """Delete listed communities, keeping an optional group restriction."""
        await driver.community_node_ops.delete_by_uuids(
            driver, uuids, group_id=group_id, batch_size=batch_size
        )

    async def community_node_get_by_uuid(self, _cls, driver, uuid):
        """Return a stored community or raise NodeNotFoundError."""
        return await driver.community_node_ops.get_by_uuid(driver, uuid)

    async def community_node_get_by_uuids(self, _cls, driver, uuids):
        """Return the stored communities among the listed UUIDs."""
        return await driver.community_node_ops.get_by_uuids(driver, uuids)

    async def community_node_get_by_group_ids(
        self, _cls, driver, group_ids, limit=None, uuid_cursor=None
    ):
        """Return communities of the listed groups with UUID cursor paging."""
        return await driver.community_node_ops.get_by_group_ids(
            driver, group_ids, limit=limit, uuid_cursor=uuid_cursor
        )

    async def community_node_load_name_embedding(self, node, driver):
        """Copy the stored name embedding onto the community model."""
        await driver.community_node_ops.load_name_embedding(driver, node)

    async def saga_node_save(self, node, driver):
        """Persist a saga through the driver's saga operations."""
        await driver.saga_node_ops.save(driver, node)

    async def saga_node_delete(self, node, driver):
        """Delete a saga and its HAS_EPISODE edges, keeping the episodes themselves."""
        await driver.saga_node_ops.delete(driver, node)

    async def saga_node_save_bulk(self, _cls, driver, transaction, nodes, batch_size=100):
        """Save sagas on the caller's transaction, accepting model dictionaries."""
        prepared = [SagaNode.model_validate(node) for node in nodes]
        await driver.saga_node_ops.save_bulk(
            driver, prepared, tx=transaction, batch_size=batch_size
        )

    async def saga_node_delete_by_group_id(self, _cls, driver, group_id, batch_size=100):
        """Delete sagas of exactly one group."""
        await driver.saga_node_ops.delete_by_group_id(driver, group_id, batch_size=batch_size)

    async def saga_node_delete_by_uuids(self, _cls, driver, uuids, group_id=None, batch_size=100):
        """Delete listed sagas, keeping an optional group restriction."""
        await driver.saga_node_ops.delete_by_uuids(
            driver, uuids, group_id=group_id, batch_size=batch_size
        )

    async def saga_node_get_by_uuid(self, _cls, driver, uuid):
        """Return a stored saga or raise NodeNotFoundError."""
        return await driver.saga_node_ops.get_by_uuid(driver, uuid)

    async def saga_node_get_by_uuids(self, _cls, driver, uuids):
        """Return the stored sagas among the listed UUIDs."""
        return await driver.saga_node_ops.get_by_uuids(driver, uuids)

    async def saga_node_get_by_group_ids(
        self, _cls, driver, group_ids, limit=None, uuid_cursor=None
    ):
        """Return sagas of the listed groups with UUID cursor paging."""
        return await driver.saga_node_ops.get_by_group_ids(
            driver, group_ids, limit=limit, uuid_cursor=uuid_cursor
        )

    async def saga_get_previous_episode_uuid(self, driver, saga_uuid, current_episode_uuid):
        """Return the saga's latest other episode UUID, or None when it has none."""
        return await driver.saga_node_ops.get_previous_episode_uuid(
            driver, saga_uuid, current_episode_uuid
        )

    async def saga_get_episode_contents(self, driver, saga_uuid, since=None, limit=200):
        """Return (content, valid_at, created_at) for saga episodes created after ``since``."""
        return await driver.saga_node_ops.get_episode_contents(
            driver, saga_uuid, since=since, limit=limit
        )

    async def community_edge_save(self, edge, driver):
        """Persist a HAS_MEMBER edge after checking both endpoints exist."""
        await driver.community_edge_ops.save(driver, edge)

    async def community_edge_delete(self, edge, driver):
        """Delete one HAS_MEMBER edge."""
        await driver.community_edge_ops.delete(driver, edge)

    async def community_edge_delete_by_uuids(self, _cls, driver, uuids, group_id=None):
        """Delete listed HAS_MEMBER edges, keeping an optional group restriction."""
        await driver.community_edge_ops.delete_by_uuids(driver, uuids, group_id=group_id)

    async def community_edge_get_by_uuid(self, _cls, driver, uuid):
        """Return a stored HAS_MEMBER edge or raise EdgeNotFoundError."""
        return await driver.community_edge_ops.get_by_uuid(driver, uuid)

    async def community_edge_get_by_uuids(self, _cls, driver, uuids):
        """Return the stored HAS_MEMBER edges among the listed UUIDs."""
        return await driver.community_edge_ops.get_by_uuids(driver, uuids)

    async def community_edge_get_by_group_ids(
        self, _cls, driver, group_ids, limit=None, uuid_cursor=None
    ):
        """Return HAS_MEMBER edges of the listed groups with UUID cursor paging."""
        return await driver.community_edge_ops.get_by_group_ids(
            driver, group_ids, limit=limit, uuid_cursor=uuid_cursor
        )

    async def has_episode_edge_save(self, edge, driver):
        """Persist a HAS_EPISODE edge after checking saga and episode exist."""
        await driver.has_episode_edge_ops.save(driver, edge)

    async def has_episode_edge_delete(self, edge, driver):
        """Delete one HAS_EPISODE edge."""
        await driver.has_episode_edge_ops.delete(driver, edge)

    async def has_episode_edge_save_bulk(self, _cls, driver, transaction, edges, batch_size=100):
        """Save HAS_EPISODE edges on the caller's transaction, accepting dictionaries."""
        prepared = [HasEpisodeEdge.model_validate(edge) for edge in edges]
        await driver.has_episode_edge_ops.save_bulk(
            driver, prepared, tx=transaction, batch_size=batch_size
        )

    async def has_episode_edge_delete_by_uuids(self, _cls, driver, uuids, group_id=None):
        """Delete listed HAS_EPISODE edges, keeping an optional group restriction."""
        await driver.has_episode_edge_ops.delete_by_uuids(driver, uuids, group_id=group_id)

    async def has_episode_edge_get_by_uuid(self, _cls, driver, uuid):
        """Return a stored HAS_EPISODE edge or raise EdgeNotFoundError."""
        return await driver.has_episode_edge_ops.get_by_uuid(driver, uuid)

    async def has_episode_edge_get_by_uuids(self, _cls, driver, uuids):
        """Return the stored HAS_EPISODE edges among the listed UUIDs."""
        return await driver.has_episode_edge_ops.get_by_uuids(driver, uuids)

    async def has_episode_edge_get_by_group_ids(
        self, _cls, driver, group_ids, limit=None, uuid_cursor=None
    ):
        """Return HAS_EPISODE edges of the listed groups with UUID cursor paging."""
        return await driver.has_episode_edge_ops.get_by_group_ids(
            driver, group_ids, limit=limit, uuid_cursor=uuid_cursor
        )

    async def next_episode_edge_save(self, edge, driver):
        """Persist a NEXT_EPISODE edge after checking both episodes exist."""
        await driver.next_episode_edge_ops.save(driver, edge)

    async def next_episode_edge_delete(self, edge, driver):
        """Delete one NEXT_EPISODE edge."""
        await driver.next_episode_edge_ops.delete(driver, edge)

    async def next_episode_edge_save_bulk(self, _cls, driver, transaction, edges, batch_size=100):
        """Save NEXT_EPISODE edges on the caller's transaction, accepting dictionaries."""
        prepared = [NextEpisodeEdge.model_validate(edge) for edge in edges]
        await driver.next_episode_edge_ops.save_bulk(
            driver, prepared, tx=transaction, batch_size=batch_size
        )

    async def next_episode_edge_delete_by_uuids(self, _cls, driver, uuids, group_id=None):
        """Delete listed NEXT_EPISODE edges, keeping an optional group restriction."""
        await driver.next_episode_edge_ops.delete_by_uuids(driver, uuids, group_id=group_id)

    async def next_episode_edge_get_by_uuid(self, _cls, driver, uuid):
        """Return a stored NEXT_EPISODE edge or raise EdgeNotFoundError."""
        return await driver.next_episode_edge_ops.get_by_uuid(driver, uuid)

    async def next_episode_edge_get_by_uuids(self, _cls, driver, uuids):
        """Return the stored NEXT_EPISODE edges among the listed UUIDs."""
        return await driver.next_episode_edge_ops.get_by_uuids(driver, uuids)

    async def next_episode_edge_get_by_group_ids(
        self, _cls, driver, group_ids, limit=None, uuid_cursor=None
    ):
        """Return NEXT_EPISODE edges of the listed groups with UUID cursor paging."""
        return await driver.next_episode_edge_ops.get_by_group_ids(
            driver, group_ids, limit=limit, uuid_cursor=uuid_cursor
        )

    async def get_mentioned_nodes(self, driver, episodes):
        """Return the distinct entities the given episodes mention."""
        return await driver.graph_ops.get_mentioned_nodes(driver, episodes)

    async def get_communities_by_nodes(self, driver, nodes):
        """Return the distinct communities having any given entity as a member."""
        return await driver.graph_ops.get_communities_by_nodes(driver, nodes)

    async def get_community_clusters(self, driver, group_ids):
        """Return label-propagation entity clusters for the given groups."""
        return await driver.graph_ops.get_community_clusters(driver, group_ids)

    async def remove_communities(self, driver, group_ids=None):
        """Delete communities, optionally only in the given groups."""
        await driver.graph_ops.remove_communities(driver, group_ids=group_ids)

    async def determine_entity_community(self, driver, entity):
        """Return ``(community, is_new)`` for an entity by Graphiti's membership rule."""
        return await driver.graph_ops.determine_entity_community(driver, entity)
