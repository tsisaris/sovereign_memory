"""Dreaming: when it starts, and a dream that reviews recent messages and edits the memory blocks.

It is for Letta's name for sleep-time compute, as Letta Code runs it: the trigger rules of
src/cli/helpers/post-turn-reflection.ts and memory-reminder.ts (shouldFireStepCountTrigger),
and the reflection subagent of src/agent/subagents/builtin/reflection.md reading a transcript of
the messages since its last successful run. What goes in is a store, an agent, a chat function
and the dream settings; what comes out is a record of the dream and the blocks it changed. It
never edits a block except through the memory tools, and it does not advance its place in the
history when a dream is cut off by the step limit.
"""

from typing import Any, Optional

from letta_memory_model.chat import Chat
from letta_memory_model.letta_duckdb_store import LettaMemoryStore
from letta_memory_model.messages import SUMMARY_ROLE, message_text
from letta_memory_model.model_state import (
    ensure_state_tables,
    get_dream_sequence,
    set_dream_sequence,
)
from letta_memory_model.schemas.message import Message
from letta_memory_model.sleeptime.dream_prompt import (
    DREAM_INSTRUCTION_TEMPLATE,
    DREAM_SYSTEM_PROMPT,
    DREAM_USER_TEMPLATE,
)
from letta_memory_model.sleeptime.loop import run_memory_agent
from letta_memory_model.sleeptime.settings import DreamSettings
from letta_memory_model.sleeptime.tools import (
    SLEEPTIME_TOOL_NAMES,
    SLEEPTIME_TOOL_SCHEMAS,
    SleeptimeTools,
)


def messages_since_last_dream(
    store: LettaMemoryStore, organization_id: str, agent_id: str
) -> list[Message]:
    """Return the agent's messages stored after the last successful dream, oldest first.

    Goes in: the store and the agent; comes out: the messages with a sequence id above the
    saved one (all of them if the agent has never dreamed). It only reads.
    """
    ensure_state_tables(store)
    after = get_dream_sequence(store, organization_id, agent_id)
    return store.list_messages(organization_id, agent_id, after_sequence_id=after or None)


def should_dream(settings: DreamSettings, new_messages: list[Message]) -> tuple[bool, str]:
    """Say whether a dream should start now, and why or why not.

    Translated from maybeLaunchPostTurnReflection and shouldFireStepCountTrigger: trigger
    off never fires; step-count fires when the new assistant messages (the steps) reach
    step_count; compaction-event fires when a summary message arrived since the last dream.
    It reads only its arguments.
    """
    if settings.trigger == "off":
        return False, "dreaming is off"
    if settings.trigger == "step-count":
        steps = sum(1 for m in new_messages if m.role == "assistant")
        if steps >= settings.step_count:
            return True, f"{steps} new steps reached the step count {settings.step_count}"
        return False, f"{steps} new steps, fewer than the step count {settings.step_count}"
    compactions = sum(1 for m in new_messages if m.role == SUMMARY_ROLE)
    if compactions:
        return True, f"{compactions} compaction(s) since the last dream"
    return False, "no compaction since the last dream"


def _transcript(messages: list[Message]) -> str:
    """Write messages as the plain transcript a dream reads, one "role: text" line each.

    Tool messages and empty messages are left out. It never changes the messages.
    """
    lines = []
    for message in messages:
        text = message_text(message)
        if message.role != "tool" and text:
            lines.append(f"{message.role}: {text}")
    return "\n".join(lines)


async def dream(
    store: LettaMemoryStore,
    organization_id: str,
    agent_id: str,
    chat: Chat,
    settings: DreamSettings,
    instruction: Optional[str] = None,
    force: bool = False,
    actor_id: Optional[str] = None,
) -> dict[str, Any]:
    """Run one dream for an agent when its trigger says so (or when forced) and report it.

    The dream reads the messages since the last successful dream, runs the reflection agent
    with the memory tools on the agent's own blocks until memory_finish_edits, no tool call
    or max_steps, and returns what it did and which blocks changed. The place in the history
    advances only when the dream ended by itself (terminal tool or end of turn). With nothing
    new to read it does not run.
    """
    new_messages = messages_since_last_dream(store, organization_id, agent_id)
    fire, reason = should_dream(settings, new_messages)
    if not fire and not force:
        return {"ran": False, "reason": reason}
    transcript = _transcript(new_messages)
    if not transcript:
        return {"ran": False, "reason": "no new messages to review"}
    extra = (
        DREAM_INSTRUCTION_TEMPLATE.format(instruction=instruction.strip()) if instruction else ""
    )
    user_text = DREAM_USER_TEMPLATE.format(instruction=extra, transcript=transcript)
    before = {b.label: b.value for b in store.list_agent_blocks(organization_id, agent_id)}

    def system_prompt() -> str:
        """Build the dream system prompt: the reflection instructions plus the agent's memory."""
        return (
            DREAM_SYSTEM_PROMPT + "\n" + store.get_agent_memory(organization_id, agent_id).compile()
        )

    result = await run_memory_agent(
        chat,
        system_prompt,
        user_text,
        SLEEPTIME_TOOL_SCHEMAS,
        SleeptimeTools(store, organization_id, agent_id, actor_id),
        settings.max_steps,
    )
    after = {b.label: b.value for b in store.list_agent_blocks(organization_id, agent_id)}
    changed = {
        label: {"before": before.get(label), "after": value}
        for label, value in after.items()
        if before.get(label) != value
    }
    finished = result.stop_reason in ("terminal_tool", "end_turn")
    if finished:
        top = max(m.sequence_id or 0 for m in new_messages)
        set_dream_sequence(store, organization_id, agent_id, top)
    return {
        "ran": True,
        "reason": reason,
        "forced": force and not fire,
        "tools_offered": list(SLEEPTIME_TOOL_NAMES),
        "messages_reviewed": len(new_messages),
        "steps": result.steps,
        "stop_reason": result.stop_reason,
        "tool_calls": result.tool_calls,
        "models_reported": sorted(set(result.models)),
        "changed_blocks": changed,
        "advanced": finished,
    }
