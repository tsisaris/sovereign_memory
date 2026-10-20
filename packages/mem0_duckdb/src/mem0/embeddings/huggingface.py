"""An embedder that uses Hugging Face, either a local sentence-transformers model or a text-embeddings server reached through an OpenAI-compatible client.

Goes in: text; comes out: a list of floats. It is not used by the memory owner path, which accepts
only received numbers.
"""

import logging
import os
from typing import Literal, Optional

from openai import OpenAI
from sentence_transformers import SentenceTransformer

from mem0.configs.embeddings.base import BaseEmbedderConfig
from mem0.embeddings.base import EmbeddingBase

logging.getLogger("transformers").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)


class HuggingFaceEmbedding(EmbeddingBase):
    """Embedder for Hugging Face models.

    Goes in: text to embed; comes out: its embedding from a local model or a server. It never caches
    results.
    """

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        """Set up the embedder: a client for a configured server URL, or a local sentence-transformers model.

        Goes in: an optional embedder config; comes out: a ready embedder. The local model is loaded
        here when no server URL is set.
        """
        super().__init__(config)

        if self.config.huggingface_base_url:
            api_key = self.config.api_key or os.getenv("HUGGINGFACE_API_KEY") or "hf"
            self.client = OpenAI(base_url=self.config.huggingface_base_url, api_key=api_key)
            self.config.model = self.config.model or "tei"
        else:
            self.config.model = self.config.model or "multi-qa-MiniLM-L6-cos-v1"

            self.model = SentenceTransformer(self.config.model, **self.config.model_kwargs)

            self.config.embedding_dims = (
                self.config.embedding_dims or self.model.get_sentence_embedding_dimension()
            )

    def embed(self, text, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """
        Get the embedding for the given text using Hugging Face.

        Args:
            text (str): The text to embed.
            memory_action (optional): The type of embedding to use. Must be one of "add", "search", or "update". Defaults to None.
        Returns:
            list: The embedding vector.
        """
        if self.config.huggingface_base_url:
            return (
                self.client.embeddings.create(
                    input=text, model=self.config.model, **self.config.model_kwargs
                )
                .data[0]
                .embedding
            )
        else:
            return self.model.encode(text, convert_to_numpy=True).tolist()

    def embed_batch(self, texts, memory_action="add"):
        """Embed several texts in one call, through the server when one is configured and otherwise with the local model.

        Goes in: a list of texts and an unused memory action; comes out: one embedding per text in
        order. It returns an empty list for no texts.
        """
        if not texts:
            return []
        if self.config.huggingface_base_url:
            response = self.client.embeddings.create(
                input=texts, model=self.config.model, **self.config.model_kwargs
            )
            sorted_data = sorted(response.data, key=lambda x: x.index)
            embeddings = [item.embedding for item in sorted_data]
            if len(embeddings) != len(texts):
                raise ValueError(
                    f"HuggingFace embed_batch() returned {len(embeddings)} embeddings for {len(texts)} texts"
                    f" using model '{self.config.model}'"
                )
            return embeddings
        else:
            result = self.model.encode(texts, convert_to_numpy=True).tolist()
            if len(result) != len(texts):
                raise ValueError(
                    f"HuggingFace embed_batch() returned {len(result)} embeddings for {len(texts)} texts"
                    f" using model '{self.config.model}'"
                )
            return result
