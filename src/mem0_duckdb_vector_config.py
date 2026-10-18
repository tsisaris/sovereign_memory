from typing import Dict, Optional
from pydantic import BaseModel, Field

class DuckDBVectorStoreConfig(BaseModel):
    collection_name: str = Field(default="mem0", description="Name of the collection")
    embedding_model_dims: int = Field(default=1536, description="Dimensions of the embedding model")
    db_path: str = Field(default="vector_store.duckdb", description="Path to the DuckDB database file")
