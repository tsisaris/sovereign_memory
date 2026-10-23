"""Find the events and their times in document chunks.

Translated unchanged from the removed Cognee file
`cognee/tasks/temporal_graph/extract_events_and_entities.py` (upstream commit 1513b1f41^). Each
chunk is read by the language model, and every event found becomes an Event node inside the
chunk. It does not look for the entities around the events.
"""

import asyncio

from cognee.infrastructure.llm.extraction.knowledge_graph.extract_event_graph import (
    extract_event_graph,
)
from cognee.modules.chunking.models import DocumentChunk
from cognee.modules.engine.utils.generate_event_datapoint import generate_event_datapoint
from cognee.modules.pipelines.tasks.task import task_summary
from cognee.tasks.temporal_graph.models import EventList


@task_summary("Extracted events from {n} chunk(s)")
async def extract_events_and_timestamps(data_chunks: list[DocumentChunk]) -> list[DocumentChunk]:
    """Add the events found in each chunk's text to that chunk.

    It is translated from `extract_events_and_timestamps` in
    `cognee/tasks/temporal_graph/extract_events_and_entities.py`. It takes document chunks,
    asks the language model for the events of every chunk at once, turns each into an Event
    node with `generate_event_datapoint` and appends it to the chunk's `contains` list. It gives
    back the same chunks and never removes anything a chunk already contained.
    """
    events = await asyncio.gather(
        *[extract_event_graph(chunk.text, EventList) for chunk in data_chunks]
    )

    for data_chunk, event_list in zip(data_chunks, events):
        for event in event_list.events:
            event_datapoint = generate_event_datapoint(event)
            if data_chunk.contains is None:
                data_chunk.contains = []
            data_chunk.contains.append(event_datapoint)

    return data_chunks
