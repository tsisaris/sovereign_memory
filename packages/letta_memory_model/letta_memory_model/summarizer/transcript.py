"""Turning messages into the transcript text a summarizer reads, and cutting it to fit.

It is for the transcript steps of Letta's summarizer, translated from
letta/services/summarizer/summarizer.py (simple_formatter and
middle_truncate_text). What goes in is stored messages or a long text and a
byte budget; what comes out is a compact plain-text transcript or a text with
its middle removed. It never calls a model and never changes the messages.
"""

from collections.abc import Sequence
from typing import Optional

from letta_memory_model.messages import to_openai_dicts
from letta_memory_model.schemas.message import Message


def simple_formatter(
    messages: Sequence[Message],
    include_system: bool = False,
    tool_return_truncation_chars: Optional[int] = None,
) -> str:
    """Turn messages into one start/end-marked plain-text transcript.

    Translated from simple_formatter in summarizer.py: system messages are left
    out unless asked for, tool replies are clamped to the given length, each line
    is "[role] content" with tool calls shown as "-> name(arguments)", and the
    text ends with the original's ". Generate the summary." cue. A message with an
    unexpected shape is written as its JSON; nothing is dropped silently.
    """
    parsed = to_openai_dicts(
        [m for m in messages if m.role != "system" or include_system],
        tool_return_truncation_chars=tool_return_truncation_chars,
    )
    lines = []
    for msg in parsed:
        role = msg.get("role", "?")
        content = msg.get("content")
        if isinstance(content, list):
            content = " ".join(
                part.get("text", str(part)) if isinstance(part, dict) else str(part)
                for part in content
            )
        content = content or ""
        tool_calls = msg.get("tool_calls")
        if tool_calls and isinstance(tool_calls, list):
            call_parts = []
            for call in tool_calls:
                function = (call.get("function") or {}) if isinstance(call, dict) else {}
                call_parts.append(f"{function.get('name', '?')}({function.get('arguments', '')})")
            content = (content + " " if content else "") + "-> " + ", ".join(call_parts)
        if content:
            lines.append(f"[{role}] {content}")
    return "<start_transcript>\n" + "\n".join(lines) + "\n<end_transcript>\n. Generate the summary."


def middle_truncate_text(
    text: str, budget_bytes: int, head_frac: float, tail_frac: float
) -> tuple[str, int]:
    """Cut the middle out of a text so it fits a byte budget; return the text and chars dropped.

    Translated from middle_truncate_text in summarizer.py. The byte budget is turned
    into a character budget with the text's own bytes-per-character ratio, the first
    head_frac and last tail_frac of it are kept, and a marker says how many middle
    characters were dropped. The two fractions are arguments here (the original gave
    them defaults of 0.3); a text already within budget comes back unchanged.
    """
    if budget_bytes <= 0:
        return text, 0
    text_bytes = len(text.encode("utf-8"))
    if text_bytes <= budget_bytes:
        return text, 0
    bytes_per_char = text_bytes / len(text) if text else 1.0
    budget_chars = max(1, int(budget_bytes / bytes_per_char))
    head_len = max(0, int(budget_chars * head_frac))
    tail_len = max(0, int(budget_chars * tail_frac))
    if head_len + tail_len > budget_chars:
        tail_len = max(0, budget_chars - head_len)
    head = text[:head_len]
    tail = text[-tail_len:] if tail_len > 0 else ""
    dropped = max(0, len(text) - (len(head) + len(tail)))
    marker = f"\n[TRUNCATED: dropped {dropped} middle chars due to context budget]\n"
    available_for_marker = budget_chars - (len(head) + len(tail))
    if available_for_marker < len(marker):
        over = len(marker) - available_for_marker
        tail = tail[:-over] if over < len(tail) else ""
    return head + marker + tail, dropped
