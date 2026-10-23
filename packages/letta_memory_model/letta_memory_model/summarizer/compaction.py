"""Compaction of a conversation: pick the mode, fall back, count, and build the summary message.

It is for the top of Letta's compaction, translated from compact_messages in
letta/services/summarizer/compact.py, get_compaction_trigger_threshold in
thresholds.py and package_summarize_message_no_counts in letta/system.py. What
goes in is the in-context messages, a chat function, the compaction settings and
the tool definitions; what comes out is a CompactResult with the summary message
and the messages that stay. It never stores anything and never picks a model (the
original's per-provider summarizer model choice is left out; the one model given
is used).
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from letta_memory_model.chat import Chat, ChatError, ContextWindowExceededError
from letta_memory_model.messages import SUMMARY_ROLE, new_message
from letta_memory_model.schemas.message import Message
from letta_memory_model.summarizer.settings import CompactionSettings, get_default_prompt_for_mode
from letta_memory_model.summarizer.summarize import (
    self_summarize_all,
    self_summarize_sliding_window,
    summarize_all,
    summarize_via_sliding_window,
)
from letta_memory_model.summarizer.tokens import count_tokens, count_tokens_with_tools

logger = logging.getLogger(__name__)
# The failures that make Letta fall back to a simpler mode: the model call failed or the
# sliding window found no place to cut. Any other error is a bug and is raised, not hidden.
_FALLBACK_ERRORS = (ChatError, ValueError)


class SystemPromptTokenExceededError(RuntimeError):
    """The system message alone is longer than the model's whole context window.

    Translated from letta.errors.SystemPromptTokenExceededError, which compact.py
    raises when compaction cannot get below the threshold because of the system
    prompt. It carries the estimate and the window in its message. It is raised
    nowhere else.
    """


@dataclass(frozen=True)
class CompactResult:
    """What compaction produced: the summary message, the messages now in context and the summary.

    Translated from CompactResult in compact.py, plus mode_used and fallback_notes so a fallback
    is never silent. context_token_estimate is the approximate count of the compacted messages
    plus tools. It never writes anything.
    """

    summary_message: Message
    compacted_messages: list[Message]
    summary_text: str
    context_token_estimate: Optional[int]
    mode_used: str
    fallback_notes: tuple[str, ...]


def get_compaction_trigger_threshold(settings: CompactionSettings) -> int:
    """Return the token count at which compaction should start.

    Translated from get_compaction_trigger_threshold in thresholds.py: the context
    window times the trigger multiplier, as a whole number. Letta's GPT-5 special
    case is left out because it is keyed to model names this project does not use.
    It never reads anything but the settings.
    """
    return int(settings.context_window_tokens * settings.trigger_multiplier)


def package_summary_note(
    summary: str, compaction_stats: Optional[dict[str, Any]], mode: Optional[str], timezone: str
) -> str:
    """Wrap a summary in the system-alert JSON Letta shows the model.

    Translated from package_summarize_message_no_counts in letta/system.py: the
    wording depends on whether the mode is a sliding window and whether the counts
    are known, and the JSON carries the local time and the compaction stats. The
    time zone comes from the settings. It never changes the summary.
    """
    if mode and "sliding_window" in mode:
        if (
            compaction_stats
            and "messages_count_before" in compaction_stats
            and "messages_count_after" in compaction_stats
        ):
            evicted = (
                compaction_stats["messages_count_before"] - compaction_stats["messages_count_after"]
            )
            context_message = (
                f"Note: {evicted} messages from the beginning of the conversation have been "
                "hidden from view due to memory constraints.\n"
                f"The following is a summary of the previous messages:\n {summary}"
            )
        else:
            context_message = (
                "Note: prior messages from the beginning of the conversation have been hidden "
                "from view due to conversation memory constraints.\n"
                f"The following is a summary of the previous messages:\n {summary}"
            )
    else:
        context_message = (
            "Note: prior messages have been hidden from view due to conversation memory "
            f"constraints.\nThe following is a summary of the previous messages:\n {summary}"
        )
    local_time = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %I:%M:%S %p %Z%z").strip()
    packaged: dict[str, Any] = {
        "type": "system_alert",
        "message": context_message,
        "time": local_time,
    }
    if compaction_stats:
        packaged["compaction_stats"] = compaction_stats
    return json.dumps(packaged, ensure_ascii=False)


async def _fallback_to_all(
    messages: Sequence[Message], chat: Chat, settings: CompactionSettings
) -> tuple[str, list[Message]]:
    """Summarize everything with the all-mode prompt after another mode failed.

    Translated from the repeated fallback blocks in compact_messages: the mode and
    prompt of a copy of the settings are replaced by all and its prompt. The caller's
    settings are not changed.
    """
    fallback = settings.model_copy(update={"mode": "all", "prompt": None})
    fallback = fallback.model_copy(update={"prompt": get_default_prompt_for_mode("all", fallback)})
    return await summarize_all(messages, chat, fallback)


async def compact_messages(
    agent_id: str,
    messages: Sequence[Message],
    chat: Chat,
    settings: CompactionSettings,
    tools: Optional[Sequence[dict[str, Any]]] = None,
    trigger_threshold: Optional[int] = None,
    trigger: Optional[str] = None,
    context_tokens_before: Optional[int] = None,
    messages_count_before: Optional[int] = None,
) -> CompactResult:
    """Compact in-context messages by summarizing them in the chosen mode.

    Translated from compact_messages in compact.py. The self modes fall back to the
    self sliding window and then to all mode, sliding_window falls back to all mode
    (a context-window error is never retried), and if the result is still over
    trigger_threshold a sliding-window result is summarized again in all mode and a
    system message larger than the window raises SystemPromptTokenExceededError. The
    summary becomes a message with role summary, placed right after the system message.
    """
    working = settings.model_copy()
    if working.prompt is None:
        working = working.model_copy(
            update={"prompt": get_default_prompt_for_mode(working.mode, working)}
        )
    mode_used = working.mode
    notes: list[str] = []
    if working.mode == "self_compact_all":
        try:
            summary, compacted = await self_summarize_all(messages, chat, working, tools, agent_id)
        except _FALLBACK_ERRORS as error:
            notes.append(f"self_compact_all failed: {error}")
            logger.warning(notes[-1])
            try:
                fallback = working.model_copy(update={"mode": "self_compact_sliding_window"})
                fallback = fallback.model_copy(
                    update={"prompt": get_default_prompt_for_mode(fallback.mode, fallback)}
                )
                summary, compacted = await self_summarize_sliding_window(
                    messages, chat, fallback, tools, agent_id
                )
                mode_used = "self_compact_sliding_window"
            except _FALLBACK_ERRORS as error:
                notes.append(f"self_compact_sliding_window failed: {error}")
                logger.warning(notes[-1])
                summary, compacted = await _fallback_to_all(messages, chat, working)
                mode_used = "all"
    elif working.mode == "self_compact_sliding_window":
        try:
            summary, compacted = await self_summarize_sliding_window(
                messages, chat, working, tools, agent_id
            )
        except ContextWindowExceededError:
            raise
        except _FALLBACK_ERRORS as error:
            notes.append(f"self_compact_sliding_window failed: {error}")
            logger.warning(notes[-1])
            summary, compacted = await _fallback_to_all(messages, chat, working)
            mode_used = "all"
    elif working.mode == "all":
        summary, compacted = await summarize_all(messages, chat, working)
    elif working.mode == "sliding_window":
        try:
            summary, compacted = await summarize_via_sliding_window(messages, chat, working)
        except ContextWindowExceededError:
            raise
        except _FALLBACK_ERRORS as error:
            notes.append(f"sliding_window failed: {error}")
            logger.warning(notes[-1])
            summary, compacted = await _fallback_to_all(messages, chat, working)
            mode_used = "all"
    else:
        raise ValueError(f"Invalid summarizer mode: {working.mode}")

    estimate = count_tokens_with_tools(compacted, tools or [], working)
    if trigger_threshold is not None and estimate >= trigger_threshold:
        if mode_used == "sliding_window":
            notes.append(
                "still over the threshold after a sliding window; summarized again in all mode"
            )
            logger.warning(notes[-1])
            summary, compacted = await summarize_all(compacted, chat, working)
            mode_used = "all"
        estimate = count_tokens_with_tools(compacted, tools or [], working)
        if estimate >= trigger_threshold:
            system_estimate = count_tokens([compacted[0]], working)
            if system_estimate >= working.context_window_tokens:
                raise SystemPromptTokenExceededError(
                    f"System prompt ({system_estimate} tokens) exceeds context window "
                    f"({working.context_window_tokens})"
                )

    stats = None
    if trigger and context_tokens_before is not None and messages_count_before is not None:
        stats = {
            "trigger": trigger,
            "context_tokens_before": context_tokens_before,
            "context_tokens_after": estimate,
            "context_window": working.context_window_tokens,
            "messages_count_before": messages_count_before,
            "messages_count_after": len(compacted) + 1,
        }
    note = package_summary_note(summary, stats, mode_used, working.timezone)
    summary_message = new_message(SUMMARY_ROLE, note, agent_id)
    final = [compacted[0], summary_message, *compacted[1:]]
    return CompactResult(
        summary_message=summary_message,
        compacted_messages=final,
        summary_text=summary,
        context_token_estimate=estimate,
        mode_used=mode_used,
        fallback_notes=tuple(notes),
    )
