"""Persist Graphiti entities through its maintained operation and transaction contracts."""

import json
from contextlib import asynccontextmanager

from graphiti_core.driver.operations.entity_node_ops import EntityNodeOperations
from graphiti_core.errors import NodeNotFoundError
from graphiti_core.nodes import EntityNode


@asynccontextmanager
async def _write_transaction(executor, transaction):
    """Borrow a supplied transaction or let the driver own one complete mutation."""
    if transaction is not None:
        yield transaction
    else:
        async with executor.transaction() as owned:
            yield owned


class DuckDBEntityNodeOps(EntityNodeOperations):
    """Store actual entity models and detach incident edges within the Graphiti schema."""

    async def save(self, executor, node, tx=None):
        """Insert or update an entity, refusing a UUID already owned by another family."""
        if not isinstance(node, EntityNode):
            raise TypeError("Entity storage requires an EntityNode")
        properties = node.model_dump(mode="json")
        properties["labels"] = list(dict.fromkeys([*node.labels, "Entity"]))
        async with _write_transaction(executor, tx) as transaction:
            records, _, _ = await transaction.run(
                "INSERT INTO graphiti.graph_node (uuid, label, properties) VALUES (?, 'Entity', ?) "
                "ON CONFLICT (uuid) DO UPDATE SET properties = excluded.properties "
                "WHERE graph_node.label = 'Entity' RETURNING uuid",
                parameters=[node.uuid, json.dumps(properties)],
            )
            if not records:
                raise ValueError(f"Node UUID {node.uuid} belongs to a different node family")

    async def save_bulk(self, executor, nodes, tx=None, batch_size=100):
        """Save all supplied entities on the caller's transaction or one owned transaction."""
        async with _write_transaction(executor, tx) as transaction:
            for node in nodes:
                await self.save(executor, node, tx=transaction)

    async def _delete_matching(self, executor, predicate, parameters, tx):
        """Detach only selected entities and their incident edges in one transaction."""
        selected = "SELECT uuid FROM graphiti.graph_node WHERE label = 'Entity' AND " + predicate
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
                "DELETE FROM graphiti.graph_node WHERE label = 'Entity' AND " + predicate,
                parameters=parameters,
            )

    async def delete(self, executor, node, tx=None):
        """Remove the concrete entity and its incident edges without touching other families."""
        if not isinstance(node, EntityNode):
            raise TypeError("Entity storage requires an EntityNode")
        await self._delete_matching(executor, "uuid = ?", [node.uuid], tx)

    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100):
        """Delete only entities in the requested group and their incident edges."""
        await self._delete_matching(
            executor, "json_extract_string(properties, 'group_id') = ?", [group_id], tx
        )

    async def delete_by_uuids(self, executor, uuids, tx=None, batch_size=100, group_id=None):
        """Delete selected entity UUIDs, retaining an optional caller group restriction."""
        predicate = "uuid IN (SELECT unnest(?))"
        parameters = [uuids]
        if group_id is not None:
            predicate += " AND json_extract_string(properties, 'group_id') = ?"
            parameters.append(group_id)
        await self._delete_matching(executor, predicate, parameters, tx)

    async def get_by_uuid(self, executor, uuid):
        """Load the actual maintained entity model or raise its missing-node exception."""
        nodes = await self.get_by_uuids(executor, [uuid])
        if not nodes:
            raise NodeNotFoundError(uuid)
        return nodes[0]

    async def get_by_uuids(self, executor, uuids, group_id=None):
        """Load only matching entity UUIDs, optionally constrained to one group."""
        query = (
            "SELECT properties FROM graphiti.graph_node WHERE label = 'Entity' "
            "AND uuid IN (SELECT unnest(?))"
        )
        parameters = [uuids]
        if group_id is not None:
            query += " AND json_extract_string(properties, 'group_id') = ?"
            parameters.append(group_id)
        records, _, _ = await executor.execute_query(query, parameters=parameters)
        return [EntityNode.model_validate_json(record["properties"]) for record in records]

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        """Read groups with the maintained descending UUID cursor and optional limit."""
        query = (
            "SELECT properties FROM graphiti.graph_node WHERE label = 'Entity' "
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
        return [EntityNode.model_validate_json(record["properties"]) for record in records]

    async def get_by_names(self, executor, names, group_ids):
        """Find exact nonempty names only within the explicitly requested groups."""
        names = sorted({name for name in names if name})
        group_ids = sorted({group for group in group_ids if group})
        records, _, _ = await executor.execute_query(
            "SELECT properties FROM graphiti.graph_node WHERE label = 'Entity' "
            "AND json_extract_string(properties, 'name') IN (SELECT unnest(?)) "
            "AND json_extract_string(properties, 'group_id') IN (SELECT unnest(?))",
            parameters=[names, group_ids],
        )
        return [EntityNode.model_validate_json(record["properties"]) for record in records]

    async def load_embeddings(self, executor, node):
        """Load the stored embedding without generating or substituting model output."""
        node.name_embedding = (await self.get_by_uuid(executor, node.uuid)).name_embedding

    async def load_embeddings_bulk(self, executor, nodes, batch_size=100):
        """Populate embeddings for existing entities, leaving missing nodes unchanged."""
        await self.load_embedding_map(executor, nodes)

    async def load_embedding_map(self, executor, nodes):
        """Populate requested entities and return only embeddings actually present in storage."""
        stored = await self.get_by_uuids(executor, [node.uuid for node in nodes])
        embeddings = {node.uuid: node.name_embedding for node in stored}
        for node in nodes:
            if node.uuid in embeddings:
                node.name_embedding = embeddings[node.uuid]
        return embeddings
