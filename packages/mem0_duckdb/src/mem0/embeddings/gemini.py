"""An embedder that asks Google's Generative AI service for embeddings.

Goes in: text; comes out: a list of floats. It calls a remote service and is not used by the memory
owner path, which accepts only received numbers.
"""

import os
from typing import Literal, Optional

from google import genai
from google.genai import types

from mem0.configs.embeddings.base import BaseEmbedderConfig
from mem0.embeddings.base import EmbeddingBase


class GoogleGenAIEmbedding(EmbeddingBase):
    """Embedder for Google Generative AI.

    Goes in: text to embed; comes out: its embedding from the configured Gemini model. It calls a
    remote service and never caches results.
    """

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        """Set up the Gemini client.

        Goes in: an optional embedder config (model, width, API key; the environment supplies a
        missing key); comes out: a ready embedder. It sends no request.
        """
        super().__init__(config)

        self.config.model = self.config.model or "models/gemini-embedding-001"
        self.config.embedding_dims = (
            self.config.embedding_dims or self.config.output_dimensionality or 768
        )

        api_key = self.config.api_key or os.getenv("GOOGLE_API_KEY")

        self.client = genai.Client(api_key=api_key)

    def embed(self, text, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """
        Get the embedding for the given text using Google Generative AI.
        Args:
            text (str): The text to embed.
            memory_action (optional): The type of embedding to use. Must be one of "add", "search", or "update". Defaults to None.
        Returns:
            list: The embedding vector.
        """
        text = text.replace("\n", " ")

        # Create config for embedding parameters
        config = types.EmbedContentConfig(output_dimensionality=self.config.embedding_dims)

        # Call the embed_content method with the correct parameters
        response = self.client.models.embed_content(
            model=self.config.model, contents=text, config=config
        )

        return response.embeddings[0].values

    def embed_batch(self, texts, memory_action="add"):
        """Embed several texts, at most 100 per request.

        Goes in: a list of texts and an unused memory action; comes out: one embedding per text in
        order. It raises ValueError if the service returns a different count, and returns an empty
        list for no texts.
        """
        if not texts:
            return []
        config = types.EmbedContentConfig(output_dimensionality=self.config.embedding_dims)
        MAX_BATCH = 100
        all_embeddings = []
        for i in range(0, len(texts), MAX_BATCH):
            chunk = [t.replace("\n", " ") for t in texts[i : i + MAX_BATCH]]
            response = self.client.models.embed_content(
                model=self.config.model, contents=chunk, config=config
            )
            all_embeddings.extend(e.values for e in response.embeddings)
        if len(all_embeddings) != len(texts):
            raise ValueError(
                f"Gemini embed_batch() returned {len(all_embeddings)} embeddings for {len(texts)} texts "
                f"using model '{self.config.model}'"
            )
        return all_embeddings
