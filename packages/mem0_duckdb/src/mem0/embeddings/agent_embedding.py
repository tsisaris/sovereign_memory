"""Adapt Sovereign Agent Embedding to Mem0 and the Owner's Cognee embedding seam.

Transport belongs to Sovereign's shared AgentClient. This adapter only builds
passage requests and checks served identity and vectors; it never boots agents,
downloads models, stores vectors or substitutes another provider.
"""

import asyncio
import importlib
import math
from functools import cached_property
from typing import Any, List, Optional

from mem0.configs.embeddings.base import BaseEmbedderConfig
from mem0.embeddings.base import EmbeddingBase


class AgentEmbedding(EmbeddingBase):
    """Use one declared capability through an existing, lazily constructed AgentClient."""

    provider = "agent_embedding"
    api_version = None

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        """Validate explicit model, revision, width and client settings without any I/O."""
        super().__init__(config)
        options = self.config.agent_embedding
        if not isinstance(options, dict):
            raise ValueError("agent_embedding must contain the shared client settings")
        required = {
            "revision",
            "capability_id",
            "agent_id",
            "port",
            "bootstrap_config",
            "requester_agent_id",
        }
        if set(options) != required:
            raise ValueError(f"agent_embedding requires exactly {sorted(required)}")
        for name in ("revision", "capability_id", "agent_id", "requester_agent_id"):
            if not isinstance(options[name], str) or not options[name]:
                raise ValueError(f"agent_embedding.{name} must be a non-empty string")
        if type(options["port"]) is not int or not 0 < options["port"] < 65536:
            raise ValueError("agent_embedding.port must be a valid positive integer port")
        if not isinstance(options["bootstrap_config"], dict):
            raise ValueError("agent_embedding.bootstrap_config must be a shared AgentClient table")
        if not isinstance(self.config.model, str) or not self.config.model:
            raise ValueError("Agent Embedding requires an explicit model")
        if type(self.config.embedding_dims) is not int or self.config.embedding_dims <= 0:
            raise ValueError("Agent Embedding requires positive integer embedding_dims")
        self.model = self.config.model
        self.dimensions = self.config.embedding_dims
        self.revision = options["revision"]
        self.capability_id = options["capability_id"]
        self._options = options.copy()

    @cached_property
    def client(self) -> Any:
        """Resolve the installed Sovereign shared client on first model work, never boot it."""
        shared = importlib.import_module("shared_agent_client")
        return shared.AgentClient(
            agent_id=self._options["agent_id"],
            port=self._options["port"],
            bootstrap_config=self._options["bootstrap_config"],
        )

    def embed(self, text: str, memory_action=None) -> List[float]:
        """Embed one text with the selected capability; never infer a query prefix or model."""
        return self.embed_batch([text], memory_action)[0]

    def embed_batch(self, texts, memory_action="add") -> List[List[float]]:
        """Post exact passages and reject mismatched identity, ids, widths or nonfinite numbers."""
        passages = [{"id": str(index), "text": text} for index, text in enumerate(texts)]
        if not all(isinstance(p["text"], str) for p in passages):
            raise ValueError("Agent Embedding passages must be strings")
        if not passages:
            return []
        response = self.client.post(
            "/embed",
            body={"capability_id": self.capability_id, "passages": passages},
            requester_agent_id=self._options["requester_agent_id"],
        )
        if not isinstance(response, dict) or any(
            response.get(key) != expected
            for key, expected in (
                ("capability_id", self.capability_id),
                ("model_id", self.model),
                ("revision", self.revision),
                ("dim", self.dimensions),
            )
        ):
            raise ValueError("Agent Embedding served identity differs from the declaration")
        if type(response.get("dim")) is not int:
            raise ValueError("Agent Embedding returned a non-integer dimension")
        vectors = response.get("vectors")
        if not isinstance(vectors, dict) or set(vectors) != {p["id"] for p in passages}:
            raise ValueError("Agent Embedding returned different passage ids")
        result = []
        for passage in passages:
            vector = vectors[passage["id"]]
            if not isinstance(vector, list) or len(vector) != self.dimensions:
                raise ValueError("Agent Embedding returned an invalid vector width")
            try:
                valid = all(type(v) in (int, float) and math.isfinite(v) for v in vector)
            except OverflowError as error:
                raise ValueError("Agent Embedding returned an oversized vector value") from error
            if not valid:
                raise ValueError("Agent Embedding returned a nonfinite or nonnumeric vector")
            result.append([float(value) for value in vector])
        return result

    async def embed_text(self, text: List[str]) -> List[List[float]]:
        """Serve the Owner's Cognee seam with client construction and HTTP in a model worker."""
        return await asyncio.to_thread(self.embed_batch, text)

    def get_vector_size(self) -> int:
        """Return the configured width without constructing a client or calling a model."""
        return self.dimensions
