"""The declared interface through which memory receives numbers from its supplier.

The supplier is agent_corpus: it cuts text into pieces, has agent_embedding turn
each piece into numbers, saves them labelled with model, revision and width, and
hands them to memory. Memory never makes numbers of its own, so everything it
needs from the supplier is named here as one interface, and where the supplier
cannot answer the answer is the sentinel NumbersNotYetSupplied, which names the
pending work. This module holds data shapes, the interface and the sentinel
supplier only; it opens no database, calls no model and cuts no text.
"""

import hashlib
from dataclasses import dataclass
from typing import Protocol

PENDING_WORK = "numbers not yet supplied"
# The form of every text fingerprint exchanged with the supplier: the lowercase
# hexadecimal SHA-256 of the text's UTF-8 bytes. It is a format, not a tunable.
FINGERPRINT_FORMAT = "sha256-hex-of-utf8"
PIECE_SOURCE_EVENT = "event"
PIECE_SOURCE_DERIVED = "derived"
MODEL_KINDS = ("embedding", "classifier")


class NumbersNotYetSupplied(RuntimeError):
    """The supplier cannot give the numbers asked for yet; the message names the pending work.

    It is the one answer for "not supplied": a tier that meets it records a
    retryable failure, and search leaves the model out and says why. It is
    never turned into an empty result and never replaced by invented numbers.
    """


def text_fingerprint(text: str) -> str:
    """Return the fingerprint of a text in the declared format.

    Goes in: any string. Comes out: its lowercase hexadecimal SHA-256 over the
    UTF-8 bytes. It never measures, cuts or interprets the text.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SupplierModelRow:
    """The supplier's current row for a collection and recipe: which model it now runs.

    Comes from the supplier only; memory compares it with its registry row to
    find a model whose label no longer matches. It carries no numbers.
    """

    model_id: str
    revision: str
    width: int
    kind: str
    collection: str
    recipe: str


@dataclass(frozen=True)
class SuppliedPiece:
    """One piece the supplier cut: its id, its source event, its position and its text.

    source is "event" for a piece of a captured event's text and "derived" for
    a text memory's engines derived from an event (for example a Mem0 fact) that
    the supplier measured on request. The fingerprint is in the declared format.
    """

    piece_id: str
    event_id: str
    position: int
    text: str
    text_fingerprint: str
    source: str = PIECE_SOURCE_EVENT


@dataclass(frozen=True)
class SuppliedNumbers:
    """A batch from the supplier: pieces and one model's numbers for them, labelled.

    numbers maps a piece id to its list of numbers; every list has length width
    (the class count for a classifier). The batch is data in transit, never
    memory's own computation.
    """

    model_id: str
    revision: str
    width: int
    pieces: tuple[SuppliedPiece, ...]
    numbers: dict[str, list[float]]


class NumbersSupplier(Protocol):
    """What memory calls on the supplier; every method may raise NumbersNotYetSupplied.

    Goes in: labels and ids memory already holds. Comes out: data in the shapes
    above. Memory never calls a model through this interface; it only receives
    numbers the supplier made and asks for recomputation.
    """

    async def current_row(self, collection: str, recipe: str) -> SupplierModelRow:
        """Return the model the supplier runs now for this collection and recipe."""
        ...

    async def saved_numbers(
        self, collection: str, recipe: str, model_id: str, revision: str, event_id: str
    ) -> SuppliedNumbers:
        """Return the pieces of one event with the saved numbers of one model revision."""
        ...

    async def question_numbers(self, model_id: str, revision: str, text: str) -> list[float]:
        """Return one model revision's numbers for a short search question (the single-text door)."""
        ...

    async def recompute(
        self,
        collection: str,
        recipe: str,
        model_id: str,
        revision: str,
        event_ids: list[str],
        reason: str,
    ) -> None:
        """Ask the supplier to recompute one model revision's numbers for the named events."""
        ...


class UnavailableSupplier:
    """The supplier used while none is connected: every call answers with the sentinel.

    Goes in: any call. Comes out: it always raises NumbersNotYetSupplied naming
    why. It never returns numbers, a row or an empty list that could pass for an
    answer, and it keeps no state.
    """

    def __init__(self, reason: str) -> None:
        """Keep the reason every refusal will name (what the supplier lacks)."""
        self.reason = reason

    def _refuse(self) -> NumbersNotYetSupplied:
        """Build the sentinel error naming the pending work and the reason."""
        return NumbersNotYetSupplied(f"{PENDING_WORK}: {self.reason}")

    async def current_row(self, collection: str, recipe: str) -> SupplierModelRow:
        """Refuse: the supplier's current row cannot be read."""
        raise self._refuse()

    async def saved_numbers(
        self, collection: str, recipe: str, model_id: str, revision: str, event_id: str
    ) -> SuppliedNumbers:
        """Refuse: no saved numbers can be handed over."""
        raise self._refuse()

    async def question_numbers(self, model_id: str, revision: str, text: str) -> list[float]:
        """Refuse: the single-question door does not exist or is not reachable."""
        raise self._refuse()

    async def recompute(
        self,
        collection: str,
        recipe: str,
        model_id: str,
        revision: str,
        event_ids: list[str],
        reason: str,
    ) -> None:
        """Refuse: the request cannot be delivered; the caller keeps it as pending work."""
        raise self._refuse()
