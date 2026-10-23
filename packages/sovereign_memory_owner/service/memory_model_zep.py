"""Zep's thread and user summaries inside the memory service, made from the language_model setting.

It is for keeping a running summary of each conversation (thread) and a summary of each user, with
the model the settings name, and for storing and checking a user's summary instructions. It takes
the validated language_model settings, a way to reach the database and the zep settings, and
answers the steps "set_instructions", "thread_summary", "get_summary" and "user_summary". The
thread summary is translated from Zep's own message summariser (graphiti_duckdb.zep_summarizer)
and the user summary from Graphiti's entity-summary prompt plus Zep's user summary instructions.
It never makes numbers (Zep's summary embedding waits on them) and never holds a database
connection across a model call.
"""

import math
import time
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from graphiti_core.llm_client.config import LLMConfig, ModelSize
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.prompts.extract_nodes import SummarizedEntities, extract_summaries_batch
from graphiti_core.utils.text_utils import MAX_SUMMARY_CHARS, truncate_at_sentence
from graphiti_duckdb.zep_ce_store import UserInstruction, ZepCeStore
from graphiti_duckdb.zep_summarizer import (
    SummarizerSettings,
    drop_empty_messages,
    is_due,
    settings_from,
    summarize,
)

from service.memory_model_parts import AchievedRecorder, DatabaseAccess, build_door


def _graph_facts(handle: Any, group_id: str) -> list[str]:
    """Return the still-valid fact texts Graphiti holds for a group, or none when it has no graph yet."""
    present = handle.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE database_name = current_database() "
        "AND schema_name = 'graphiti' AND table_name = 'graph_edge'"
    ).fetchone()[0]
    if not present:
        return []
    rows = handle.execute(
        "SELECT json_extract_string(properties, 'fact') FROM graphiti.graph_edge "
        "WHERE label = 'RELATES_TO' AND json_extract_string(properties, 'group_id') = ? "
        "AND json_extract_string(properties, 'invalid_at') IS NULL "
        "ORDER BY json_extract_string(properties, 'created_at'), uuid",
        [group_id],
    ).fetchall()
    return [row[0] for row in rows if row[0]]


class ZepModel:
    """The Zep part: thread summaries, user summaries and user summary instructions."""

    name = "zep"

    def __init__(
        self,
        language_model: Mapping[str, Any],
        database: DatabaseAccess,
        settings: Mapping[str, Any],
    ) -> None:
        """Keep the settings, build the HTTP door that records answers, and check the zep settings.

        settings holds message_window, summary_max_output_tokens, model_context_tokens,
        prompt_headroom_tokens and characters_per_token (all required, none defaulted) and
        optionally summarizer_prompt. Raises ValueError naming the first setting that is wrong.
        """
        self.language_model = dict(language_model)
        self.database = database
        self.recorder = AchievedRecorder()
        self.door = build_door(self.language_model, self.recorder)
        per_token = settings.get("characters_per_token")
        if isinstance(per_token, bool) or not isinstance(per_token, (int, float)) or per_token <= 0:
            raise ValueError("the setting characters_per_token must be a number above 0")
        self.settings: SummarizerSettings = settings_from(
            settings, lambda text: math.ceil(len(text) / per_token)
        )

    def steps(self) -> tuple[str, ...]:
        """Return the step names this part answers."""
        return ("set_instructions", "thread_summary", "get_summary", "user_summary")

    def achieved_model(self) -> str | None:
        """Return the model name the server reported for the latest answer, or None before any."""
        return self.recorder.last_model

    async def run_step(self, step: str, args: dict[str, Any], model: str) -> dict[str, Any]:
        """Run one step with the named model; every step needs project_uuid and its own id argument."""
        project = UUID(str(args.get("project_uuid", "")))
        await self.database.write(lambda handle: ZepCeStore(handle, project).ensure_schema())
        if step == "set_instructions":
            return await self._set_instructions(project, args)
        if step == "get_summary":
            return await self._get_summary(project, self._text(args, "session_id"))
        if step == "thread_summary":
            return await self._thread_summary(project, self._text(args, "session_id"), model)
        return await self._user_summary(project, self._text(args, "user_id"), model)

    @staticmethod
    def _text(args: Mapping[str, Any], key: str) -> str:
        """Return a required non-empty text argument, or raise ValueError naming it."""
        value = args.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"this step needs {key}")
        return value

    async def _set_instructions(self, project: UUID, args: Mapping[str, Any]) -> dict[str, Any]:
        """Store a user's summary instructions, refusing a list that breaks Zep's published limits."""
        user_id = self._text(args, "user_id")
        raw = args.get("instructions")
        if not isinstance(raw, list) or any(
            not isinstance(i, dict)
            or not isinstance(i.get("name"), str)
            or not isinstance(i.get("text"), str)
            for i in raw
        ):
            raise ValueError("instructions must be a list of objects with a name and a text")
        wanted = [UserInstruction(i["name"], i["text"]) for i in raw]

        def write(handle: Any) -> list[UserInstruction]:
            """Replace the user's instructions and read them back."""
            store = ZepCeStore(handle, project)
            store.set_user_instructions(user_id, wanted)
            return store.get_user_instructions(user_id)

        stored = await self.database.write(write)
        return {"instructions": [{"name": i.name, "text": i.text} for i in stored]}

    async def _get_summary(self, project: UUID, session_id: str) -> dict[str, Any]:
        """Return a session's newest summary and the messages after its summary point."""

        def read(handle: Any) -> Any:
            """Read the summary and the unsummarised messages; no model."""
            store = ZepCeStore(handle, project)
            store.get_session(session_id)
            return store.get_memory_with_summary(session_id, 0, self.settings.message_window)

        memory = await self.database.read(read)
        summary = memory.summary
        return {
            "summary": None if summary is None else summary.content,
            "summary_point_uuid": None if summary is None else str(summary.summary_point_uuid),
            "summary_tokens": None if summary is None else summary.token_count,
            "messages_after_summary_point": len(memory.messages),
        }

    async def _thread_summary(self, project: UUID, session_id: str, model: str) -> dict[str, Any]:
        """Summarise a session when its unsummarised messages reach the message window.

        Original: getzep/zep at 411142c^, pkg/tasks/message_summarizer.go, MessageSummaryTask.Execute.
        Returns outcome "not_due" with the counts when the window is not reached, or "summarised"
        with the new summary, its summary point and how many messages were folded in and kept.
        """

        def read(handle: Any) -> Any:
            """Read the newest summary and the unsummarised messages; no model."""
            store = ZepCeStore(handle, project)
            store.get_session(session_id)
            summary = store.get_summary(session_id)
            return summary, store.messages_since_last_summary(
                session_id, summary, self.settings.message_window
            )

        previous, messages = await self.database.read(read)
        messages = drop_empty_messages(messages)
        if not is_due(messages, self.settings):
            return {
                "outcome": "not_due",
                "unsummarised_messages": len(messages),
                "message_window": self.settings.message_window,
            }

        async def complete(prompt: str, max_tokens: int) -> str:
            """Ask the model for one summary with the prompt as a single user message."""
            answer = await self.door.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
            return answer.choices[0].message.content or ""

        calls_before = self.recorder.calls
        started = time.monotonic()
        result = await summarize(
            messages, None if previous is None else previous.content, self.settings, complete
        )
        model_seconds = round(time.monotonic() - started, 2)

        def write(handle: Any) -> Any:
            """Store the summary with its summary point."""
            return ZepCeStore(handle, project).create_summary(
                session_id, result.content, result.summary_point_uuid, result.token_count
            )

        stored = await self.database.write(write)
        kept = self.settings.message_window // 2
        return {
            "outcome": "summarised",
            "summary": stored.content,
            "summary_uuid": str(stored.uuid),
            "summary_point_uuid": str(stored.summary_point_uuid),
            "summary_tokens_estimated": stored.token_count,
            "messages_folded_in": len(messages) - kept,
            "messages_kept": kept,
            "model_calls": self.recorder.calls - calls_before,
            "model_seconds": model_seconds,
            "builds_on_previous_summary": previous is not None,
        }

    @staticmethod
    def _user_prompt(
        user_name: str, prior: str, new_lines: str, instructions: list[UserInstruction]
    ) -> list[Any]:
        """Build Graphiti's entity-summary messages for the user, with the user's instructions added.

        Original: graphiti_core/prompts/extract_nodes.py, extract_summaries_batch (the prompt that
        combines the new messages with the existing summary). Zep publishes no prompt for user
        summary instructions, so they are added as one clearly marked block after Graphiti's text.
        """
        messages = extract_summaries_batch(
            {
                "previous_episodes": [],
                "episode_content": new_lines,
                "entities": [
                    {
                        "name": user_name,
                        "summary": prior,
                        "entity_types": ["User"],
                        "attributes": {},
                    }
                ],
            }
        )
        if instructions:
            block = "\n".join(f"- {i.name}: {i.text}" for i in instructions)
            messages[-1].content += (
                "\n<USER SUMMARY INSTRUCTIONS>\n"
                f"For the ENTITY {user_name}, the summary should answer these when the MESSAGES "
                "support an answer, and leave a question out when they do not:\n"
                f"{block}\n</USER SUMMARY INSTRUCTIONS>\n"
            )
        return messages

    async def _user_summary(self, project: UUID, user_id: str, model: str) -> dict[str, Any]:
        """Update a user's summary from the previous summary, the user's new messages and valid facts.

        Original: Graphiti's entity summary ("previous summary plus the new information",
        graphiti_core/utils/maintenance/node_operations.py, _process_summary_flight) with Zep's user
        summary instructions. Returns outcome "nothing_new" when no message or fact is new.
        """

        def read(handle: Any) -> Any:
            """Read the user, instructions, stored summary, new messages and valid facts; no model."""
            store = ZepCeStore(handle, project)
            user = store.get_user(user_id)
            stored = store.get_user_summary(user_id)
            through = 0 if stored is None else stored[1]
            return (
                user,
                store.get_user_instructions(user_id),
                stored,
                store.user_messages_after(user_id, through),
                _graph_facts(handle, user_id),
            )

        user, instructions, stored, fresh, facts = await self.database.read(read)
        prior = "" if stored is None else stored[0]
        lines = [
            f"[{session}] {message.role}: {message.content}"
            for _, session, message in fresh
            if message.content.strip()
        ]
        if not lines and not facts:
            return {"outcome": "nothing_new", "summary": prior or None}
        if stored is not None and not lines:
            return {"outcome": "nothing_new", "summary": prior}
        name = " ".join(part for part in (user.first_name, user.last_name) if part) or user.user_id
        new_lines = "\n".join(lines)
        if facts:
            new_lines += (
                "\nFACTS the graph holds about " + name + ":\n" + "\n".join(f"- {f}" for f in facts)
            )
        options = self.language_model["options"]
        client = OpenAIGenericClient(
            config=LLMConfig(
                api_key=self.language_model["api_key"],
                model=model,
                small_model=model,
                base_url=self.language_model["base_url"].rstrip("/") + "/v1",
                **{key: options[key] for key in ("temperature", "max_tokens") if key in options},
            ),
            client=self.door,
        )
        started = time.monotonic()
        response = await client.generate_response(
            self._user_prompt(name, prior, new_lines, instructions),
            response_model=SummarizedEntities,
            model_size=ModelSize.small,
        )
        model_seconds = round(time.monotonic() - started, 2)
        answer = next(
            (
                s.summary
                for s in SummarizedEntities(**response).summaries
                if s.name.lower() == name.lower()
            ),
            None,
        )
        if not answer:
            raise ValueError(f"the model returned no summary for {name}")
        summary = truncate_at_sentence(answer, MAX_SUMMARY_CHARS)
        through = (
            max(identifier for identifier, _, _ in fresh) if fresh else (stored[1] if stored else 0)
        )

        await self.database.write(
            lambda handle: ZepCeStore(handle, project).put_user_summary(user_id, summary, through)
        )
        return {
            "outcome": "summarised",
            "summary": summary,
            "instructions_used": [i.name for i in instructions],
            "messages_folded_in": len(lines),
            "facts_used": len(facts),
            "builds_on_previous_summary": bool(prior),
            "model_seconds": model_seconds,
        }
