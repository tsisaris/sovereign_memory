"""An end-to-end check that Mem0 stores a memory in a DuckDB vector store and finds it again by search."""

import os
import shutil
import tempfile

import pytest
from mem0 import Memory


@pytest.fixture
def temp_db_dir():
    """Test fixture: make a temporary directory for the database file and remove it afterwards.

    Comes out: the directory path. It never touches any other path.
    """
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def memory(temp_db_dir):
    """Test fixture: build a Memory configured with a small DuckDB vector store in the temporary directory.

    Goes in: the directory; comes out: the memory object. It never touches a real database.
    """
    config = {
        "vector_store": {
            "provider": "duckdb",
            "config": {
                "collection_name": "test_mem0_e2e",
                "embedding_model_dims": 3,
                "db_path": os.path.join(temp_db_dir, "vector.duckdb"),
            },
        },
        "history_db_path": os.path.join(temp_db_dir, "history.duckdb"),
        "enable_update_delete_branches": True,
        # Mock embedder and LLM to return static values
        "embedder": {
            "provider": "openai",
            "config": {"api_key": "fake_key", "model": "text-embedding-3-small"},
        },
        "llm": {"provider": "openai", "config": {"api_key": "fake_key", "model": "gpt-4o-mini"}},
    }

    # We will use mock embedder/llm inside the test to bypass network since models are not local
    mem = Memory.from_config(config)

    # Simple Mocking
    mem.embedding_model.embed = lambda text, purpose="add": [0.1, 0.2, 0.3]
    mem.embedding_model.embed_batch = lambda texts, purpose="add": [[0.1, 0.2, 0.3]] * len(texts)

    # Mock LLM to return an ADD action for "I like apples"
    def mock_generate_response(*args, **kwargs):
        return '{"memory": [{"id": "1", "text": "User likes apples", "event": "ADD"}]}'

    mem.llm.generate_response = mock_generate_response

    yield mem


def test_memory_add_and_search(memory):
    """Check that adding one memory for a user and searching for it returns that memory.

    It reads and writes only the temporary database.
    """
    memory.add("I like apples", user_id="alice")

    results = memory.search("apples", filters={"user_id": "alice"})
    assert len(results) >= 1
    print("results:", results)
    results = results["results"]
    assert "apples" in results[0]["memory"]
