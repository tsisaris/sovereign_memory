"""What the tiers use to reach received numbers: lookup, owed requests and the question door.

NumbersServices is handed to each tier. It looks up numbers memory already
received, records numbers that are owed, and fetches the numbers of a search
question from the supplier's single-text door, checking them against the
registered width. ReceivedNumbersEmbeddingEngine is the embedder object Cognee
needs: it answers from received numbers and, where none exist, raises
NumbersNotYetSupplied. Nothing here makes numbers, runs a model, measures or cuts
text; a missing number is always reported, never replaced by a vector.
"""

import asyncio

from service.memory_models import ModelRow, check_vector, get_model, list_models, model_state
from service.memory_numbers import NumbersStore, numbers_for_text
from service.memory_processor_seam import OwnerReader
from service.memory_supplier import (
    PENDING_WORK,
    NumbersNotYetSupplied,
    NumbersSupplier,
)


class NumbersServices:
    """Received-number lookup, owed-number records and the question door, for the tiers.

    Goes in: the numbers store, the supplier and (later, from the dispatcher)
    the owner's reader. Comes out: numbers or the sentinel. It never writes
    numbers itself, never calls a model and never answers from a guess.
    """

    pending_error = NumbersNotYetSupplied

    def __init__(self, store: NumbersStore, supplier: NumbersSupplier) -> None:
        """Keep the store and the supplier; the reader is attached when the owner starts."""
        self.store = store
        self.supplier = supplier
        self._reader: OwnerReader | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_reader(self, reader: OwnerReader) -> None:
        """Receive the owner's reader and remember the event loop it runs on."""
        self._reader = reader
        self._loop = asyncio.get_running_loop()

    def _require_reader(self) -> OwnerReader:
        """Return the attached reader or raise RuntimeError if the owner never started."""
        if self._reader is None:
            raise RuntimeError("NumbersServices has no owner reader attached")
        return self._reader

    async def model(self, model_id: str, revision: str) -> ModelRow:
        """Return the registered model revision or raise ValueError when it is not registered."""
        found = await self._require_reader()(lambda handle: get_model(handle, model_id, revision))
        if found is None:
            raise ValueError(f"Model {model_id}@{revision} is not registered")
        return found

    async def alookup(self, model_id: str, revision: str, text: str) -> list[float] | None:
        """Return the received, fresh numbers of a model for a text, or None if none were received."""

        def read(handle):
            """Find the model and then its numbers for the text on the owner's worker."""
            model = get_model(handle, model_id, revision)
            if model is None:
                raise ValueError(f"Model {model_id}@{revision} is not registered")
            return numbers_for_text(handle, model, text)

        return await self._require_reader()(read)

    def lookup(self, model_id: str, revision: str, text: str) -> list[float] | None:
        """Blocking form of alookup for a model-worker thread; never call it on the event loop.

        Raises RuntimeError when called from the loop thread itself, where waiting
        would deadlock the owner.
        """
        loop = self._loop
        if loop is None:
            raise RuntimeError("NumbersServices has no event loop attached")
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            raise RuntimeError("lookup blocks; use alookup on the event loop")
        return asyncio.run_coroutine_threadsafe(
            self.alookup(model_id, revision, text), loop
        ).result()

    async def request(
        self, model_id: str, revision: str, event_id: str | None, text: str | None, reason: str
    ) -> str:
        """Record that numbers for this text (or pieces for this event) are owed; return the request id."""
        model = await self.model(model_id, revision)
        return await self.store.request(model, event_id, text, reason)

    async def request_pieces(self, event_id: str, reason: str) -> list[str]:
        """Record, once per registered embedding model, that the pieces of an event are owed.

        Returns the request ids. With no model registered nothing can be asked
        and the list is empty; the caller still reports the pending work.
        """
        models = await self._require_reader()(lambda handle: list_models(handle, "embedding"))
        return [await self.store.request(model, event_id, None, reason) for model in models]

    async def question_numbers(self, model_id: str, revision: str, text: str) -> list[float]:
        """Fetch the numbers of one search question from the supplier's single-text door.

        The vector is checked against the registered width and for finite
        values. Raises NumbersNotYetSupplied when the supplier cannot answer and
        ValueError when it answers with a wrong-width or non-finite vector. Memory
        never embeds the question itself.
        """
        model = await self.model(model_id, revision)
        vector = await self.supplier.question_numbers(model_id, revision, text)
        return check_vector(vector, model.width)

    async def fresh_models(self) -> tuple[list[ModelRow], dict[str, str]]:
        """Return (fresh embedding models, {label: reason} for the stale ones). Reads only."""

        def read(handle):
            """Measure every embedding model's freshness on the owner's worker."""
            fresh, left_out = [], {}
            for model in list_models(handle, "embedding"):
                state = model_state(handle, model)
                if state.fresh:
                    fresh.append(model)
                else:
                    left_out[model.label] = "stale: " + "; ".join(state.reasons)
            return fresh, left_out

        return await self._require_reader()(read)


class ReceivedNumbersEmbeddingEngine:
    """The embedder object Cognee needs, answering only from numbers memory received.

    It has the interface of a Cognee embedding engine (embed_text, get_vector_size
    and the model attributes) for one registered model revision. A text with no
    received numbers raises NumbersNotYetSupplied; it never returns a vector it
    made, and it never loads or calls a model.
    """

    provider = "received_numbers"
    api_version = None

    def __init__(self, services: NumbersServices, model: ModelRow) -> None:
        """Bind the lookup service to one registered embedding model."""
        if model.kind != "embedding":
            raise ValueError("An embedding engine needs an embedding model")
        self.services = services
        self.model = model.model_id
        self.revision = model.revision
        self.dimensions = model.width

    async def embed_text(self, text: list[str]) -> list[list[float]]:
        """Return the received numbers for each text, or raise NumbersNotYetSupplied for the first missing."""
        vectors: list[list[float]] = []
        for item in text:
            found = await self.services.alookup(self.model, self.revision, item)
            if found is None:
                raise NumbersNotYetSupplied(
                    f"{PENDING_WORK}: no received numbers from {self.model}@{self.revision} "
                    "for this text"
                )
            vectors.append(found)
        return vectors

    def get_vector_size(self) -> int:
        """Return the registered width without any lookup or model call."""
        return self.dimensions

