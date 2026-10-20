"""An embedder that asks Google Vertex AI for embeddings.

Goes in: text; comes out: a list of floats. It calls a remote service and is not used by the memory
owner path, which accepts only received numbers.
"""

import logging
import os
from typing import Literal, Optional

from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

from mem0.configs.embeddings.base import BaseEmbedderConfig
from mem0.embeddings.base import EmbeddingBase
from mem0.utils.gcp_auth import GCPAuthenticator

logger = logging.getLogger(__name__)


class VertexAIEmbedding(EmbeddingBase):
    """Embedder for Vertex AI.

    Goes in: text to embed; comes out: its embedding from the configured model and task type. It
    never caches results.
    """

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        """Set up the Vertex AI model and the embedding task type for each memory action.

        Goes in: an optional embedder config; comes out: a ready embedder. It authenticates through
        the GCP helper and sends no embedding request.
        """
        super().__init__(config)

        self.config.model = self.config.model or "gemini-embedding-001"
        self.config.embedding_dims = self.config.embedding_dims or 256

        self.embedding_types = {
            "add": self.config.memory_add_embedding_type or "RETRIEVAL_DOCUMENT",
            "update": self.config.memory_update_embedding_type or "RETRIEVAL_DOCUMENT",
            "search": self.config.memory_search_embedding_type or "RETRIEVAL_QUERY",
        }

        # Set up authentication using centralized GCP authenticator
        # This supports multiple authentication methods while preserving environment variable support
        try:
            GCPAuthenticator.setup_vertex_ai(
                service_account_json=getattr(self.config, "google_service_account_json", None),
                credentials_path=self.config.vertex_credentials_json,
                project_id=getattr(self.config, "google_project_id", None),
            )
        except Exception:
            logger.debug(
                "GCP authenticator setup failed; using the credentials path", exc_info=True
            )
            # Fall back to original behavior for backward compatibility
            credentials_path = self.config.vertex_credentials_json
            if credentials_path:
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path
            elif not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
                raise ValueError(
                    "Google application credentials JSON is not provided. Please provide a valid JSON path or set the 'GOOGLE_APPLICATION_CREDENTIALS' environment variable."
                )

        self.model = TextEmbeddingModel.from_pretrained(self.config.model)

    def embed(self, text, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """
        Get the embedding for the given text using Vertex AI.

        Args:
            text (str): The text to embed.
            memory_action (optional): The type of embedding to use. Must be one of "add", "search", or "update". Defaults to None.
        Returns:
            list: The embedding vector.
        """
        embedding_type = "SEMANTIC_SIMILARITY"
        if memory_action is not None:
            if memory_action not in self.embedding_types:
                raise ValueError(f"Invalid memory action: {memory_action}")

            embedding_type = self.embedding_types[memory_action]

        text_input = TextEmbeddingInput(text=text, task_type=embedding_type)
        embeddings = self.model.get_embeddings(
            texts=[text_input], output_dimensionality=self.config.embedding_dims
        )

        return embeddings[0].values

    def embed_batch(self, texts, memory_action="add"):
        """Embed several texts with the task type chosen by the memory action.

        Goes in: a list of texts and a memory action (add, update, search or None); comes out: one
        embedding per text in order. It raises ValueError for an unknown action and returns an empty
        list for no texts.
        """
        if not texts:
            return []
        embedding_type = "SEMANTIC_SIMILARITY"
        if memory_action is not None:
            if memory_action not in self.embedding_types:
                raise ValueError(f"Invalid memory action: {memory_action}")
            embedding_type = self.embedding_types[memory_action]
        all_embeddings = []
        for i in range(0, len(texts), 250):
            chunk = texts[i : i + 250]
            inputs = [TextEmbeddingInput(text=t, task_type=embedding_type) for t in chunk]
            results = self.model.get_embeddings(
                texts=inputs, output_dimensionality=self.config.embedding_dims
            )
            all_embeddings.extend(r.values for r in results)
        if len(all_embeddings) != len(texts):
            raise ValueError(
                f"Vertex AI embed_batch() returned {len(all_embeddings)} embeddings for {len(texts)} texts"
                f" using model '{self.config.model}'"
            )
        return all_embeddings
