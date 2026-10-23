"""Helpers that turn stored Letta messages into the text and dicts a model is shown.

They are for the translated summarizer and sleep-time code, which in Letta use
methods on its Message class (to_openai_dicts_from_list, stringify_message,
truncate_tool_return). What goes in is Message objects from the recall store;
what comes out is plain text, OpenAI-style dicts, or new Message objects. They
never read or write storage and never call a model.
"""

import json
from collections.abc import Sequence
from typing import Any, Optional

from letta_memory_model.schemas.message import Message

SUMMARY_ROLE = "summary"
APPROVAL_ROLE = "approval"


def message_text(message: Message) -> str:
    """Return the text of a message: its text field, else its text content parts joined.

    Translated from the text part of letta.schemas.message.Message content
    handling. A message with neither gives an empty string. Images and other
    parts are skipped, never described.
    """
    if message.text:
        return message.text
    parts = message.content or []
    return "".join(
        str(part.get("text", "")) for part in parts if part.get("type", "text") == "text"
    )


def truncate_tool_return(content: Optional[str], limit: Optional[int]) -> Optional[str]:
    """Cut a tool's reply to limit characters and say how many were dropped.

    Translated from truncate_tool_return in letta/schemas/message.py. With no
    limit or no content the input comes back unchanged. It never removes anything
    from a reply that already fits.
    """
    if limit is None or content is None:
        return content
    if len(content) <= limit:
        return content
    return content[:limit] + f"... [truncated {len(content) - limit} chars]"


def to_openai_dicts(
    messages: Sequence[Message], tool_return_truncation_chars: Optional[int] = None
) -> list[dict[str, Any]]:
    """Turn stored messages into OpenAI-style role and content dicts.

    Translated from Message.to_openai_dicts_from_list in letta/schemas/message.py,
    reduced to the roles this store holds: system, user, assistant, tool, a summary
    (shown as a user message) and an approval request (shown as an assistant
    message with its tool calls). The original's human-approval filtering and
    provider-specific shapes are left out. It never changes the messages.
    """
    result: list[dict[str, Any]] = []
    for message in messages:
        text = message_text(message)
        if message.role == "tool":
            result.append(
                {
                    "role": "tool",
                    "content": truncate_tool_return(text, tool_return_truncation_chars),
                    "tool_call_id": message.tool_call_id or "",
                }
            )
        elif message.role in ("assistant", APPROVAL_ROLE) and message.tool_calls:
            result.append(
                {"role": "assistant", "content": text or None, "tool_calls": message.tool_calls}
            )
        elif message.role in ("system", "user", "assistant"):
            result.append({"role": message.role, "content": text})
        elif message.role in (SUMMARY_ROLE, APPROVAL_ROLE):
            result.append({"role": "user", "content": text})
        else:
            raise ValueError(f"Unknown message role: {message.role}")
    return result


def stringify_message(message: Message) -> str | None:
    """Return one stored message as the line the sleep-time agent reads, or None if it has none.

    Translated from stringify_message in letta/groups/helpers.py (the user,
    assistant and approval-request cases). A user message becomes "user: text"
    (or its name), an assistant message "assistant: text" and each tool call
    "assistant: Calling tool name", with send_message shown as its message.
    It never invents text for a message that has none.
    """
    if message.role == "user":
        text = message_text(message)
        return f"{message.name or 'user'}: {text}" if text else None
    if message.role in ("assistant", APPROVAL_ROLE):
        lines: list[str] = []
        text = message_text(message)
        if text:
            lines.append(f"assistant: {text}")
        for call in message.tool_calls or []:
            function = call.get("function", {})
            if function.get("name") == "send_message":
                lines.append(f"assistant: {json.loads(function['arguments'])['message']}")
            else:
                lines.append(f"assistant: Calling tool {function.get('name')}")
        return "\n".join(lines) if lines else None
    return None


def new_message(
    role: str, text: str, agent_id: str, conversation_id: Optional[str] = None
) -> Message:
    """Make a new stored-shape message with a fresh id, the given role and one text.

    Goes in: the role, the text, the owning agent and optionally the conversation;
    comes out: a Message not yet stored. It never stores it.
    """
    return Message(role=role, text=text, agent_id=agent_id, conversation_id=conversation_id)
