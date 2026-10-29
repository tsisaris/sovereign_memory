"""The small tables that remember where compaction and sleep-time work stand.

They are for the state Letta kept on its agent and group rows and this store does
not have: which messages are in an agent's context (the agent's message_ids), a
sleep-time group's turn counter and last processed message (letta/orm/group.py,
letta/services/group_manager.py), and how far dreaming has read. What goes in is
a LettaMemoryStore and plain values; what comes out is plain values. They never
touch Letta's own tables and never run a model.
"""

import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Column, Integer, MetaData, String, Table, delete, insert, select, update

from letta_memory_model.letta_duckdb_store import LettaMemoryStore
from letta_memory_model.orm.base import LETTA_SCHEMA

_METADATA = MetaData(schema=LETTA_SCHEMA)
CONTEXT = Table(
    "letta_conversation_context",
    _METADATA,
    Column("organization_id", String, primary_key=True),
    Column("conversation_id", String, primary_key=True),
    Column("agent_id", String, nullable=False),
    Column("system_text", String, nullable=False),
    Column("message_ids", String, nullable=False),
)
GROUPS = Table(
    "letta_sleeptime_group",
    _METADATA,
    Column("organization_id", String, primary_key=True),
    Column("group_id", String, primary_key=True),
    Column("foreground_agent_id", String, nullable=False),
    Column("agent_ids", String, nullable=False),
    Column("sleeptime_agent_frequency", Integer, nullable=True),
    Column("turns_counter", Integer, nullable=False),
    Column("last_processed_message_id", String, nullable=True),
)
DREAMS = Table(
    "letta_dream_state",
    _METADATA,
    Column("organization_id", String, primary_key=True),
    Column("agent_id", String, primary_key=True),
    Column("last_reflected_sequence_id", Integer, nullable=False),
)


@dataclass(frozen=True)
class ContextState:
    """Which messages are in a conversation's context: the system text and the message ids.

    Translated from the agent row's message_ids in letta/orm/agent.py: the first
    entry of Letta's list is the system message, which this store does not keep as a
    message, so its text is held here. It never touches the messages themselves.
    """

    system_text: str
    message_ids: tuple[str, ...]


@dataclass(frozen=True)
class GroupState:
    """A sleep-time group: its foreground agent, sleep-time agents, frequency and counters.

    Translated from the fields of letta/orm/group.py that SleeptimeMultiAgentV4
    reads: agent_ids, sleeptime_agent_frequency, turns_counter and
    last_processed_message_id. A frequency of None means every turn. It never runs an agent.
    """

    group_id: str
    foreground_agent_id: str
    agent_ids: tuple[str, ...]
    sleeptime_agent_frequency: Optional[int]
    turns_counter: int
    last_processed_message_id: Optional[str]


def ensure_state_tables(store: LettaMemoryStore) -> None:
    """Create the three state tables when they are missing; leave existing ones as they are.

    Goes in: a store whose Letta schema exists; comes out: nothing. It runs inside
    the store's own transaction and never drops or alters a table.
    """
    with store.transaction() as session:
        _METADATA.create_all(session.connection(), checkfirst=True)


def get_context_state(
    store: LettaMemoryStore, organization_id: str, conversation_id: str
) -> Optional[ContextState]:
    """Return the saved context of a conversation, or None if none was saved yet.

    It only reads.
    """
    with store.transaction() as session:
        row = session.execute(
            select(CONTEXT.c.system_text, CONTEXT.c.message_ids).where(
                CONTEXT.c.organization_id == organization_id,
                CONTEXT.c.conversation_id == conversation_id,
            )
        ).one_or_none()
    if row is None:
        return None
    return ContextState(system_text=row[0], message_ids=tuple(json.loads(row[1])))


def set_context_state(
    store: LettaMemoryStore,
    organization_id: str,
    conversation_id: str,
    agent_id: str,
    state: ContextState,
) -> None:
    """Replace the saved context of a conversation with the given state, in one transaction.

    It writes only the one row and never touches messages.
    """
    with store.transaction() as session:
        session.execute(
            delete(CONTEXT).where(
                CONTEXT.c.organization_id == organization_id,
                CONTEXT.c.conversation_id == conversation_id,
            )
        )
        session.execute(
            insert(CONTEXT).values(
                organization_id=organization_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                system_text=state.system_text,
                message_ids=json.dumps(list(state.message_ids)),
            )
        )


def create_group(
    store: LettaMemoryStore,
    organization_id: str,
    group_id: str,
    foreground_agent_id: str,
    agent_ids: list[str],
    sleeptime_agent_frequency: Optional[int],
) -> None:
    """Record a sleep-time group with its counters at zero and no processed message.

    A group id that already exists is refused with a ValueError. It never starts an agent.
    """
    with store.transaction() as session:
        exists = session.execute(
            select(GROUPS.c.group_id).where(
                GROUPS.c.organization_id == organization_id, GROUPS.c.group_id == group_id
            )
        ).one_or_none()
        if exists is not None:
            raise ValueError(f"Group {group_id} already exists")
        session.execute(
            insert(GROUPS).values(
                organization_id=organization_id,
                group_id=group_id,
                foreground_agent_id=foreground_agent_id,
                agent_ids=json.dumps(agent_ids),
                sleeptime_agent_frequency=sleeptime_agent_frequency,
                turns_counter=0,
                last_processed_message_id=None,
            )
        )


def get_group(store: LettaMemoryStore, organization_id: str, group_id: str) -> GroupState:
    """Return a sleep-time group, or raise LookupError when there is none.

    It only reads.
    """
    with store.transaction() as session:
        row = session.execute(
            select(
                GROUPS.c.foreground_agent_id,
                GROUPS.c.agent_ids,
                GROUPS.c.sleeptime_agent_frequency,
                GROUPS.c.turns_counter,
                GROUPS.c.last_processed_message_id,
            ).where(GROUPS.c.organization_id == organization_id, GROUPS.c.group_id == group_id)
        ).one_or_none()
    if row is None:
        raise LookupError(f"Group {group_id} not found")
    return GroupState(
        group_id=group_id,
        foreground_agent_id=row[0],
        agent_ids=tuple(json.loads(row[1])),
        sleeptime_agent_frequency=row[2],
        turns_counter=row[3],
        last_processed_message_id=row[4],
    )


def bump_turns_counter(store: LettaMemoryStore, organization_id: str, group_id: str) -> int:
    """Add one turn to a group's counter, wrapping at its frequency, and return the new value.

    Translated from GroupManager.bump_turns_counter_async: the counter becomes
    (counter + 1) modulo the frequency. A group with no frequency is refused with a
    ValueError because the original only calls this when a frequency is set.
    """
    group = get_group(store, organization_id, group_id)
    if not group.sleeptime_agent_frequency:
        raise ValueError(f"Group {group_id} has no sleeptime_agent_frequency")
    counter = (group.turns_counter + 1) % group.sleeptime_agent_frequency
    with store.transaction() as session:
        session.execute(
            update(GROUPS)
            .where(GROUPS.c.organization_id == organization_id, GROUPS.c.group_id == group_id)
            .values(turns_counter=counter)
        )
    return counter


def swap_last_processed_message_id(
    store: LettaMemoryStore, organization_id: str, group_id: str, message_id: str
) -> Optional[str]:
    """Save the newest processed message id of a group and return the previous one (or None).

    Translated from GroupManager.get_last_processed_message_id_and_update_async.
    It changes only that one column.
    """
    previous = get_group(store, organization_id, group_id).last_processed_message_id
    with store.transaction() as session:
        session.execute(
            update(GROUPS)
            .where(GROUPS.c.organization_id == organization_id, GROUPS.c.group_id == group_id)
            .values(last_processed_message_id=message_id)
        )
    return previous


def get_dream_sequence(store: LettaMemoryStore, organization_id: str, agent_id: str) -> int:
    """Return the sequence id of the last message dreaming has read for an agent, or 0 if none.

    It only reads.
    """
    with store.transaction() as session:
        value = session.execute(
            select(DREAMS.c.last_reflected_sequence_id).where(
                DREAMS.c.organization_id == organization_id, DREAMS.c.agent_id == agent_id
            )
        ).scalar_one_or_none()
    return 0 if value is None else value


def set_dream_sequence(
    store: LettaMemoryStore, organization_id: str, agent_id: str, sequence_id: int
) -> None:
    """Record how far dreaming has read for an agent, replacing the earlier value.

    It writes only that one row.
    """
    with store.transaction() as session:
        session.execute(
            delete(DREAMS).where(
                DREAMS.c.organization_id == organization_id, DREAMS.c.agent_id == agent_id
            )
        )
        session.execute(
            insert(DREAMS).values(
                organization_id=organization_id,
                agent_id=agent_id,
                last_reflected_sequence_id=sequence_id,
            )
        )
