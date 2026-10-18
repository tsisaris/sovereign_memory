"""Share native storage rules for Graphiti's community, saga and episode-link families.

Each family is one label in the existing ``graphiti.graph_node`` or ``graphiti.graph_edge``
table, stored as the maintained Pydantic model's JSON. Nothing here opens a connection,
commits a borrowed transaction or invents a record that storage does not hold.
"""

from graphiti_core.errors import EdgeNotFoundError, NodeNotFoundError

from .entity_node_ops import _write_transaction


async def read_models(executor, table, label, model, predicate, parameters, **page):
    """Load stored models of one label with bound filtering and descending UUID pages.

    ``predicate`` is trusted SQL written in this package; ``parameters`` are bound values.
    ``page`` may hold ``limit`` and ``uuid_cursor`` with the maintained cursor meaning:
    only UUIDs strictly below the cursor, newest UUID first. Returns a list, never a
    fabricated model.
    """
    query = f"SELECT properties FROM graphiti.{table} WHERE label = ? AND ({predicate})"
    parameters = [label, *parameters]
    if page.get("uuid_cursor"):
        query += " AND uuid < ?"
        parameters.append(page["uuid_cursor"])
    query += " ORDER BY uuid DESC"
    if page.get("limit") is not None:
        query += " LIMIT ?"
        parameters.append(page["limit"])
    records, _, _ = await executor.execute_query(query, parameters=parameters)
    return [model.model_validate_json(record["properties"]) for record in records]


class NodeFamilyOps:
    """Store one non-entity node family and detach its incident edges on deletion.

    Subclasses set ``label`` and ``model``. Every mutation runs on the caller's
    transaction when one is supplied, otherwise on one transaction the driver owns.
    It never overwrites a UUID that belongs to another node family.
    """

    label: str
    model: type

    def _require(self, node):
        """Refuse a model of the wrong family instead of storing it under this label."""
        if not isinstance(node, self.model):
            raise TypeError(f"{self.label} storage requires a {self.model.__name__}")

    async def save(self, executor, node, tx=None):
        """Insert or update the node, raising when its UUID belongs to another family."""
        self._require(node)
        async with _write_transaction(executor, tx) as transaction:
            records, _, _ = await transaction.run(
                "INSERT INTO graphiti.graph_node (uuid, label, properties) VALUES (?, ?, ?) "
                "ON CONFLICT (uuid) DO UPDATE SET properties = excluded.properties "
                "WHERE graph_node.label = excluded.label RETURNING uuid",
                parameters=[node.uuid, self.label, node.model_dump_json()],
            )
            if not records:
                raise ValueError(f"Node UUID {node.uuid} belongs to a different node family")

    async def save_bulk(self, executor, nodes, tx=None, batch_size=100):
        """Save every node on one transaction so a failure stores none of them."""
        async with _write_transaction(executor, tx) as transaction:
            for node in nodes:
                await self.save(executor, node, tx=transaction)

    async def _delete_matching(self, executor, predicate, parameters, tx):
        """Delete selected nodes of this family and every edge touching them, atomically."""
        selected = (
            f"SELECT uuid FROM graphiti.graph_node WHERE label = '{self.label}' AND ({predicate})"
        )
        async with _write_transaction(executor, tx) as transaction:
            await transaction.run(
                f"DELETE FROM graphiti.graph_edge WHERE source_node_uuid IN ({selected}) "
                f"OR target_node_uuid IN ({selected})",
                parameters=parameters + parameters,
            )
            await transaction.run(
                f"DELETE FROM graphiti.graph_node WHERE label = '{self.label}' AND ({predicate})",
                parameters=parameters,
            )

    async def delete(self, executor, node, tx=None):
        """Delete this node and its incident edges, leaving its neighbours in place."""
        self._require(node)
        await self._delete_matching(executor, "uuid = ?", [node.uuid], tx)

    async def delete_all(self, executor, tx=None):
        """Delete every node of this family in every group, with its incident edges."""
        await self._delete_matching(executor, "TRUE", [], tx)

    async def delete_by_group_id(self, executor, group_id, tx=None, batch_size=100):
        """Delete only this family's nodes in exactly the given group."""
        await self._delete_matching(
            executor, "json_extract_string(properties, 'group_id') = ?", [group_id], tx
        )

    async def delete_by_uuids(self, executor, uuids, tx=None, batch_size=100, group_id=None):
        """Delete the listed UUIDs of this family, optionally only inside one group."""
        predicate = "uuid IN (SELECT unnest(?::VARCHAR[]))"
        parameters = [list(uuids)]
        if group_id is not None:
            predicate += " AND json_extract_string(properties, 'group_id') = ?"
            parameters.append(group_id)
        await self._delete_matching(executor, predicate, parameters, tx)

    async def get_by_uuid(self, executor, uuid):
        """Return the stored node or raise Graphiti's NodeNotFoundError."""
        nodes = await self.get_by_uuids(executor, [uuid])
        if not nodes:
            raise NodeNotFoundError(uuid)
        return nodes[0]

    async def get_by_uuids(self, executor, uuids):
        """Return the stored nodes among the listed UUIDs; missing ones are simply absent."""
        return await read_models(
            executor,
            "graph_node",
            self.label,
            self.model,
            "uuid IN (SELECT unnest(?::VARCHAR[]))",
            [list(uuids)],
        )

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        """Return nodes of the listed groups, newest UUID first, honouring cursor and limit."""
        return await read_models(
            executor,
            "graph_node",
            self.label,
            self.model,
            "json_extract_string(properties, 'group_id') IN (SELECT unnest(?::VARCHAR[]))",
            [list(group_ids)],
            limit=limit,
            uuid_cursor=uuid_cursor,
        )


class EdgeFamilyOps:
    """Store one non-entity edge family between nodes of the labels it allows.

    Subclasses set ``label``, ``model``, ``source_labels`` and ``target_labels``.
    Saving checks inside the same transaction that both endpoints exist with an allowed
    label, and never overwrites a UUID owned by another edge family.
    """

    label: str
    model: type
    source_labels: tuple[str, ...]
    target_labels: tuple[str, ...]

    def _require(self, edge):
        """Refuse a model of the wrong family instead of storing it under this label."""
        if not isinstance(edge, self.model):
            raise TypeError(f"{self.label} storage requires a {self.model.__name__}")

    async def save(self, executor, edge, tx=None):
        """Upsert the edge after proving both endpoints exist with allowed labels."""
        self._require(edge)
        async with _write_transaction(executor, tx) as transaction:
            for uuid, labels in (
                (edge.source_node_uuid, self.source_labels),
                (edge.target_node_uuid, self.target_labels),
            ):
                nodes, _, _ = await transaction.run(
                    "SELECT uuid FROM graphiti.graph_node "
                    "WHERE uuid = ? AND label IN (SELECT unnest(?::VARCHAR[]))",
                    parameters=[uuid, list(labels)],
                )
                if not nodes:
                    raise NodeNotFoundError(uuid)
            records, _, _ = await transaction.run(
                "INSERT INTO graphiti.graph_edge "
                "(uuid, source_node_uuid, target_node_uuid, label, properties) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (uuid) DO UPDATE SET "
                "source_node_uuid = excluded.source_node_uuid, "
                "target_node_uuid = excluded.target_node_uuid, properties = excluded.properties "
                "WHERE graph_edge.label = excluded.label RETURNING uuid",
                parameters=[
                    edge.uuid,
                    edge.source_node_uuid,
                    edge.target_node_uuid,
                    self.label,
                    edge.model_dump_json(),
                ],
            )
            if not records:
                raise ValueError(f"Edge UUID {edge.uuid} belongs to a different edge family")

    async def save_bulk(self, executor, edges, tx=None, batch_size=100):
        """Save every edge on one transaction so a failure stores none of them."""
        async with _write_transaction(executor, tx) as transaction:
            for edge in edges:
                await self.save(executor, edge, tx=transaction)

    async def delete(self, executor, edge, tx=None):
        """Delete only this edge, leaving both endpoint nodes in place."""
        self._require(edge)
        await self.delete_by_uuids(executor, [edge.uuid], tx=tx)

    async def delete_by_uuids(self, executor, uuids, tx=None, group_id=None):
        """Delete the listed UUIDs of this family, optionally only inside one group."""
        query = (
            "DELETE FROM graphiti.graph_edge WHERE label = ? "
            "AND uuid IN (SELECT unnest(?::VARCHAR[]))"
        )
        parameters = [self.label, list(uuids)]
        if group_id is not None:
            query += " AND json_extract_string(properties, 'group_id') = ?"
            parameters.append(group_id)
        async with _write_transaction(executor, tx) as transaction:
            await transaction.run(query, parameters=parameters)

    async def get_by_uuid(self, executor, uuid):
        """Return the stored edge or raise Graphiti's EdgeNotFoundError."""
        edges = await self.get_by_uuids(executor, [uuid])
        if not edges:
            raise EdgeNotFoundError(uuid)
        return edges[0]

    async def get_by_uuids(self, executor, uuids):
        """Return the stored edges among the listed UUIDs; missing ones are simply absent."""
        return await read_models(
            executor,
            "graph_edge",
            self.label,
            self.model,
            "uuid IN (SELECT unnest(?::VARCHAR[]))",
            [list(uuids)],
        )

    async def get_by_group_ids(self, executor, group_ids, limit=None, uuid_cursor=None):
        """Return edges of the listed groups, newest UUID first, honouring cursor and limit."""
        return await read_models(
            executor,
            "graph_edge",
            self.label,
            self.model,
            "json_extract_string(properties, 'group_id') IN (SELECT unnest(?::VARCHAR[]))",
            [list(group_ids)],
            limit=limit,
            uuid_cursor=uuid_cursor,
        )
