"""Memory's client for agent_embedding's direct door: the numbers come from there, labelled by what answered.

It is for the interim in which memory asks agent_embedding for numbers directly, before agent_corpus
runs every classifier over what reaches memory. It takes the door's address, the requester id the door
requires, a time limit and a reader of a memory event's original text. It gives back the interface's data
shapes, with the model, revision and width the door reports for the reader that answered. It never loads a
model, never makes a number, never cuts text (an event's whole text is one piece) and never fills in a
number the door did not send: every failure is NumbersNotYetSupplied naming the door and the reason.
"""

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any

from service.memory_supplier import (
    PENDING_WORK,
    NumbersNotYetSupplied,
    SuppliedNumbers,
    SuppliedPiece,
    SupplierModelRow,
    text_fingerprint,
)

CAPABILITIES_PATH = "/capabilities"
EMBED_PATH = "/embed"
REQUESTER_HEADER = "requester-agent-id"
PIECE_SUFFIX = "#0"

EventTextReader = Callable[[str], Awaitable[str | None]]


class EmbeddingDoorSupplier:
    """The NumbersSupplier that asks agent_embedding's GET /capabilities and POST /embed and nothing else.

    Goes in: the door's base address, the requester id, the time limit of one request and a reader that
    returns a memory event's original text. Comes out: the interface's data shapes. The recipe a model
    row carries is the door's capability id (for example the static meaning reader); the model id and
    revision are the ones the door reports. It never returns invented numbers.
    """

    def __init__(
        self,
        base_url: str,
        requester_agent_id: str,
        timeout_seconds: float,
        event_text: EventTextReader,
    ) -> None:
        """Keep the declared address, requester, time limit and event-text reader; open nothing."""
        if not base_url or not requester_agent_id or timeout_seconds <= 0:
            raise ValueError(
                "base_url, requester_agent_id and a positive timeout_seconds are all required"
            )
        self.base_url = base_url.rstrip("/")
        self.requester_agent_id = requester_agent_id
        self.timeout_seconds = timeout_seconds
        self.event_text = event_text

    def _call(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        """Make one blocking request and return the decoded reply, or raise the sentinel.

        It takes the method, the path and an optional JSON body. It returns the decoded JSON. A refused
        connection, a timeout, an error status (quoted) or a reply that is not JSON raises
        NumbersNotYetSupplied. It never retries.
        """
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json", REQUESTER_HEADER: self.requester_agent_id},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as reply:
                raw = reply.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:400]
            raise NumbersNotYetSupplied(
                f"{PENDING_WORK}: agent_embedding {method} {path} answered {error.code}: {detail}"
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise NumbersNotYetSupplied(
                f"{PENDING_WORK}: agent_embedding {method} {path} is not reachable at "
                f"{self.base_url}: {error}"
            ) from error
        try:
            return json.loads(raw)
        except ValueError as error:
            raise NumbersNotYetSupplied(
                f"{PENDING_WORK}: agent_embedding {method} {path} did not answer with JSON"
            ) from error

    async def _capabilities(self) -> list[dict[str, Any]]:
        """Return the door's reader rows (capability id, occupant, revision, width) as it lists them."""
        reply = await asyncio.to_thread(self._call, "GET", CAPABILITIES_PATH, None)
        rows = reply.get("capabilities") if isinstance(reply, dict) else None
        if not isinstance(rows, list):
            raise NumbersNotYetSupplied(
                f"{PENDING_WORK}: agent_embedding's capabilities reply has no list of readers"
            )
        return rows

    async def current_row(self, collection: str, recipe: str) -> SupplierModelRow:
        """Return the reader the door runs now for a capability id (the recipe), as the door lists it.

        It takes the collection label and the recipe (a capability id). It returns the occupant, its
        revision and its width. It raises the sentinel when the door does not list that capability or
        lists no width for it.
        """
        for row in await self._capabilities():
            if row.get("capability_id") == recipe:
                if not isinstance(row.get("dim"), int) or not row.get("occupant_id"):
                    raise NumbersNotYetSupplied(
                        f"{PENDING_WORK}: agent_embedding lists no occupant or width for {recipe}"
                    )
                return SupplierModelRow(
                    model_id=row["occupant_id"],
                    revision=row["revision"],
                    width=row["dim"],
                    kind="embedding",
                    collection=collection,
                    recipe=recipe,
                )
        raise NumbersNotYetSupplied(f"{PENDING_WORK}: agent_embedding does not list {recipe}")

    async def _embed(
        self, capability: str, model_id: str, revision: str, passages: list[dict[str, str]]
    ) -> dict[str, list[float]]:
        """Ask the door to embed passages with one capability and check who answered.

        It takes the capability id, the model label the caller expects and the passages (id and text).
        It returns the vectors by id. It raises the sentinel when the door answers with another
        occupant or revision than expected (the numbers would carry a different label), when it
        returns no vector for an id, or when a vector's length is not the reported width.
        """
        reply = await asyncio.to_thread(
            self._call, "POST", EMBED_PATH, {"capability_id": capability, "passages": passages}
        )
        if reply.get("model_id") != model_id or reply.get("revision") != revision:
            raise NumbersNotYetSupplied(
                f"{PENDING_WORK}: agent_embedding answered with {reply.get('model_id')} "
                f"{reply.get('revision')}, not the {model_id} {revision} that was asked for"
            )
        vectors = reply.get("vectors")
        width = reply.get("dim")
        if not isinstance(vectors, dict) or not isinstance(width, int):
            raise NumbersNotYetSupplied(f"{PENDING_WORK}: agent_embedding sent no vectors or width")
        for passage in passages:
            vector = vectors.get(passage["id"])
            if not isinstance(vector, list) or len(vector) != width:
                raise NumbersNotYetSupplied(
                    f"{PENDING_WORK}: agent_embedding sent no vector of width {width} for "
                    f"{passage['id']}"
                )
        return vectors

    async def saved_numbers(
        self, collection: str, recipe: str, model_id: str, revision: str, event_id: str
    ) -> SuppliedNumbers:
        """Return the one piece for an event (its whole text) with the door's numbers for it.

        It takes the collection, the recipe (capability id), the model label and the event id. It
        returns the event text as one piece (position 0) and the vector the door sent for it. It
        raises the sentinel when memory holds no text for the event or the door cannot answer.
        """
        text = await self.event_text(event_id)
        if text is None or not text.strip():
            raise NumbersNotYetSupplied(
                f"{PENDING_WORK}: memory holds no original text for event {event_id}"
            )
        piece_id = f"{event_id}{PIECE_SUFFIX}"
        vectors = await self._embed(recipe, model_id, revision, [{"id": piece_id, "text": text}])
        piece = SuppliedPiece(piece_id, event_id, 0, text, text_fingerprint(text))
        width = len(vectors[piece_id])
        return SuppliedNumbers(model_id, revision, width, (piece,), {piece_id: vectors[piece_id]})

    async def question_numbers(self, model_id: str, revision: str, text: str) -> list[float]:
        """Return the door's numbers for one text under the capability whose occupant is the model asked for.

        It takes the model label and the text. It finds the capability the door lists for that
        occupant and revision, asks it to embed the text and returns the vector. It raises the sentinel
        when the door lists no such capability or answers with another label.
        """
        capability = next(
            (
                row["capability_id"]
                for row in await self._capabilities()
                if row.get("occupant_id") == model_id and row.get("revision") == revision
            ),
            None,
        )
        if capability is None:
            raise NumbersNotYetSupplied(
                f"{PENDING_WORK}: agent_embedding lists no reader that is {model_id} {revision}"
            )
        vectors = await self._embed(capability, model_id, revision, [{"id": "question", "text": text}])
        return list(vectors["question"])

    async def recompute(
        self,
        collection: str,
        recipe: str,
        model_id: str,
        revision: str,
        event_ids: list[str],
        reason: str,
    ) -> None:
        """Accept the request: this door makes numbers when they are asked for, not in advance.

        The next sync asks for each event's numbers afresh from the door, so nothing needs queueing here;
        the caller keeps the request recorded as pending work until those numbers arrive. If the door now
        runs another revision, the next sync's current_row says so and the old label stays stale.
        """
        return None
