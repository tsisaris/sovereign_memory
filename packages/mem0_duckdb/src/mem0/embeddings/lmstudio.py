"""An embedder that asks a local LM Studio server for embeddings through an OpenAI-compatible client.

Goes in: text; comes out: a list of floats. It is not used by the memory owner path, which accepts
only received numbers.
"""

from typing import Literal, Optional

from openai import OpenAI

from mem0.configs.embeddings.base import BaseEmbedderConfig
from mem0.embeddings.base import EmbeddingBase


class LMStudioEmbedding(EmbeddingBase):
    """Embedder for LM Studio.

    Goes in: text to embed; comes out: its embedding from the configured local model. It never
    caches results.
    """

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        """Set up the LM Studio client with defaults for model, width, key and server address.

        Goes in: an optional embedder config; comes out: a ready embedder. It sends no request.
        """
        super().__init__(config)

        self.config.model = (
            self.config.model
            or "nomic-ai/nomic-embed-text-v1.5-GGUF/nomic-embed-text-v1.5.f16.gguf"
        )
        self.config.embedding_dims = self.config.embedding_dims or 1536
        self.config.api_key = self.config.api_key or "lm-studio"

        self.client = OpenAI(base_url=self.config.lmstudio_base_url, api_key=self.config.api_key)

    def embed(self, text, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """
        Get the embedding for the given text using LM Studio.
        Args:
            text (str): The text to embed.
            memory_action (optional): The type of embedding to use. Must be one of "add", "search", or "update". Defaults to None.
        Returns:
            list: The embedding vector.
        """
        text = text.replace("\n", " ")
        return (
            self.client.embeddings.create(input=[text], model=self.config.model).data[0].embedding
        )

    def embed_batch(self, texts, memory_action="add"):
        """Embed several texts in one request with newlines replaced by spaces.

        Goes in: a list of texts and an unused memory action; comes out: one embedding per text in
        order. It raises ValueError if the server returns a different count and returns an empty
        list for no texts.
        """
        if not texts:
            return []
        cleaned = [t.replace("\n", " ") for t in texts]
        response = self.client.embeddings.create(input=cleaned, model=self.config.model)
        sorted_data = sorted(response.data, key=lambda x: x.index)
        embeddings = [item.embedding for item in sorted_data]
        if len(embeddings) != len(texts):
            raise ValueError(
                f"LM Studio embed_batch() returned {len(embeddings)} embeddings for {len(texts)} texts"
                f" using model '{self.config.model}'"
            )
        return embeddings
