"""The vector-store provider choice for Mem0's configuration and the check that turns it into the matching provider settings.

It holds and validates values only and opens no store.
"""

from typing import Dict, Optional

from pydantic import BaseModel, Field, model_validator


class VectorStoreConfig(BaseModel):
    """The vector-store part of Mem0's configuration: a provider name (default duckdb) and that provider's settings.

    Goes in: a provider and a settings dict; comes out: a validated object. It never connects to a
    store.
    """

    provider: str = Field(
        description="Provider of the vector store (e.g., 'qdrant', 'chroma', 'upstash_vector')",
        default="duckdb",
    )
    config: Optional[Dict] = Field(
        description="Configuration for the specific vector store", default=None
    )

    _provider_configs: Dict[str, str] = {
        "duckdb": "DuckDBVectorStoreConfig",
        "qdrant": "QdrantConfig",
        "chroma": "ChromaDbConfig",
        "pgvector": "PGVectorConfig",
        "pinecone": "PineconeConfig",
        "mongodb": "MongoDBConfig",
        "milvus": "MilvusDBConfig",
        "baidu": "BaiduDBConfig",
        "cassandra": "CassandraConfig",
        "neptune": "NeptuneAnalyticsConfig",
        "upstash_vector": "UpstashVectorConfig",
        "azure_ai_search": "AzureAISearchConfig",
        "azure_mysql": "AzureMySQLConfig",
        "redis": "RedisDBConfig",
        "valkey": "ValkeyConfig",
        "databricks": "DatabricksConfig",
        "elasticsearch": "ElasticsearchConfig",
        "vertex_ai_vector_search": "GoogleMatchingEngineConfig",
        "opensearch": "OpenSearchConfig",
        "supabase": "SupabaseConfig",
        "weaviate": "WeaviateConfig",
        "faiss": "FAISSConfig",
        "langchain": "LangchainConfig",
        "s3_vectors": "S3VectorsConfig",
        "turbopuffer": "TurbopufferConfig",
        "oracledb": "OracleAIVectorSearchConfig",
    }

    @model_validator(mode="after")
    def validate_and_create_config(self) -> "VectorStoreConfig":
        """Check the provider is supported and turn the settings dict into that provider's settings object.

        Goes in: nothing but the object's own fields; comes out: the same object with settings
        validated. It raises ValueError for an unsupported provider and for settings the provider
        does not accept.
        """
        provider = self.provider
        config = self.config

        if provider not in self._provider_configs:
            raise ValueError(f"Unsupported vector store provider: {provider}")

        module = __import__(
            f"mem0.configs.vector_stores.{provider}",
            fromlist=[self._provider_configs[provider]],
        )
        config_class = getattr(module, self._provider_configs[provider])

        if config is None:
            config = {}

        if not isinstance(config, dict):
            if not isinstance(config, config_class):
                raise ValueError(f"Invalid config type for provider {provider}")
            return self

        # also check if path in allowed kays for pydantic model, and whether config extra fields are allowed
        if "path" not in config and "path" in config_class.__annotations__:
            config["path"] = f"/tmp/{provider}"

        self.config = config_class(**config)
        return self
