"""Attach found entities to an Event node, each by a named relationship.

Translated unchanged from the removed Cognee file
`cognee/tasks/temporal_graph/add_entities_to_event.py` (upstream commit 1513b1f41^). Entities
and entity types are made as ordinary Cognee nodes with deterministic ids, so the same name
found twice is one node. It only changes the event it is given.
"""

from cognee.infrastructure.engine.models.Edge import Edge
from cognee.modules.engine.models.Entity import Entity
from cognee.modules.engine.models.EntityType import EntityType
from cognee.modules.engine.models.Event import Event
from cognee.modules.engine.utils.generate_node_name import generate_node_name
from cognee.tasks.temporal_graph.models import EventWithEntities


def add_entities_to_event(event: Event, event_with_entities: EventWithEntities) -> None:
    """Append each found entity to the event's attributes with its relationship edge.

    It is translated from `add_entities_to_event` in
    `cognee/tasks/temporal_graph/add_entities_to_event.py`. It takes an Event node and the
    entities found for it; for each it makes (or reuses by type name) an EntityType, an Entity
    and an Edge carrying the relationship word, and appends the pair to the event. It gives back
    nothing and never touches an event that has no found entities.
    """
    if not event_with_entities.attributes:
        return

    entity_types = {}

    for attribute in event_with_entities.attributes:
        entity_type = get_or_create_entity_type(entity_types, attribute.entity_type)

        entity_id = Entity.id_for(attribute.entity)
        entity_name = generate_node_name(attribute.entity)
        entity = Entity(
            id=entity_id,
            name=entity_name,
            is_a=entity_type,
            description=f"Entity {attribute.entity} of type {attribute.entity_type}",
            ontology_valid=False,
            belongs_to_set=None,
        )

        edge = Edge(relationship_type=attribute.relationship)

        if event.attributes is None:
            event.attributes = []
        event.attributes.append((edge, [entity]))


def get_or_create_entity_type(entity_types: dict, entity_type_name: str) -> EntityType:
    """Return the EntityType node for a type name, making it the first time the name is seen.

    It is translated from `get_or_create_entity_type` in
    `cognee/tasks/temporal_graph/add_entities_to_event.py`. It takes a cache of the types made
    so far and a type name, makes a node with a deterministic id and the normalised name when
    the name is new, and gives back the cached node. It never renames or removes a cached type.
    """
    if entity_type_name not in entity_types:
        type_id = EntityType.id_for(entity_type_name)
        type_name = generate_node_name(entity_type_name)
        entity_type = EntityType(
            id=type_id,
            name=type_name,
            type=type_name,
            description=f"Type for {entity_type_name}",
            ontology_valid=False,
        )
        entity_types[entity_type_name] = entity_type

    return entity_types[entity_type_name]
