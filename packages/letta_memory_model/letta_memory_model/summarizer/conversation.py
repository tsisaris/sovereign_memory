"""Compacting one stored conversation: read it, summarize it, and save what stays in context.

It is for the part of Letta's agent loop that decides which messages are in
context and stores the summary message the agent writes after compaction
(letta/agents/letta_agent_v3.py calling compact_messages and updating the agent's
message_ids). What goes in is a StoreAccess, the agent and conversation ids,
a chat function and the compaction settings; what comes out is a plain dict of
what was summarized and what stays. It reads, then calls the model, then writes, and never
holds the store across the model call; it never deletes a message, so evicted messages
stay in recall and remain searchable.
"""

from collections.abc import Sequence
from typing import Any, Optional

from letta_memory_model.chat import Chat
from letta_memory_model.letta_duckdb_store import LettaMemoryStore
from letta_memory_model.messages import new_message
from letta_memory_model.model_state import (
    ContextState,
    ensure_state_tables,
    get_context_state,
    set_context_state,
)
from letta_memory_model.schemas.message import Message
from letta_memory_model.store_access import StoreAccess
from letta_memory_model.summarizer.compaction import (
    compact_messages,
    get_compaction_trigger_threshold,
)
from letta_memory_model.summarizer.settings import CompactionSettings
from letta_memory_model.summarizer.tokens import count_tokens_with_tools


async def compact_conversation(
    access: StoreAccess,
    organization_id: str,
    agent_id: str,
    conversation_id: str,
    chat: Chat,
    settings: CompactionSettings,
    tools: Optional[Sequence[dict[str, Any]]] = None,
    force: bool = True,
) -> dict[str, Any]:
    """Compact a conversation and return a plain dict of what was summarized and what stays.

    The messages in context are the saved ones, or every message of the conversation the first
    time; the system message is the agent's compiled core memory. With force False nothing
    happens while the context is below the trigger threshold. The summary is stored as a new
    message with role summary and the context state is replaced; it raises ValueError for a
    conversation with no messages and never deletes a message.
    """
    await access.write(ensure_state_tables)

    def load(store: LettaMemoryStore) -> tuple[str, list[Message]]:
        """Read the conversation's messages and the context that was saved for it."""
        stored = store.list_messages(organization_id, agent_id, conversation_id=conversation_id)
        by_id = {message.id: message for message in stored}
        state = get_context_state(store, organization_id, conversation_id)
        if state is None:
            return store.get_agent_memory(organization_id, agent_id).compile(), list(stored)
        return state.system_text, [by_id[i] for i in state.message_ids if i in by_id]

    system_text, in_context = await access.read(load)
    if len(in_context) < 1:
        raise ValueError("The conversation has no messages to compact")
    full = [new_message("system", system_text, agent_id), *in_context]
    tokens_before = count_tokens_with_tools(full, tools or [], settings)
    threshold = get_compaction_trigger_threshold(settings)
    if not force and tokens_before < threshold:
        return {
            "compacted": False,
            "reason": f"context is {tokens_before} tokens, below the trigger threshold {threshold}",
            "tokens_before": tokens_before,
            "messages_in_context": len(full),
        }
    result = await compact_messages(
        agent_id,
        full,
        chat,
        settings,
        tools=tools,
        trigger_threshold=threshold,
        trigger="requested",
        context_tokens_before=tokens_before,
        messages_count_before=len(full),
    )
    summary = result.summary_message.model_copy(update={"conversation_id": conversation_id})
    kept = result.compacted_messages[2:]

    def save(store: LettaMemoryStore) -> str:
        """Store the summary message and replace the saved context in one transaction."""
        stored_summary = store.add_messages(organization_id, [summary])[0]
        ids = (stored_summary.id, *(message.id for message in kept))
        set_context_state(
            store, organization_id, conversation_id, agent_id, ContextState(system_text, ids)
        )
        return stored_summary.id

    summary_id = await access.write(save)
    new_ids = {summary_id, *(message.id for message in kept)}
    return {
        "compacted": True,
        "mode_used": result.mode_used,
        "fallback_notes": list(result.fallback_notes),
        "summary_text": result.summary_text,
        "summary_message_id": summary_id,
        "messages_before": len(full),
        "messages_after": len(new_ids) + 1,
        "evicted_message_ids": [m.id for m in in_context if m.id not in new_ids],
        "tokens_before": tokens_before,
        "tokens_after": result.context_token_estimate,
    }
