"""Settings for Mem0's DuckDB vector store: the collection name, the vector width and the database file path.

It holds values only and never opens the database.
"""

from pydantic import BaseModel, Field


class DuckDBVectorStoreConfig(BaseModel):
    """The validated settings of the DuckDB vector store: collection name, embedding width and database path, each with a default.

    Goes in: keyword values; comes out: a checked settings object. It never opens a file or a
    connection.
    """

    collection_name: str = Field(default="mem0", description="Name of the collection")
    embedding_model_dims: int = Field(default=1536, description="Dimensions of the embedding model")
    db_path: str = Field(
        default="vector_store.duckdb", description="Path to the DuckDB database file"
    )
