"""Add the entities around each event of some chunks, so events join the knowledge graph.

Translated unchanged from the removed Cognee file
`cognee/tasks/temporal_graph/extract_knowledge_graph_from_events.py` (upstream commit
1513b1f41^). It gathers every Event node from the chunks, has the language model name the
entities around them, and attaches those. It does not find new events.
"""

from cognee.modules.chunking.models import DocumentChunk
from cognee.modules.engine.models.Event import Event
from cognee.modules.pipelines.tasks.task import task_summary
from cognee.tasks.temporal_graph.add_entities_to_event import add_entities_to_event
from cognee.tasks.temporal_graph.enrich_events import enrich_events


@task_summary("Built graph from {n} event(s)")
async def extract_knowledge_graph_from_events(
    data_chunks: list[DocumentChunk],
) -> list[DocumentChunk]:
    """Attach the entities around every event found in the given chunks.

    It is translated from `extract_knowledge_graph_from_events` in
    `cognee/tasks/temporal_graph/extract_knowledge_graph_from_events.py`. It takes document
    chunks whose `contains` lists hold Event nodes, sends all those events to the language
    model in one request, and appends the entities it names to each event by the relationship
    it names. It gives back the same chunks and never calls the model when no chunk has an
    event. Where the removed task silently paired as many events as the model answered for,
    this one raises ValueError when the model answers for a different number of events.
    """
    all_events = []
    for chunk in data_chunks:
        for item in chunk.contains or []:
            if isinstance(item, Event):
                all_events.append(item)

    if not all_events:
        return data_chunks

    enriched_events = await enrich_events(all_events)

    for event, enriched_event in zip(all_events, enriched_events, strict=True):
        add_entities_to_event(event, enriched_event)

    return data_chunks
