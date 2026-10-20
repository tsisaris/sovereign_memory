"""Tell whether any chunk of a document is missing from, or changed in, the vector collection.

Goes in: the chunks of one document and the collection name; comes out: whether any chunk is
new or has different text. It never writes to the collection.
"""

from cognee.infrastructure.databases.vector import get_vector_engine_async
from cognee.modules.chunking.models import DocumentChunk


async def has_new_chunks(data_chunks: list[DocumentChunk], collection_name: str) -> bool:
    """Say whether at least one chunk is new or has changed text compared with the collection.

    Goes in: the chunks of one document and the name of the vector collection to compare with;
    comes out: True when the collection is missing, holds none of the chunks, or holds a chunk
    whose text differs, otherwise False. It never writes to the collection.
    """
    vector_engine = await get_vector_engine_async()

    if not await vector_engine.has_collection(collection_name):
        # There is no collection created,
        # so no existing chunks, all chunks are new.
        return True

    existing_chunks = await vector_engine.retrieve(
        collection_name,
        [str(chunk.chunk_id) for chunk in data_chunks],
    )

    if len(existing_chunks) == 0:
        # If we don't find any existing chunk,
        # all chunks are new.
        return True

    existing_chunks_map = {chunk.id: chunk.payload for chunk in existing_chunks}

    new_data_chunks = [
        chunk
        for chunk in data_chunks
        if chunk.chunk_id not in existing_chunks_map
        or chunk.text != existing_chunks_map[chunk.chunk_id]["text"]
    ]

    return len(new_data_chunks) > 0
