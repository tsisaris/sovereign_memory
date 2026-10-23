"""Sleep-time consolidation: after a conversation turn, background agents tidy the memory blocks.

It is for the group logic of Letta's SleeptimeMultiAgentV4 (letta/groups/
sleeptime_multi_agent_v4.py: run_sleeptime_agents, _issue_background_task and
_participant_agent_step) with its counters from letta/services/group_manager.py. What
goes in is a StoreAccess, a group id, the foreground agent's new messages, a chat function and
the sleep-time settings; what comes out is a record per sleep-time agent of what it did
and which blocks changed. It reads, calls the model, then writes, and never holds the store
across a model call; it never runs the foreground agent, and it runs each sleep-time
agent to the end before returning instead of in the background (the run records of the
original are left out).
"""

from typing import Any, Optional, Sequence

from letta_memory_model.chat import Chat
from letta_memory_model.constants import CORE_MEMORY_BLOCK_CHAR_LIMIT
from letta_memory_model.letta_duckdb_store import LettaMemoryStore
from letta_memory_model.messages import stringify_message
from letta_memory_model.model_state import (
    GroupState,
    bump_turns_counter,
    create_group,
    ensure_state_tables,
    get_group,
    swap_last_processed_message_id,
)
from letta_memory_model.schemas.block import Block
from letta_memory_model.schemas.message import Message
from letta_memory_model.sleeptime.loop import LoopResult, run_memory_agent
from letta_memory_model.sleeptime.prompts import (
    SLEEPTIME_BASE_INSTRUCTIONS,
    SLEEPTIME_PERSONA,
    SLEEPTIME_TASK_TEMPLATE,
)
from letta_memory_model.sleeptime.settings import SleeptimeSettings
from letta_memory_model.sleeptime.tools import (
    SLEEPTIME_TOOL_NAMES,
    SLEEPTIME_TOOL_SCHEMAS,
    SleeptimeTools,
    ToolOutcome,
)
from letta_memory_model.store_access import StoreAccess

PERSONA_LABEL = "memory_persona"


def _prepare(
    store: LettaMemoryStore,
    organization_id: str,
    foreground_agent_id: str,
    sleeptime_agent_id: str,
    actor_id: Optional[str],
) -> dict[str, Any]:
    """Give a sleep-time agent its persona block and the foreground agent's blocks, shared.

    Translated from how Letta's server builds a sleep-time agent: it gets a memory_persona
    block holding the sleep-time persona text (sleeptime_memory_persona.txt) and every
    block of the foreground agent is attached to it too, so edits reach both. A label the
    sleep-time agent already holds is skipped, not replaced. It never edits a block's text.
    """
    attached = {b.label for b in store.list_agent_blocks(organization_id, sleeptime_agent_id)}
    created = []
    if PERSONA_LABEL not in attached:
        persona = store.create_block(
            organization_id,
            Block(label=PERSONA_LABEL, value=SLEEPTIME_PERSONA, limit=CORE_MEMORY_BLOCK_CHAR_LIMIT),
            actor_id=actor_id,
        )
        store.attach_block(organization_id, sleeptime_agent_id, persona.id)
        created.append(persona.label)
    shared = []
    for block in store.list_agent_blocks(organization_id, foreground_agent_id):
        if block.label in attached or block.label == PERSONA_LABEL:
            continue
        store.attach_block(organization_id, sleeptime_agent_id, block.id)
        shared.append(block.label)
    return {"created": created, "shared": shared}


async def setup_sleeptime(
    access: StoreAccess,
    organization_id: str,
    group_id: str,
    foreground_agent_id: str,
    sleeptime_agent_id: str,
    sleeptime_agent_frequency: Optional[int],
    actor_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create a sleep-time group and prepare its agent, in one committed write.

    Goes in: the ids and the frequency (null means every turn); comes out: what was created
    and shared. An existing group id raises ValueError and writes nothing. It runs no model.
    """
    await access.write(ensure_state_tables)

    def work(store: LettaMemoryStore) -> dict[str, Any]:
        """Create the group and prepare the sleep-time agent on the transaction's store."""
        create_group(
            store,
            organization_id,
            group_id,
            foreground_agent_id,
            [sleeptime_agent_id],
            sleeptime_agent_frequency,
        )
        return _prepare(store, organization_id, foreground_agent_id, sleeptime_agent_id, actor_id)

    return await access.write(work)


def _block_values(store: LettaMemoryStore, organization_id: str, agent_id: str) -> dict[str, str]:
    """Return the label and value of every block attached to an agent.

    It only reads, and is used to show which blocks a run changed.
    """
    return {b.label: b.value for b in store.list_agent_blocks(organization_id, agent_id)}


def _prior_messages(
    store: LettaMemoryStore,
    organization_id: str,
    foreground_agent_id: str,
    response_messages: Sequence[Message],
    last_processed_message_id: Optional[str],
) -> tuple[list[Message], Optional[str]]:
    """Return the foreground messages between the last processed one and the new ones.

    Translated from the message_manager.list_messages(after=, before=) call in
    _participant_agent_step. When the ids cannot be found the original carries on with just
    the latest messages; here the problem is returned as a note so it is not silent.
    """
    if last_processed_message_id is None:
        return [], None
    stored = store.list_messages(organization_id, foreground_agent_id)
    ids = [m.id for m in stored]
    try:
        start = ids.index(last_processed_message_id) + 1
        end = ids.index(response_messages[0].id)
    except ValueError:
        return [], "prior messages unavailable: a message id was not found in the agent's recall"
    return stored[start:end], None


async def _participant_agent_step(
    access: StoreAccess,
    organization_id: str,
    foreground_agent_id: str,
    sleeptime_agent_id: str,
    response_messages: Sequence[Message],
    last_processed_message_id: Optional[str],
    frequency: Optional[int],
    chat: Chat,
    settings: SleeptimeSettings,
    actor_id: Optional[str],
) -> dict[str, Any]:
    """Run one sleep-time agent over the new messages and report what it did.

    Translated from _participant_agent_step: the new messages (with earlier unprocessed
    ones when a frequency is set) become the system-reminder message, and the agent runs
    with its four memory tools until memory_finish_edits, no tool call, or max_steps. Each
    model call is made with the store released; each tool call is its own committed write.
    The blocks that changed are returned with before and after text.
    """

    def start(store: LettaMemoryStore) -> tuple[list[Message], Optional[str], dict[str, str]]:
        """Read the earlier unprocessed messages and the blocks as they are before the run."""
        prior: list[Message] = []
        note = None
        if frequency:
            prior, note = _prior_messages(
                store,
                organization_id,
                foreground_agent_id,
                response_messages,
                last_processed_message_id,
            )
        return prior, note, _block_values(store, organization_id, sleeptime_agent_id)

    prior, note, before = await access.read(start)
    lines = [stringify_message(m) for m in [*prior, *response_messages]]
    messages_text = "\n".join(line for line in lines if line is not None)
    task = SLEEPTIME_TASK_TEMPLATE.format(messages_text=messages_text)

    async def system_prompt() -> str:
        """Build the sleep-time system prompt: base instructions plus the agent's current memory."""
        memory = await access.read(
            lambda store: store.get_agent_memory(organization_id, sleeptime_agent_id).compile()
        )
        return SLEEPTIME_BASE_INSTRUCTIONS + "\n" + memory

    async def run_tool(name: str, arguments: str) -> ToolOutcome:
        """Run one tool call as its own committed write; a refusal rolls back and is returned."""

        def work(store: LettaMemoryStore) -> ToolOutcome:
            """Run the tool on the transaction's store."""
            return SleeptimeTools(store, organization_id, sleeptime_agent_id, actor_id).run(
                name, arguments
            )

        return await access.write(work)

    result: LoopResult = await run_memory_agent(
        chat, system_prompt, task, SLEEPTIME_TOOL_SCHEMAS, run_tool, settings.max_steps
    )
    after = await access.read(
        lambda store: _block_values(store, organization_id, sleeptime_agent_id)
    )
    changed = {
        label: {"before": before.get(label), "after": value}
        for label, value in after.items()
        if before.get(label) != value
    }
    return {
        "sleeptime_agent_id": sleeptime_agent_id,
        "tools_offered": list(SLEEPTIME_TOOL_NAMES),
        "steps": result.steps,
        "stop_reason": result.stop_reason,
        "tool_calls": result.tool_calls,
        "models_reported": sorted(set(result.models)),
        "changed_blocks": changed,
        "note": note,
        "messages_reviewed": len(prior) + len(response_messages),
    }


async def run_sleeptime_agents(
    access: StoreAccess,
    organization_id: str,
    group_id: str,
    message_ids: Optional[list[str]],
    chat: Chat,
    settings: SleeptimeSettings,
    actor_id: Optional[str] = None,
) -> dict[str, Any]:
    """Run a group's sleep-time agents after the foreground agent's turn, when the counter says so.

    Translated from run_sleeptime_agents in sleeptime_multi_agent_v4.py: with a frequency the
    turn counter is bumped (modulo the frequency) and the agents run only when it reaches zero;
    with no frequency they run every turn. message_ids names the new foreground messages; with
    none given, every message after the group's last processed one is used. No new messages
    means nothing runs. The newest message id is saved as last processed and each agent's
    record is returned in group order.
    """
    await access.write(ensure_state_tables)

    def decide(
        store: LettaMemoryStore,
    ) -> tuple[GroupState, Optional[int], list[Message], Optional[str]]:
        """Bump the counter, pick the new messages and, when due, save the newest as processed."""
        group = get_group(store, organization_id, group_id)
        frequency = group.sleeptime_agent_frequency
        counter = bump_turns_counter(store, organization_id, group_id) if frequency else None
        due = frequency is None or (counter is not None and counter % frequency == 0)
        if not due:
            return group, counter, [], None
        everything = store.list_messages(organization_id, group.foreground_agent_id)
        if message_ids is not None:
            chosen = [m for m in everything if m.id in set(message_ids)]
        elif group.last_processed_message_id is None:
            chosen = everything
        else:
            ids = [m.id for m in everything]
            chosen = everything[ids.index(group.last_processed_message_id) + 1 :]
        if not chosen:
            return group, counter, [], None
        previous = swap_last_processed_message_id(store, organization_id, group_id, chosen[-1].id)
        return group, counter, chosen, previous or ""

    group, counter, chosen, previous = await access.write(decide)
    frequency = group.sleeptime_agent_frequency
    if frequency and not (counter is not None and counter % frequency == 0):
        return {
            "ran": False,
            "reason": "turn counter has not reached the frequency",
            "turns_counter": counter,
            "agents": [],
        }
    if not chosen:
        return {
            "ran": False,
            "reason": "no response messages were generated",
            "turns_counter": counter,
            "agents": [],
        }
    agents = [
        await _participant_agent_step(
            access,
            organization_id,
            group.foreground_agent_id,
            agent_id,
            chosen,
            previous or None,
            frequency,
            chat,
            settings,
            actor_id,
        )
        for agent_id in group.agent_ids
    ]
    return {"ran": True, "reason": None, "turns_counter": counter, "agents": agents}
