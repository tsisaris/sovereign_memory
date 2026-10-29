"""Find the entities around a list of events.

Translated unchanged from the removed Cognee file `cognee/tasks/temporal_graph/enrich_events.py`
(upstream commit 1513b1f41^). It sends each event's name and description to the language model
and gets back the entities that take part. It does not touch the graph.
"""

import json

from cognee.infrastructure.llm.extraction.extract_event_entities import extract_event_entities
from cognee.modules.engine.models.Event import Event
from cognee.tasks.temporal_graph.models import EventEntityList, EventWithEntities


async def enrich_events(events: list[Event]) -> list[EventWithEntities]:
    """Return, for each event, the entities the language model finds around it.

    It is translated from `enrich_events` in `cognee/tasks/temporal_graph/enrich_events.py`.
    It takes a list of Event nodes, sends their names and descriptions as JSON in one request,
    and gives back one record per event in the same order. It never changes the events.
    """
    events_json = [
        {"event_name": event.name, "description": event.description or ""} for event in events
    ]

    events_json_str = json.dumps(events_json)

    entity_result = await extract_event_entities(events_json_str, EventEntityList)

    return entity_result.events
