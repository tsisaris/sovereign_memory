"""The four ways Letta writes a summary, translated from the original summarizer files.

It is for compaction's model work: simple_summary (summarizer.py), summarize_all
(summarizer_all.py), summarize_via_sliding_window (summarizer_sliding_window.py)
and self_summarize_all and self_summarize_sliding_window (self_summarizer.py).
What goes in is stored messages, the compaction settings and a chat function;
what comes out is the summary text and the messages that stay in context. It
never stores anything, picks a model, or falls back to a different provider
(the original's Anthropic and Bedrock overload fallback is left out).
"""

from collections.abc import Sequence
from typing import Any, Optional

from letta_memory_model.chat import Chat, ChatError, ContextWindowExceededError
from letta_memory_model.messages import new_message, to_openai_dicts
from letta_memory_model.schemas.message import Message
from letta_memory_model.summarizer.settings import CompactionSettings, get_default_prompt_for_mode
from letta_memory_model.summarizer.tokens import count_tokens
from letta_memory_model.summarizer.transcript import middle_truncate_text, simple_formatter

SUMMARY_TRUNCATION_SUFFIX = "... [summary truncated to fit]"
MESSAGE_SUMMARY_REQUEST_ACK = (
    "Understood, I will respond with a summary of the message (and only the summary, nothing "
    "else) once I receive the conversation history. I'm ready."
)


def _input_messages(system_prompt: str, transcript: str, include_ack: bool) -> list[dict[str, str]]:
    """Build the three- or two-message request simple_summary sends to the model.

    Translated from the input_messages lists in simple_summary (summarizer.py): the
    system prompt, then (with include_ack) the assistant acknowledgement, then the
    transcript as a user message. It never calls a model.
    """
    if include_ack:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "assistant", "content": MESSAGE_SUMMARY_REQUEST_ACK},
            {"role": "user", "content": transcript},
        ]
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": transcript},
    ]


async def _run_summarizer_request(
    chat: Chat, request: Sequence[dict[str, Any]], tools: Optional[Sequence[dict[str, Any]]]
) -> str:
    """Send one summary request and return the reply text, stripped.

    Translated from _run_summarizer_request and _execute_summarizer_request in
    summarizer.py (the non-streaming path). An empty reply raises ChatError
    "Summary failed to generate", as the original does. It never retries.
    """
    reply = await chat(request, tools)
    if reply.text is None or not reply.text.strip():
        raise ChatError("Summary failed to generate")
    return reply.text.strip()


async def simple_summary(
    messages: Sequence[Message],
    chat: Chat,
    settings: CompactionSettings,
    include_ack: bool,
    prompt: str | None,
) -> str:
    """Write one summary of messages, retrying smaller if the model says the request was too long.

    Translated from simple_summary in summarizer.py. It sends the prompt and the
    transcript; if the model reports its context window exceeded it sends the
    transcript again with tool replies clamped (fallback A), and if that fails again
    it sends the transcript with its middle cut to a byte budget (fallback B). The
    telemetry, provider client and inner-thought settings of the original are left
    out. A failure of the last attempt is raised.
    """
    system_prompt = prompt or get_default_prompt_for_mode("all", settings)
    transcript = simple_formatter(messages)
    request = _input_messages(system_prompt, transcript, include_ack)
    try:
        return await _run_summarizer_request(chat, request, None)
    except ContextWindowExceededError:
        transcript = simple_formatter(
            messages, tool_return_truncation_chars=settings.tool_return_truncation_chars
        )
        request = _input_messages(system_prompt, transcript, include_ack)
        try:
            return await _run_summarizer_request(chat, request, None)
        except ChatError:
            budget_bytes = int(
                settings.context_window_tokens
                * settings.fallback_budget_fraction
                * settings.fallback_bytes_per_token
            )
            overhead_bytes = (
                len(system_prompt.encode("utf-8"))
                + (len(MESSAGE_SUMMARY_REQUEST_ACK.encode("utf-8")) if include_ack else 0)
                + settings.fallback_overhead_bytes
            )
            budget_bytes = max(settings.fallback_min_budget_bytes, budget_bytes - overhead_bytes)
            truncated, _ = middle_truncate_text(
                transcript,
                budget_bytes=budget_bytes,
                head_frac=settings.truncate_head_fraction,
                tail_frac=settings.truncate_tail_fraction,
            )
            return await _run_summarizer_request(
                chat, _input_messages(system_prompt, truncated, include_ack), None
            )


def _clip(summary: str, settings: CompactionSettings) -> str:
    """Cut a summary to clip_chars and add the truncation note, or return it unchanged.

    Translated from the clip_chars blocks at the end of summarize_all and
    summarize_via_sliding_window. A setting of null means no clipping. It never
    shortens a summary that already fits.
    """
    if settings.clip_chars is not None and len(summary) > settings.clip_chars:
        return summary[: settings.clip_chars] + SUMMARY_TRUNCATION_SUFFIX
    return summary


def _protected_tail(messages: Sequence[Message]) -> tuple[list[Message], list[Message]]:
    """Split messages into the ones to summarize and the pending approval request to keep.

    Translated from _get_protected_messages in self_summarizer.py (and the same
    block at the top of summarize_all): a trailing approval request cannot be
    evicted, and the assistant message before it stays too when both came from one
    step. With no trailing approval, nothing is protected. It never changes the list.
    """
    if messages[-1].role == "approval":
        protected = [messages[-1]]
        if len(messages) >= 2:
            before, approval = messages[-2], messages[-1]
            if before.role == "assistant" and before.step_id == approval.step_id:
                return list(messages[:-2]), [before, approval]
        return list(messages[:-1]), protected
    return list(messages), []


async def summarize_all(
    in_context_messages: Sequence[Message], chat: Chat, settings: CompactionSettings
) -> tuple[str, list[Message]]:
    """Summarize the whole history after the system message into one summary.

    Translated from summarize_all in summarizer_all.py. The first message is the
    system message and stays; a trailing approval request (and its assistant
    message) stays too; everything between is summarized and the summary is clipped
    to clip_chars. It returns the summary and the messages that stay.
    """
    to_summarize, protected = _protected_tail(in_context_messages)
    summary = await simple_summary(
        to_summarize[1:],
        chat,
        settings,
        include_ack=settings.prompt_acknowledgement,
        prompt=settings.prompt,
    )
    return _clip(summary, settings), [in_context_messages[0], *protected]


def _is_valid_cutoff(message: Message) -> bool:
    """Say whether a sliding window may be cut just before this message.

    Translated from is_valid_cutoff in summarizer_sliding_window.py: an assistant
    message, or an approval request that carries tool calls. It never looks at
    anything but the one message.
    """
    if message.role == "assistant":
        return True
    if message.role == "approval":
        return bool(message.tool_calls)
    return False


def _sliding_cutoff(messages: Sequence[Message], settings: CompactionSettings) -> int:
    """Find the index of the first message that stays after a sliding-window summary.

    Translated from the eviction loop shared by summarize_via_sliding_window
    (summarizer_sliding_window.py) and self_summarize_sliding_window
    (self_summarizer.py): start at sliding_window_percentage of the messages, move
    the cutoff back to an assistant (or tool-calling approval) message, count what
    would remain with the system message, and raise the percentage by eviction_step
    until the remainder is below (1 - percentage) of the context window. Raises
    ValueError when no cutoff is found or the cutoff reaches the last message.
    """
    system_prompt = messages[0]
    total = len(messages)
    maximum_index = total - 2 if messages[-1].role == "approval" else total - 1
    eviction = settings.sliding_window_percentage
    goal_tokens = (1 - settings.sliding_window_percentage) * settings.context_window_tokens
    count = float(settings.context_window_tokens)
    cutoff: Optional[int] = None
    while count >= goal_tokens and eviction < 1.0:
        eviction += settings.eviction_step
        message_cutoff_index = round(eviction * total)
        cutoff = next(
            (
                i
                for i in reversed(range(1, message_cutoff_index + 1))
                if i < total and _is_valid_cutoff(messages[i])
            ),
            None,
        )
        if cutoff is None:
            continue
        count = count_tokens([system_prompt, *messages[cutoff:]], settings)
    if cutoff is None or eviction >= 1.0:
        raise ValueError("No assistant message found for sliding window summarization")
    if cutoff >= maximum_index:
        raise ValueError(
            f"Assistant message index {cutoff} is at the end of the message buffer, "
            "skipping summarization"
        )
    return cutoff


async def summarize_via_sliding_window(
    in_context_messages: Sequence[Message], chat: Chat, settings: CompactionSettings
) -> tuple[str, list[Message]]:
    """Summarize only the oldest part of the history and keep the recent part.

    Translated from summarize_via_sliding_window in summarizer_sliding_window.py.
    The cutoff comes from _sliding_cutoff; messages 1 up to the cutoff are summarized
    and clipped; the system message and everything from the cutoff on stay. It raises
    ValueError when no cutoff exists, which the caller turns into a fallback to all mode.
    """
    cutoff = _sliding_cutoff(in_context_messages, settings)
    summary = await simple_summary(
        in_context_messages[1:cutoff],
        chat,
        settings,
        include_ack=settings.prompt_acknowledgement,
        prompt=settings.prompt,
    )
    return _clip(summary, settings), [in_context_messages[0], *in_context_messages[cutoff:]]


async def self_summarize_all(
    messages: Sequence[Message],
    chat: Chat,
    settings: CompactionSettings,
    tools: Optional[Sequence[dict[str, Any]]],
    agent_id: str,
) -> tuple[str, list[Message]]:
    """Ask the agent's own model to summarize, with the summary request as a last user message.

    Translated from self_summarize_all in self_summarizer.py. The whole history
    (system message included) goes to the model followed by the mode's prompt; if the
    history does not end with an assistant message a short assistant line is added
    first so the model does not carry on the conversation. Fewer than two messages
    gives "No conversation to summarize." and nothing changes. The reply is clipped.
    """
    if len(messages) < 2:
        return "No conversation to summarize.", list(messages)
    system_message = messages[0]
    to_summarize, protected = _protected_tail(messages)
    prompt = settings.prompt or get_default_prompt_for_mode(settings.mode, settings)
    request_message = new_message("user", prompt, agent_id)
    if to_summarize[-1].role != "assistant":
        with_request = [
            *to_summarize,
            new_message("assistant", "I understand. Let me summarize.", agent_id),
            request_message,
        ]
    else:
        with_request = [*to_summarize, request_message]
    summary = await _run_summarizer_request(chat, to_openai_dicts(with_request), tools)
    return _clip(summary, settings), [system_message, *protected]


async def self_summarize_sliding_window(
    messages: Sequence[Message],
    chat: Chat,
    settings: CompactionSettings,
    tools: Optional[Sequence[dict[str, Any]]],
    agent_id: str,
) -> tuple[str, list[Message]]:
    """Self-summarize only the oldest part of the history and keep the recent part.

    Translated from self_summarize_sliding_window in self_summarizer.py. The cutoff
    comes from _sliding_cutoff; the messages before it (system message included) go
    through self_summarize_all; the system message and everything from the cutoff on
    stay. It raises ValueError when no cutoff exists. Fewer than two messages gives
    "No conversation to summarize." as in the original.
    """
    if len(messages) < 2:
        return "No conversation to summarize.", list(messages)
    cutoff = _sliding_cutoff(messages, settings)
    summary, kept = await self_summarize_all(messages[:cutoff], chat, settings, tools, agent_id)
    return summary, [*kept, *messages[cutoff:]]
