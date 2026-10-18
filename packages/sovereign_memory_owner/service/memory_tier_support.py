"""Small pure helpers the owner-side tiers (Letta, Graphiti, Cognee) share.

They turn a captured event into its text and name the per-actor group a tier
stores it under. Nothing here touches a database, a model or the network.
"""

import hashlib
import json
from typing import Any

from service.memory_owner_core import REMOVAL_EVENT_TYPES
from service.memory_processor_seam import TerminalProcessingError

# How a version 2 payload was read: a list of role messages, other JSON, or plain text.
KIND_MESSAGES = "messages"
KIND_JSON = "json"
KIND_TEXT = "text"


def group_of(actor: str) -> str:
    """Return the group id holding one actor's rows; pure.

    Graphiti accepts only letters, digits, dashes and underscores in a group id,
    so the actor is hashed rather than copied; the actor itself is kept in each
    row's own provenance.
    """
    return "actor-" + hashlib.sha256(actor.encode("utf-8")).hexdigest()


def actor_of(envelope: dict[str, Any]) -> str:
    """Return the producing actor of an event (either schema), or raise TerminalProcessingError."""
    actor = envelope.get("actor_agent_id", envelope.get("agent_id"))
    if not isinstance(actor, str) or not actor:
        raise TerminalProcessingError("Event has no producing actor")
    return actor


def is_removal(envelope: dict[str, Any]) -> bool:
    """Say whether an event is an owner-authored removal ("system.expire" or "system.forget")."""
    return envelope.get("event_type") in REMOVAL_EVENT_TYPES.values()


def event_text(
    envelope: dict[str, Any], payload: bytes | None
) -> tuple[str, str, list[dict[str, str]]]:
    """Return (text, kind, messages) of a captured, non-removal event.

    A legacy 1.0.0 event carries its text in "content" (kind "text"). For a
    version 2 event the verified payload bytes are read: a JSON object carrying
    a non-empty list of {"role", "content"} messages has kind "messages", its
    text is "role: content" lines and messages is that list; other JSON has
    kind "json" and other UTF-8 kind "text" (messages is empty for both).
    Raises TerminalProcessingError for missing or non-UTF-8 payload bytes.
    """
    if envelope.get("schema_version") == "1.0.0":
        return envelope["content"], KIND_TEXT, []
    if payload is None:
        raise TerminalProcessingError("Event has no stored payload bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TerminalProcessingError(f"Payload is not UTF-8 text: {error}") from error
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return text, KIND_TEXT, []
    messages = body.get("messages") if isinstance(body, dict) else None
    if (
        isinstance(messages, list)
        and messages
        and all(
            isinstance(m, dict)
            and isinstance(m.get("role"), str)
            and isinstance(m.get("content"), str)
            for m in messages
        )
    ):
        lines = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
        return (
            lines,
            KIND_MESSAGES,
            [{"role": m["role"], "content": m["content"]} for m in messages],
        )
    return text, KIND_JSON, []


def event_instant(envelope: dict[str, Any]) -> str:
    """Return the ISO 8601 text of when the event happened, whichever schema it uses."""
    if envelope.get("schema_version") == "1.0.0":
        return envelope["timestamp"]
    return envelope["created_at"]


def removal_target(envelope: dict[str, Any], payload: bytes | None) -> tuple[str, str]:
    """Return (target_event_id, target_actor) an owner-authored removal names.

    Raises TerminalProcessingError when the payload is not JSON, or does not
    agree with the removal's own causation event and event type.
    """
    try:
        body = json.loads((payload or b"").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TerminalProcessingError(f"Removal payload is not JSON: {error}") from error
    if not isinstance(body, dict):
        raise TerminalProcessingError("Removal payload does not match its causation event")
    target = body.get("target_event_id")
    target_actor = body.get("target_actor_agent_id")
    expected = REMOVAL_EVENT_TYPES.get(body.get("operation"))
    if (
        not isinstance(target, str)
        or not isinstance(target_actor, str)
        or not target_actor
        or target != envelope.get("causation_event_id")
        or expected != envelope.get("event_type")
    ):
        raise TerminalProcessingError("Removal payload does not match its causation event")
    return target, target_actor
