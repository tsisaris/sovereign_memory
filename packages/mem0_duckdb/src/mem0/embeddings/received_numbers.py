"""Give Mem0 an embedder that answers only from numbers the memory already received.

Memory never makes numbers of its own: it never runs a model and never calls a
model-serving agent. This embedder looks up, by text, the numbers the supplier
handed over for one registered model revision. Where none exist it raises
NumbersMissing, whose message names the pending work "numbers not yet supplied".
It never invents, pads, truncates or substitutes a vector, never loads a model
and never opens a database; the lookup is a function the owner supplies.
"""

import math
from typing import Callable, List, Literal, Optional

from mem0.configs.embeddings.base import BaseEmbedderConfig
from mem0.embeddings.base import EmbeddingBase

PENDING_WORK = "numbers not yet supplied"


class NumbersMissing(RuntimeError):
    """The numbers for a text have not been supplied yet; the message names the pending work.

    texts lists every text in the call that had no received numbers. It is the
    one answer for "not supplied" and is never turned into an empty vector.
    """

    def __init__(self, model: str, revision: str, texts: List[str]) -> None:
        """Keep the model label and the texts whose numbers are owed."""
        super().__init__(
            f"{PENDING_WORK}: {len(texts)} text(s) have no received numbers from {model}@{revision}"
        )
        self.texts = texts


class ReceivedNumbersEmbedding(EmbeddingBase):
    """Look up received numbers for one model revision; never compute any.

    Goes in: a BaseEmbedderConfig whose model, embedding_dims and received_numbers
    ({"revision": ...}) name one registered model revision. Comes out: the
    received numbers of a text, or NumbersMissing. The owner supplies the lookup
    with attach_lookup before the first use.
    """

    provider = "received_numbers"
    api_version = None

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        """Validate the model label and width; no lookup, model or I/O is created."""
        super().__init__(config)
        options = self.config.received_numbers
        if not isinstance(options, dict) or set(options) != {"revision"}:
            raise ValueError("received_numbers must be a table with exactly: revision")
        if not isinstance(options["revision"], str) or not options["revision"]:
            raise ValueError("received_numbers.revision must be a non-empty string")
        if not isinstance(self.config.model, str) or not self.config.model:
            raise ValueError("The received-numbers embedder requires an explicit model")
        if type(self.config.embedding_dims) is not int or self.config.embedding_dims <= 0:
            raise ValueError(
                "The received-numbers embedder requires positive integer embedding_dims"
            )
        self.model = self.config.model
        self.revision = options["revision"]
        self.dimensions = self.config.embedding_dims
        self._lookup: Optional[Callable[[str, str, str], Optional[List[float]]]] = None

    def attach_lookup(self, lookup: Callable[[str, str, str], Optional[List[float]]]) -> None:
        """Receive the owner's lookup: (model id, revision, text) -> numbers, or None if none received."""
        self._lookup = lookup

    def embed(self, text, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """Return the received numbers of one text or raise NumbersMissing; never make any."""
        return self.embed_batch([text], memory_action or "add")[0]

    def embed_batch(self, texts, memory_action="add") -> List[List[float]]:
        """Return received numbers for every text, or raise NumbersMissing naming all that lack them.

        Raises ValueError when a received vector has the wrong width or a
        non-finite value, and RuntimeError when the owner has not attached its
        lookup.
        """
        if self._lookup is None:
            raise RuntimeError("The received-numbers embedder has no owner lookup attached")
        found: List[Optional[List[float]]] = [
            self._lookup(self.model, self.revision, t) for t in texts
        ]
        missing = [t for t, vector in zip(texts, found, strict=True) if vector is None]
        if missing:
            raise NumbersMissing(self.model, self.revision, missing)
        result = []
        for vector in found:
            if len(vector) != self.dimensions or not all(
                type(v) in (int, float) and math.isfinite(v) for v in vector
            ):
                raise ValueError("Received numbers have the wrong width or are not finite")
            result.append([float(v) for v in vector])
        return result
