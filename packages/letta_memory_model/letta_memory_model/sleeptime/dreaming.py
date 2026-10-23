"""Dreaming: when it starts, and a dream that reviews recent messages and edits the memory blocks.

It is for Letta's name for sleep-time compute, as Letta Code runs it: the trigger rules of
src/cli/helpers/post-turn-reflection.ts and memory-reminder.ts (shouldFireStepCountTrigger),
and the reflection subagent of src/agent/subagents/builtin/reflection.md reading a transcript of
the messages since its last successful run. What goes in is a StoreAccess, an agent, a chat
function and the dream settings; what comes out is a record of the dream and the blocks it
changed. It reads, calls the model, then writes, and never holds the store across a model call;
it edits a block only through the memory tools, and it does not advance its place in the history
when a dream is cut off by the step limit.
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
    ToolOutcome,
)
from letta_memory_model.store_access import StoreAccess


def messages_since_last_dream(
    store: LettaMemoryStore, organization_id: str, agent_id: str
) -> list[Message]:
    """Return the agent's messages stored after the last successful dream, oldest first.

    Goes in: the store and the agent; comes out: the messages with a sequence id above the
    saved one (all of them if the agent has never dreamed). It only reads, and the state table
    must already exist.
    """
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


def _block_values(store: LettaMemoryStore, organization_id: str, agent_id: str) -> dict[str, str]:
    """Return the label and value of every block attached to an agent.

    It only reads, and is used to show which blocks a dream changed.
    """
    return {b.label: b.value for b in store.list_agent_blocks(organization_id, agent_id)}


async def dream(
    access: StoreAccess,
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
    await access.write(ensure_state_tables)

    def start(store: LettaMemoryStore) -> tuple[list[Message], dict[str, str]]:
        """Read the messages since the last dream and the blocks as they are before it."""
        return (
            messages_since_last_dream(store, organization_id, agent_id),
            _block_values(store, organization_id, agent_id),
        )

    new_messages, before = await access.read(start)
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

    async def system_prompt() -> str:
        """Build the dream system prompt: the reflection instructions plus the agent's memory."""
        memory = await access.read(
            lambda store: store.get_agent_memory(organization_id, agent_id).compile()
        )
        return DREAM_SYSTEM_PROMPT + "\n" + memory

    async def run_tool(name: str, arguments: str) -> ToolOutcome:
        """Run one tool call as its own committed write; a refusal rolls back and is returned."""

        def work(store: LettaMemoryStore) -> ToolOutcome:
            """Run the tool on the transaction's store."""
            return SleeptimeTools(store, organization_id, agent_id, actor_id).run(name, arguments)

        return await access.write(work)

    result = await run_memory_agent(
        chat, system_prompt, user_text, SLEEPTIME_TOOL_SCHEMAS, run_tool, settings.max_steps
    )
    finished = result.stop_reason in ("terminal_tool", "end_turn")
    top = max(m.sequence_id or 0 for m in new_messages)

    def finish(store: LettaMemoryStore) -> dict[str, str]:
        """Move the dream's place forward when it ended by itself, and read the blocks after."""
        if finished:
            set_dream_sequence(store, organization_id, agent_id, top)
        return _block_values(store, organization_id, agent_id)

    after = await access.write(finish)
    changed = {
        label: {"before": before.get(label), "after": value}
        for label, value in after.items()
        if before.get(label) != value
    }
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
