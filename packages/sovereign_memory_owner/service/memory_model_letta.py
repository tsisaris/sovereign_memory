"""Letta's language-model steps inside the memory service: compaction, consolidation and dreaming.

It is the service's door to the translated Letta code in letta_memory_model (summarizer and
sleeptime), as one part of the model router (memory_model_parts.py). What goes in is the
language_model settings, the service's database access and the letta settings sections
(compaction, sleeptime, dream); what comes out, per step, is a plain dict with the real output.
It makes its model door from the settings only, takes the model for each call from the router,
reports the model the server's reply named, and never holds the database across a model call.
"""

from typing import Any, Mapping, Optional

from letta_memory_model.chat import ChatError, ChatReply, ContextWindowExceededError
from letta_memory_model.letta_duckdb_store import LettaMemoryStore
from letta_memory_model.sleeptime.consolidate import run_sleeptime_agents, setup_sleeptime
from letta_memory_model.sleeptime.dreaming import dream as run_dream
from letta_memory_model.sleeptime.settings import parse_dream_settings, parse_sleeptime_settings
from letta_memory_model.summarizer.conversation import compact_conversation
from letta_memory_model.summarizer.settings import parse_compaction_settings
from openai import APIConnectionError, APIStatusError

from service.memory_model_parts import (
    DEFAULTED_REQUEST_OPTIONS,
    AchievedRecorder,
    DatabaseAccess,
    build_door,
)

CONTEXT_WORDS = ("context length", "context window", "too many tokens", "prompt is too long")
STEPS = ("compact", "setup_sleeptime", "consolidate", "dream")


class LettaStoreAccess:
    """Gives the translated Letta code a store over the service's database access.

    It wraps the service's DatabaseAccess: a read or a write runs the given function with a
    LettaMemoryStore built on the handle the service supplies, and the store is released when the
    function returns. It never opens a connection of its own and is never held across a model call.
    """

    def __init__(self, database: DatabaseAccess) -> None:
        """Keep the service's database access."""
        self._database = database

    async def read(self, work: Any) -> Any:
        """Run work(store) as a read on the owner's handle and return its result."""
        return await self._database.read(lambda handle: self._with_store(handle, work))

    async def write(self, work: Any) -> Any:
        """Run work(store) in one committed transaction and return its result."""
        return await self._database.write(lambda handle: self._with_store(handle, work))

    @staticmethod
    def _with_store(handle: Any, work: Any) -> Any:
        """Build a store on the handle, run work on it and release the store (not the handle)."""
        store = LettaMemoryStore(handle)
        try:
            return work(store)
        finally:
            store.close()


class LettaModel:
    """The Letta part of the memory service: compaction, consolidation and dreaming by model.

    It follows the ModelPart protocol: name, steps, achieved_model and run_step. It is built from
    the language_model settings, the service's database access and the letta settings. Each step
    reads what it needs, calls the model the router names, then writes; the model reported by the
    server's latest reply is what achieved_model returns. It names no model in code.
    """

    name = "letta"

    def __init__(
        self,
        language_model: Mapping[str, Any],
        database: DatabaseAccess,
        letta_settings: Mapping[str, Any],
    ) -> None:
        """Check the three settings sections and build the one door that records what answered."""
        self.language_model = dict(language_model)
        self.access = LettaStoreAccess(database)
        self.recorder = AchievedRecorder()
        self.door = build_door(self.language_model, self.recorder)
        self._compaction = parse_compaction_settings(dict(letta_settings["compaction"]))
        self._sleeptime = parse_sleeptime_settings(dict(letta_settings["sleeptime"]))
        self._dream = parse_dream_settings(dict(letta_settings["dream"]))
        self._request_options = {
            key: value
            for key, value in self.language_model["options"].items()
            if key not in DEFAULTED_REQUEST_OPTIONS
        }

    def steps(self) -> tuple[str, ...]:
        """Return the step names this part answers."""
        return STEPS

    def achieved_model(self) -> Optional[str]:
        """Return the model name the server reported for the latest answer, or None before any."""
        return self.recorder.last_model

    def _chat(self, model: str) -> Any:
        """Return a chat function that asks the named model through the recorded door.

        The function takes OpenAI-style messages and optional tools and returns a ChatReply. A
        server refusal or a lost connection becomes a ChatError (a ContextWindowExceededError when
        the server's words say the request was too long). It never retries.
        """

        async def chat(messages: Any, tools: Any) -> ChatReply:
            """Ask the model once and convert its answer to a ChatReply."""
            request: dict[str, Any] = {
                "model": model,
                "messages": list(messages),
                **self._request_options,
            }
            if tools:
                request["tools"] = list(tools)
            try:
                response = await self.door.chat.completions.create(**request)
            except APIStatusError as error:
                if any(word in str(error).lower() for word in CONTEXT_WORDS):
                    raise ContextWindowExceededError(str(error)) from error
                raise ChatError(f"HTTP {error.status_code}: {error}") from error
            except APIConnectionError as error:
                raise ChatError(f"{type(error).__name__}: {error}") from error
            if not response.choices:
                raise ChatError("The server returned no choice")
            choice = response.choices[0]
            calls = tuple(
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in (choice.message.tool_calls or ())
            )
            return ChatReply(
                text=choice.message.content,
                tool_calls=calls,
                model=response.model,
                finish_reason=choice.finish_reason,
            )

        return chat

    @staticmethod
    def _text(args: dict[str, Any], key: str) -> str:
        """Return a required non-empty text argument or raise ValueError naming it."""
        value = args.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"the argument {key} is required and must be non-empty text")
        return value

    async def run_step(self, step: str, args: dict[str, Any], model: str) -> dict[str, Any]:
        """Run one step with the named model and return its plain output.

        compact takes organization_id, agent_id, conversation_id and optionally mode and force;
        setup_sleeptime takes organization_id, group_id, foreground_agent_id, sleeptime_agent_id
        and sleeptime_agent_frequency (a whole number or null); consolidate takes
        organization_id, group_id and optionally message_ids; dream takes organization_id,
        agent_id and optionally instruction and force. A missing or wrong argument raises
        ValueError before any model call.
        """
        org = self._text(args, "organization_id")
        chat = self._chat(model)
        if step == "compact":
            mode = args.get("mode")
            settings = self._compaction
            if mode is not None:
                settings = parse_compaction_settings(
                    {**settings.model_dump(), "mode": mode, "prompt": None}
                )
            return await compact_conversation(
                self.access,
                org,
                self._text(args, "agent_id"),
                self._text(args, "conversation_id"),
                chat,
                settings,
                None,
                bool(args.get("force", True)),
            )
        if step == "setup_sleeptime":
            frequency = args.get("sleeptime_agent_frequency")
            if frequency is not None and (
                isinstance(frequency, bool) or not isinstance(frequency, int)
            ):
                raise ValueError("sleeptime_agent_frequency must be a whole number or null")
            return await setup_sleeptime(
                self.access,
                org,
                self._text(args, "group_id"),
                self._text(args, "foreground_agent_id"),
                self._text(args, "sleeptime_agent_id"),
                frequency,
            )
        if step == "consolidate":
            ids = args.get("message_ids")
            if ids is not None and not (
                isinstance(ids, list) and all(isinstance(i, str) for i in ids)
            ):
                raise ValueError("message_ids must be a list of message ids or null")
            return await run_sleeptime_agents(
                self.access, org, self._text(args, "group_id"), ids, chat, self._sleeptime
            )
        if step == "dream":
            return await run_dream(
                self.access,
                org,
                self._text(args, "agent_id"),
                chat,
                self._dream,
                instruction=args.get("instruction"),
                force=bool(args.get("force", False)),
            )
        raise ValueError(f"part 'letta' has no step {step!r}; its steps are {', '.join(STEPS)}")
