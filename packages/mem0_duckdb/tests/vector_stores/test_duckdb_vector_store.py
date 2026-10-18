import os
import uuid

import pytest
from mem0.vector_stores.duckdb import DuckDBVectorStore


@pytest.fixture
def temp_db_path(tmp_path):
    return str(tmp_path / "test_vector_store.duckdb")


@pytest.fixture
def vector_store(temp_db_path):
    store = DuckDBVectorStore(
        db_path=temp_db_path, collection_name="test_mem0", embedding_model_dims=3
    )
    yield store
    if hasattr(store, "connection") and store.connection:
        store.connection.close()


def test_insert_and_search(vector_store):
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
    vectors = [[0.1, 0.2, 0.3]]
    payloads = [{"name": "item1"}]
    ids = [str(uuid.uuid4())]

    vector_store.insert(vectors, payloads, ids)
    vector_store.delete(ids[0])

    result = vector_store.get(ids[0])
    assert result is None
