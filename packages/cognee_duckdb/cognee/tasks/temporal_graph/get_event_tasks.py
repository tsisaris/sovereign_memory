"""Build the list of tasks that turns documents into an event-based temporal graph.

Translated from `get_temporal_tasks` in the removed Cognee file `cognee/api/v1/cognify/cognify.py`
(upstream commit 1513b1f41^). Cognee removed the `temporal_cognify` switch that selected this
list, and it is not brought back; the list is built here and handed to a pipeline by the caller.
The removed function fell back to 10 chunks per batch when nothing was set; here a missing
setting is an error, because no tunable number is written into code.
"""

from cognee.infrastructure.llm import resolve_chunk_size
from cognee.modules.chunking.TextChunker import TextChunker
from cognee.modules.cognify.config import get_cognify_config
from cognee.modules.pipelines.tasks.task import Task
from cognee.tasks.documents import classify_documents, extract_chunks_from_documents
from cognee.tasks.storage import add_data_points
from cognee.tasks.temporal_graph.extract_events_and_entities import extract_events_and_timestamps
from cognee.tasks.temporal_graph.extract_knowledge_graph_from_events import (
    extract_knowledge_graph_from_events,
)


async def get_event_tasks(
    chunker=TextChunker,
    chunk_size: int | None = None,
    chunks_per_batch: int | None = None,
) -> list[Task]:
    """Return the tasks that classify, chunk, find events, find their entities and store them.

    It is translated from `get_temporal_tasks` in `cognee/api/v1/cognify/cognify.py`. It takes
    an optional chunker, chunk size and chunks per batch; the batch size comes from Cognee's
    cognify setting `chunks_per_batch` when not given. It gives back the five tasks in order
    and never picks a batch size itself: it raises ValueError when none is set.
    """
    if chunks_per_batch is None:
        chunks_per_batch = get_cognify_config().chunks_per_batch
    if chunks_per_batch is None:
        raise ValueError("chunks_per_batch is not set in the cognify settings and was not given")

    return [
        Task(classify_documents, needs_llm=False),
        Task(
            extract_chunks_from_documents,
            max_chunk_size=await resolve_chunk_size(chunk_size),
            chunker=chunker,
            needs_llm=False,
        ),
        Task(extract_events_and_timestamps, task_config={"batch_size": chunks_per_batch}),
        Task(extract_knowledge_graph_from_events),
        Task(add_data_points, task_config={"batch_size": chunks_per_batch}, needs_llm=False),
    ]
