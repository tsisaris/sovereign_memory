"""Checks for the DuckDB vector store: inserting and searching vectors, keyword search and deleting.

Each check uses its own temporary database file.
"""

import uuid

import pytest
from mem0.vector_stores.duckdb import DuckDBVectorStore


@pytest.fixture
def temp_db_path(tmp_path):
    """Test fixture: give the path of a database file inside pytest's temporary directory.

    Comes out: the path as a string. It creates no file itself.
    """
    return str(tmp_path / "test_vector_store.duckdb")


@pytest.fixture
def vector_store(temp_db_path):
    """Test fixture: open a DuckDB vector store of width 3 on the temporary path and close its connection afterwards.

    Comes out: the store. It never touches a real database.
    """
    store = DuckDBVectorStore(
        db_path=temp_db_path, collection_name="test_mem0", embedding_model_dims=3
    )
    yield store
    if hasattr(store, "connection") and store.connection:
        store.connection.close()


def test_insert_and_search(vector_store):
    """Check that two vectors can be inserted and the nearest one comes back first from a vector search."""
    vectors = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    payloads = [{"name": "item1"}, {"name": "item2"}]
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    vector_store.insert(vectors, payloads, ids)

    # Search
    results = vector_store.search(query="test", vectors=[0.1, 0.2, 0.3], top_k=1)

    assert len(results) == 1
    assert results[0].id == ids[0]
    assert results[0].payload["name"] == "item1"


def test_keyword_search(vector_store):
    """Check that a keyword search finds the record whose text contains the word."""
    vectors = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    payloads = [{"name": "item1", "text": "apple"}, {"name": "item2", "text": "banana"}]
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    vector_store.insert(vectors, payloads, ids)

    # Keyword Search
    results = vector_store.keyword_search(query="apple", top_k=1)

    assert len(results) == 1
    assert results[0].id == ids[0]
    assert results[0].payload["name"] == "item1"


def test_delete(vector_store):
    """Check that a deleted vector can no longer be fetched by its id."""
    vectors = [[0.1, 0.2, 0.3]]
    payloads = [{"name": "item1"}]
    ids = [str(uuid.uuid4())]

    vector_store.insert(vectors, payloads, ids)
    vector_store.delete(ids[0])

    result = vector_store.get(ids[0])
    assert result is None
