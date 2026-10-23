"""Mem0's language-model steps inside the memory service, made from the language_model setting.

It is for running Mem0's own fact extraction, its ADD/UPDATE/DELETE/NONE decision and its
procedural-memory text with the model the settings name, on the personalization tier's own
Mem0 object. It takes the tier and a function that turns a model name into Mem0's llm
section, and answers the steps "extract_facts", "decide_actions" and "procedural". It never
stores a memory (storing needs numbers memory does not make) and never invents an id.
"""

from typing import Any, Callable

from mem0.configs.prompts import PROCEDURAL_MEMORY_SYSTEM_PROMPT
from mem0.exceptions import LLMError
from mem0.memory.main import _build_filters_and_metadata
from mem0.memory.owner_tier import Mem0Tier
from mem0.memory.utils import remove_code_blocks
from service.memory_model_parts import AchievedRecorder


class Mem0Model:
    """The Mem0 part: fact extraction, the action decision and procedural text."""

    name = "mem0"

    def __init__(self, tier: Mem0Tier, llm_section_for: Callable[[str], dict[str, Any]]) -> None:
        """Keep the personalization tier and the function that makes an llm section for a model name."""
        self.tier = tier
        self.llm_section_for = llm_section_for
        self.recorder = AchievedRecorder()

    def steps(self) -> tuple[str, ...]:
        """Return the step names this part answers."""
        return ("extract_facts", "decide_actions", "procedural")

    def achieved_model(self) -> str | None:
        """Return the model name the server reported for the latest answer, or None before any."""
        return self.recorder.last_model

    async def _memory(self, model: str) -> Any:
        """Return the tier's Mem0 object with a model client for the named model that records answers."""
        memory = await self.tier.memory_with_language_model(self.llm_section_for(model))
        client = memory.llm.client
        original = client.chat.completions.create
        recorder = self.recorder

        def create(*args: Any, **kwargs: Any) -> Any:
            """Send the request and remember which model the server says answered."""
            reply = original(*args, **kwargs)
            recorder.last_model = reply.model
            recorder.calls += 1
            return reply

        client.chat.completions.create = create
        return memory

    async def run_step(self, step: str, args: dict[str, Any], model: str) -> dict[str, Any]:
        """Run one step with the named model and return its real output."""
        import asyncio

        memory = await self._memory(model)
        if step == "extract_facts":
            messages = self._messages(args)
            user = args.get("user_id")
            if not isinstance(user, str) or not user:
                raise ValueError("extract_facts needs a user_id")
            metadata, filters = _build_filters_and_metadata(user_id=user)
            facts = await asyncio.to_thread(memory._extract_facts, messages, metadata, filters)
            return {"facts": facts}
        if step == "decide_actions":
            existing, facts = args.get("existing"), args.get("facts")
            if not isinstance(existing, list) or not isinstance(facts, list):
                raise ValueError("decide_actions needs existing (a list of id and text) and facts")
            try:
                decisions, mapping = await asyncio.to_thread(
                    memory._decide_memory_actions, existing, facts
                )
            except (ValueError, LLMError) as refusal:
                return {"outcome": "refused", "reason": f"{type(refusal).__name__}: {refusal}"}
            return {"outcome": "decided", "decisions": decisions, "id_map": mapping}
        messages = self._messages(args)
        text = await asyncio.to_thread(
            memory.llm.generate_response,
            messages=[
                {"role": "system", "content": args.get("prompt") or PROCEDURAL_MEMORY_SYSTEM_PROMPT},
                *messages,
                {"role": "user", "content": "Create procedural memory of the above conversation."},
            ],
        )
        return {"procedural_memory": remove_code_blocks(text)}

    @staticmethod
    def _messages(args: dict[str, Any]) -> list[dict[str, str]]:
        """Return the step's messages, or raise ValueError saying what is missing or malformed."""
        messages = args.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("this step needs messages, a non-empty list of role and content")
        for message in messages:
            if (
                not isinstance(message, dict)
                or not isinstance(message.get("role"), str)
                or not isinstance(message.get("content"), str)
            ):
                raise ValueError("every message needs a role and a content, both text")
        return messages
