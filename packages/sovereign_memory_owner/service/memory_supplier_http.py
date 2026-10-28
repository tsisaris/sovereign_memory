"""Memory's client for agent_corpus's three number doors, replacing the "not supplied" stand-in.

The three doors are GET /saved_numbers/models (the catalogue), GET /saved_numbers (a page
of saved numbers for one collection) and POST /question_numbers (one question's numbers
from every model). This module is the only place memory talks to agent_corpus. It never
calls agent_embedding, makes no number, cuts no text and caches nothing beyond one
collection's pages and the latest question reply.

How memory's interface maps onto what the doors really carry (measured by reading
agent_corpus's M_8_A_2 and M_8_A_3 and its passage fingerprint recipe in M_2_A_1):

- A saved number is keyed by the corpus passage fingerprint: the SHA-256 of the passage
  text in Unicode NFC with every line end as one line feed. Memory's own fingerprint is the
  SHA-256 of the raw UTF-8 bytes. The two agree only for text already in that form, so this
  client computes the corpus form to find a number and keeps memory's form on the piece.
- The doors carry no piece text, position or event id, and no door says how a text was
  cut. An event is matched only when the whole event text is one corpus passage; the one
  piece is that passage (position 0, its unit id as piece id). An event cut into several
  passages, or not yet read by agent_corpus, has no match and is reported as numbers not yet
  supplied, naming that reason.
- There is no door for a model's current row or for recomputation. The current row is read
  from the catalogue: the supplier recipe is the model id agent_corpus labels its numbers
  with, and the revision saved most recently under that id (the catalogue's last_saved) is the
  one now run. The catalogue carries no kind, so every such row says "embedding". A recompute
  request is refused naming the missing door.
"""

import asyncio
import json
import re
import unicodedata
import urllib.error
import urllib.parse
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

MODELS_PATH = "/saved_numbers/models"
NUMBERS_PATH = "/saved_numbers"
QUESTION_PATH = "/question_numbers"
REQUESTER_FIELD = "requester_agent_id"
# The text form agent_corpus hashes (its M_2_A_1 normalized()): Unicode NFC, line ends as LF.
_NORMAL_FORM = "NFC"
_LINE_END = re.compile(r"\r\n|\r")

EventTextReader = Callable[[str], Awaitable[str | None]]


def corpus_fingerprint(text: str) -> str:
    """Return the passage fingerprint agent_corpus uses for a text.

    Goes in: any string. Comes out: the lowercase hexadecimal SHA-256 over the text in
    Unicode NFC with every line end as one line feed, which is how agent_corpus hashes a
    passage. It never changes the text it is given.
    """
    normalized = unicodedata.normalize(_NORMAL_FORM, _LINE_END.sub("\n", text))
    return text_fingerprint(normalized)


class CorpusHttpSupplier:
    """The NumbersSupplier that asks agent_corpus's three doors and nothing else.

    Goes in: the supplier's base address, the requester id the doors require, the time
    limit for one request and a reader that returns a memory event's original text. Comes
    out: the interface's data shapes. Every failure to get an answer is
    NumbersNotYetSupplied naming the door and the reason; it never returns invented numbers.
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
        self._collections: dict[tuple[str, str, str], dict[str, tuple[str, list[float], int]]] = {}
        self._question: tuple[str, dict[str, Any]] | None = None

    def _call(self, method: str, path: str, params: dict[str, Any], body: dict | None) -> Any:
        """Make one blocking request and return the decoded reply, or raise the sentinel.

        Goes in: the method, the path, query parameters and an optional JSON body. Comes
        out: the decoded JSON. A refused connection, a timeout, an error status (its
        error and detail are quoted) or a reply that is not JSON all raise
        NumbersNotYetSupplied.
        """
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as reply:
                raw = reply.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise NumbersNotYetSupplied(
                f"{PENDING_WORK}: agent_corpus {method} {path} answered {error.code}: {detail}"
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise NumbersNotYetSupplied(
                f"{PENDING_WORK}: agent_corpus {method} {path} is not reachable at "
                f"{self.base_url}: {error}"
            ) from error
        try:
            return json.loads(raw)
        except ValueError as error:
            raise NumbersNotYetSupplied(
                f"{PENDING_WORK}: agent_corpus {method} {path} did not answer with JSON"
            ) from error

    async def _get(self, path: str, **params: Any) -> Any:
        """Ask a GET door with the requester id added, off the event loop."""
        params[REQUESTER_FIELD] = self.requester_agent_id
        return await asyncio.to_thread(self._call, "GET", path, params, None)

    async def catalogue(self) -> list[dict[str, Any]]:
        """Return agent_corpus's catalogue: every model, revision and width with numbers saved.

        Goes in: nothing. Comes out: the catalogue entries as the door sent them. An empty
        catalogue raises the sentinel naming the reason the door gave.
        """
        reply = await self._get(MODELS_PATH)
        entries = reply.get("catalogue") if isinstance(reply, dict) else None
        if not isinstance(entries, list) or not entries:
            reason = reply.get("reason") if isinstance(reply, dict) else None
            raise NumbersNotYetSupplied(
                f"{PENDING_WORK}: agent_corpus has no saved numbers ({reason or 'empty catalogue'})"
            )
        return entries

    async def current_row(self, collection: str, recipe: str) -> SupplierModelRow:
        """Return the revision saved most recently under the model id named by the recipe.

        Goes in: the collection and the recipe (a model id). Comes out: that model's row
        with kind "embedding". It raises the sentinel when no number is saved under that id.
        """
        entries = [e for e in await self.catalogue() if e.get("model_id") == recipe]
        if not entries:
            raise NumbersNotYetSupplied(
                f"{PENDING_WORK}: agent_corpus has no saved numbers under model id {recipe!r}"
            )
        newest = max(entries, key=lambda entry: str(entry.get("last_saved")))
        return SupplierModelRow(
            model_id=newest["model_id"],
            revision=newest["revision"],
            width=int(newest["width"]),
            kind="embedding",
            collection=collection,
            recipe=recipe,
        )

    async def _collection_numbers(
        self, collection: str, model_id: str, revision: str, refresh: bool
    ) -> dict[str, tuple[str, list[float], int]]:
        """Read every saved number of one collection under one label, by corpus fingerprint.

        Goes in: the collection, model id and revision, and whether to ignore the cache.
        Comes out: fingerprint to (unit id, vector, width). It pages the door until its
        cursor is null; a refused label (the door names it and lists the catalogue) raises
        the sentinel.
        """
        key = (collection, model_id, revision)
        if not refresh and key in self._collections:
            return self._collections[key]
        found: dict[str, tuple[str, list[float], int]] = {}
        after: str | None = None
        while True:
            page = await self._get(
                NUMBERS_PATH,
                collection_id=collection,
                model_id=model_id,
                revision=revision,
                after=after,
            )
            for number in page.get("numbers", []):
                found.setdefault(
                    number["passage_fingerprint"],
                    (number["unit_id"], number["vector"], int(number["width"])),
                )
            after = page.get("next")
            if after is None:
                break
        self._collections[key] = found
        return found

    async def saved_numbers(
        self, collection: str, recipe: str, model_id: str, revision: str, event_id: str
    ) -> SuppliedNumbers:
        """Return the one piece for an event whose whole text is a saved corpus passage.

        Goes in: the collection, recipe, model label and the event id. Comes out: the event's
        text as one piece (its unit id as piece id) with that model revision's saved numbers.
        It re-reads the collection once if the fingerprint is not found, then raises the
        sentinel naming that no saved passage equals the event's whole text.
        """
        text = await self.event_text(event_id)
        if text is None:
            raise NumbersNotYetSupplied(
                f"{PENDING_WORK}: memory holds no original text for event {event_id}"
            )
        fingerprint = corpus_fingerprint(text)
        numbers = await self._collection_numbers(collection, model_id, revision, False)
        if fingerprint not in numbers:
            numbers = await self._collection_numbers(collection, model_id, revision, True)
        if fingerprint not in numbers:
            raise NumbersNotYetSupplied(
                f"{PENDING_WORK}: agent_corpus has no saved passage equal to the whole text of "
                f"event {event_id} under {model_id} {revision} (the event may be cut into several "
                "passages, or not read yet)"
            )
        unit_id, vector, width = numbers[fingerprint]
        piece = SuppliedPiece(unit_id, event_id, 0, text, text_fingerprint(text))
        return SuppliedNumbers(model_id, revision, width, (piece,), {unit_id: vector})

    async def question_numbers(
        self, model_id: str, revision: str, text: str, role: str = "question"
    ) -> list[float]:
        """Return one model revision's numbers for a text, from agent_corpus's question door.

        Goes in: the model label and the text (the role is accepted and ignored: this door reads
        every text the same way). Comes out: that label's vector from the reply
        for the whole text. The door answers for every model at once and saves each ask, so
        the latest reply is kept and reused for the same text. A model the door lists as
        unreached, or a label it did not return, raises the sentinel with the door's reason.
        """
        if self._question is None or self._question[0] != text:
            reply = await asyncio.to_thread(
                self._call,
                "POST",
                QUESTION_PATH,
                {},
                {REQUESTER_FIELD: self.requester_agent_id, "question": text},
            )
            self._question = (text, reply)
        reply = self._question[1]
        for entry in reply.get("numbers", []):
            if entry.get("model_id") == model_id and entry.get("revision") == revision:
                return list(entry["vector"])
        reasons = [
            f"{u.get('model_id')}: {u.get('reason')}"
            for u in reply.get("unreached", [])
            if u.get("model_id") == model_id
        ]
        raise NumbersNotYetSupplied(
            f"{PENDING_WORK}: agent_corpus returned no numbers for {model_id} {revision}"
            + (f" ({'; '.join(reasons)})" if reasons else "")
        )

    async def recompute(
        self,
        collection: str,
        recipe: str,
        model_id: str,
        revision: str,
        event_ids: list[str],
        reason: str,
    ) -> None:
        """Refuse: agent_corpus has no door through which memory can ask for recomputation."""
        raise NumbersNotYetSupplied(
            f"{PENDING_WORK}: agent_corpus has no recompute door for {model_id} {revision}"
        )
