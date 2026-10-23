"""Letta's language-model steps inside the memory service: compaction, consolidation and dreaming.

It is the service's door to the translated Letta code in letta_memory_model (summarizer and
sleeptime). What goes in is the language_model settings dict, a function that opens a Letta
store, and the letta settings sections (compaction, sleeptime, dream); what comes out is, per
step, a plain dict with the real output, the seconds it took and the model the server reported.
It builds its model handle from those settings only, names no model in code, and never keeps a
store open between steps.
"""

import asyncio
import json
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Optional

from letta_memory_model.chat import ChatError, ChatReply, ContextWindowExceededError
from letta_memory_model.letta_duckdb_store import LettaMemoryStore
from letta_memory_model.model_state import create_group, ensure_state_tables, get_group
from letta_memory_model.schemas.message import Message
from letta_memory_model.sleeptime.consolidate import prepare_sleeptime_agent, run_sleeptime_agents
from letta_memory_model.sleeptime.dreaming import dream as run_dream
from letta_memory_model.sleeptime.settings import parse_dream_settings, parse_sleeptime_settings
from letta_memory_model.summarizer.conversation import compact_conversation
from letta_memory_model.summarizer.settings import parse_compaction_settings

CONTEXT_WORDS = ("context length", "context window", "too many tokens", "prompt is too long")


class OllamaChat:
    """A chat function for Ollama's OpenAI-style door, built from the language_model settings.

    It takes the settings dict (provider, model, base_url, api_key, request_timeout_seconds,
    options) and answers chat calls with ChatReply objects. It remembers the model name the
    server reported on the last call and, after each call, whether Ollama says that model sits on
    the processor or the graphics card. It names no model in code and never retries a call.
    """

    def __init__(self, language_model: dict[str, Any]) -> None:
        """Keep the settings dict and start with nothing reported yet."""
        self._settings = language_model
        self.requested_model: str = language_model["model"]
        self.achieved_model: Optional[str] = None
        self.achieved_processor: Optional[str] = None
        self.calls = 0

    def _post(self, path: str, body: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Send one request to the server (a POST with a body, otherwise a GET) and parse the JSON reply.

        A failure raises ChatError (ContextWindowExceededError when the server's words say the
        request was too long). It runs in a worker thread and never retries.
        """
        base = self._settings["base_url"].rstrip("/")
        headers = {"Authorization": f"Bearer {self._settings['api_key']}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(base + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(
                request, timeout=float(self._settings["request_timeout_seconds"])
            ) as reply:
                return json.loads(reply.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            text = error.read().decode("utf-8", errors="replace")
            if any(word in text.lower() for word in CONTEXT_WORDS):
                raise ContextWindowExceededError(text) from error
            raise ChatError(f"HTTP {error.code}: {text}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ChatError(f"{type(error).__name__}: {error}") from error

    async def __call__(
        self, messages: Sequence[dict[str, Any]], tools: Optional[Sequence[dict[str, Any]]]
    ) -> ChatReply:
        """Ask the model once and return its reply; the options come from the settings.

        Goes in: OpenAI-style messages and optional tools; comes out: a ChatReply. The model
        name and processor the server reported are kept for describe. A reply with no choice
        raises ChatError.
        """
        body: dict[str, Any] = {
            "model": self.requested_model,
            "messages": list(messages),
            **self._settings["options"],
        }
        if tools:
            body["tools"] = list(tools)
        raw = await asyncio.to_thread(self._post, "/v1/chat/completions", body)
        choices = raw.get("choices") or []
        if not choices:
            raise ChatError(f"The server returned no choice: {json.dumps(raw)[:300]}")
        message = choices[0].get("message") or {}
        self.calls += 1
        self.achieved_model = raw.get("model")
        await self._measure_processor()
        return ChatReply(
            text=message.get("content"),
            tool_calls=tuple(message.get("tool_calls") or ()),
            model=raw.get("model"),
            finish_reason=choices[0].get("finish_reason"),
        )

    async def _measure_processor(self) -> None:
        """Ask Ollama where the model it just ran is loaded and keep the answer.

        Reads /api/ps: a size_vram of 0 means the processor. If the server does not answer or the
        model is not listed the value is None, never a guess.
        """
        try:
            listing = await asyncio.to_thread(self._post, "/api/ps", None)
        except ChatError:
            self.achieved_processor = None
            return
        found = [
            m
            for m in listing.get("models", [])
            if m.get("name", "").split(":")[0] == self.requested_model.split(":")[0]
        ]
        if not found:
            self.achieved_processor = None
            return
        vram = found[0].get("size_vram", 0)
        self.achieved_processor = "cpu" if vram == 0 else f"gpu ({vram} bytes in video memory)"


class LettaModel:
    """The Letta part of the memory service: compaction, consolidation and dreaming by model.

    It is built from the language_model settings dict, a function that opens a Letta store and the
    letta settings (compaction, sleeptime, dream sections). Each step opens a store, runs the
    translated Letta code against it, closes the store and returns a plain dict. describe reports
    the model asked for and the model and processor the server reported on the last call. It never
    names a model in code.
    """

    def __init__(
        self,
        language_model: dict[str, Any],
        store_factory: Callable[[], LettaMemoryStore],
        letta_settings: dict[str, Any],
    ) -> None:
        """Check the three settings sections and build the chat function from language_model."""
        self._chat = OllamaChat(language_model)
        self._store_factory = store_factory
        self._compaction = parse_compaction_settings(letta_settings["compaction"])
        self._sleeptime = parse_sleeptime_settings(letta_settings["sleeptime"])
        self._dream = parse_dream_settings(letta_settings["dream"])

    def describe(self) -> dict[str, Any]:
        """Report the model asked for and the model and processor the server reported last.

        achieved_model and achieved_processor are None until a call has been made. It reads
        nothing and calls nothing.
        """
        return {
            "part": "letta",
            "requested_model": self._chat.requested_model,
            "achieved_model": self._chat.achieved_model,
            "achieved_processor": self._chat.achieved_processor,
            "calls": self._chat.calls,
        }

    async def _timed(
        self, step: str, work: Callable[[LettaMemoryStore], Awaitable[Any]]
    ) -> dict[str, Any]:
        """Open a store, run work on it, close it, and return the output with its timing and model.

        Goes in: the step name and the async work; comes out: a dict with part, step, seconds,
        the model description and the work's result. The store is closed even when the work
        fails, and the failure is raised unchanged.
        """
        store = self._store_factory()
        started = time.monotonic()
        try:
            ensure_state_tables(store)
            result = await work(store)
        finally:
            store.close()
        return {
            "part": "letta",
            "step": step,
            "seconds": round(time.monotonic() - started, 1),
            "model": self.describe(),
            "result": result,
        }

    async def compact(
        self,
        organization_id: str,
        agent_id: str,
        conversation_id: str,
        mode: Optional[str] = None,
        force: bool = True,
    ) -> dict[str, Any]:
        """Compact a conversation in the given mode (or the mode in the settings) and return the result.

        The summary is stored as a new message with role summary; nothing is deleted. A mode other
        than the four Letta has raises ValueError. The model used is the language_model setting.
        """
        settings = self._compaction
        if mode is not None:
            settings = settings.model_copy(update={"mode": mode, "prompt": None})
            parse_compaction_settings(settings.model_dump())

        async def work(store: LettaMemoryStore) -> dict[str, Any]:
            """Compact the conversation on the opened store."""
            return await compact_conversation(
                store, organization_id, agent_id, conversation_id, self._chat, settings, None, force
            )

        return await self._timed("compact", work)

    async def setup_sleeptime(
        self,
        organization_id: str,
        group_id: str,
        foreground_agent_id: str,
        sleeptime_agent_id: str,
        sleeptime_agent_frequency: Optional[int],
    ) -> dict[str, Any]:
        """Create a sleep-time group and give its agent the persona block and the shared blocks.

        Goes in: the ids and the frequency (null means every turn); comes out: what was created
        and shared. An existing group id raises ValueError. It runs no model.
        """

        async def work(store: LettaMemoryStore) -> dict[str, Any]:
            """Create the group and prepare the sleep-time agent on the opened store."""
            create_group(
                store,
                organization_id,
                group_id,
                foreground_agent_id,
                [sleeptime_agent_id],
                sleeptime_agent_frequency,
            )
            return prepare_sleeptime_agent(
                store, organization_id, foreground_agent_id, sleeptime_agent_id
            )

        return await self._timed("setup_sleeptime", work)

    async def consolidate(
        self, organization_id: str, group_id: str, message_ids: Optional[list[str]] = None
    ) -> dict[str, Any]:
        """Run the group's sleep-time agents over the foreground agent's new messages.

        message_ids names the new messages; with none given, every foreground message after
        the group's last processed one is used. The record per agent lists its steps, tool calls
        and the blocks that changed.
        """

        async def work(store: LettaMemoryStore) -> dict[str, Any]:
            """Pick the new messages and run the sleep-time agents on the opened store."""
            group = get_group(store, organization_id, group_id)
            everything = store.list_messages(organization_id, group.foreground_agent_id)
            if message_ids is not None:
                chosen = [m for m in everything if m.id in set(message_ids)]
            elif group.last_processed_message_id is None:
                chosen = everything
            else:
                ids = [m.id for m in everything]
                chosen = everything[ids.index(group.last_processed_message_id) + 1 :]
            new: list[Message] = chosen
            return await run_sleeptime_agents(
                store, organization_id, group_id, new, self._chat, self._sleeptime
            )

        return await self._timed("consolidate", work)

    async def dream(
        self,
        organization_id: str,
        agent_id: str,
        instruction: Optional[str] = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Dream for an agent: review its recent messages and edit its memory blocks.

        The dream settings decide whether it starts unless force is true. The result lists the
        steps, tool calls and changed blocks, and whether the agent's place in its history moved.
        """

        async def work(store: LettaMemoryStore) -> dict[str, Any]:
            """Run the dream on the opened store."""
            return await run_dream(
                store,
                organization_id,
                agent_id,
                self._chat,
                self._dream,
                instruction=instruction,
                force=force,
            )

        return await self._timed("dream", work)
