"""Zep's thread (session) summariser, translated from Zep v0.x's message summary task.

It is for turning a long conversation into a running summary: once the unsummarised messages reach
the message window, the older half is folded into the previous summary a batch at a time and the
last message folded in becomes the new summary point. It takes stored messages, the previous summary
and the settings, and calls a model only through a function the caller passes in. The original is
getzep/zep at commit 411142c^ (the parent of "Zep CE v1 (#361)", which removed it):
pkg/tasks/message_summarizer.go, pkg/tasks/prompts.go and pkg/tasks/message_utils.go. It never
touches a database or a model server itself and invents no number: every size comes from settings.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from graphiti_duckdb.zep_ce_store import Message

PREV_SUMMARY_IDENTIFIER = "{{.PrevSummary}}"
MESSAGES_JOINED_IDENTIFIER = "{{.MessagesJoined}}"

# pkg/tasks/prompts.go, defaultSummaryPromptTemplateOpenAI, word for word (the spaces at the ends
# of four of its lines are not kept). The service here speaks the OpenAI-style door, so this is
# the template Zep chose for that service.
DEFAULT_SUMMARY_PROMPT = """
Review the Current Content, if there is one, and the New Lines of the provided conversation. Create a concise summary
of the conversation, adding from the New Lines to the Current summary.
If the New Lines are meaningless, return the Current Content.

EXAMPLE
Current summary:
The human inquires about Led Zeppelin's lead singer and other band members. The AI identifies Robert Plant as the
lead singer.
New lines of conversation:
Human: Who were the other members of Led Zeppelin?
AI: The other founding members of Led Zeppelin were Jimmy Page (guitar), John Paul Jones (bass, keyboards), and
John Bonham (drums).
New summary:
The human inquires about Led Zeppelin's lead singer and other band members. The AI identifies Robert Plant as the lead
singer and lists the founding members as Jimmy Page, John Paul Jones, and John Bonham.
EXAMPLE END

Current summary:
{{.PrevSummary}}
New lines of conversation:
{{.MessagesJoined}}
New summary:
"""

Complete = Callable[[str, int], Awaitable[str]]
CountTokens = Callable[[str], int]


@dataclass(frozen=True)
class SummarizerSettings:
    """The numbers and the prompt the summariser runs on, all read from settings, none defaulted."""

    message_window: int
    summary_max_output_tokens: int
    model_context_tokens: int
    prompt_headroom_tokens: int
    prompt: str
    count_tokens: CountTokens


@dataclass(frozen=True)
class SummaryResult:
    """A finished summary: its text, its size in tokens and the uuid of the last message it covers."""

    content: str
    token_count: int
    summary_point_uuid: UUID


def settings_from(values: Mapping[str, Any], count_tokens: CountTokens) -> SummarizerSettings:
    """Check the summariser settings and return them, or raise ValueError naming the first problem.

    Goes in: a mapping with message_window, summary_max_output_tokens, model_context_tokens and
    prompt_headroom_tokens (positive whole numbers), optionally summarizer_prompt (text holding
    both identifiers; Zep's default prompt when absent), and a token counter. Comes out: the checked
    settings. Zep's own values (12, 1024, a per-model table with a fallback of 2048, 250) are not
    defaults here: each must be set.
    """
    numbers: dict[str, int] = {}
    for key in (
        "message_window",
        "summary_max_output_tokens",
        "model_context_tokens",
        "prompt_headroom_tokens",
    ):
        value = values.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"the setting {key} must be a whole number of at least 1")
        numbers[key] = value
    prompt = values.get("summarizer_prompt", DEFAULT_SUMMARY_PROMPT)
    validate_summarizer_prompt(prompt)
    if (
        numbers["model_context_tokens"]
        - numbers["summary_max_output_tokens"]
        - numbers["prompt_headroom_tokens"]
        < 1
    ):
        raise ValueError(
            "model_context_tokens must exceed summary_max_output_tokens plus prompt_headroom_tokens"
        )
    return SummarizerSettings(prompt=prompt, count_tokens=count_tokens, **numbers)


def drop_empty_messages(messages: Sequence[Message]) -> list[Message]:
    """Return the messages without those whose content is only whitespace, order kept.

    Original: pkg/tasks/message_utils.go, dropEmptyMessages. It never changes the input list.
    """
    return [m for m in messages if m.content.strip()]


def validate_summarizer_prompt(prompt: str) -> None:
    """Raise ValueError unless the prompt holds both {{.PrevSummary}} and {{.MessagesJoined}}.

    Original: pkg/tasks/message_summarizer.go, validateSummarizerPrompt (same refusal words).
    """
    if PREV_SUMMARY_IDENTIFIER not in prompt or MESSAGES_JOINED_IDENTIFIER not in prompt:
        raise ValueError(
            "wrong summary prompt format. please make sure it contains the identifiers "
            f"{PREV_SUMMARY_IDENTIFIER} and {MESSAGES_JOINED_IDENTIFIER}"
        )


def render_prompt(prompt: str, previous_summary: str, messages_joined: str) -> str:
    """Fill the two identifiers of a summary prompt and return the finished prompt.

    Original: pkg/tasks/message_summarizer.go, generateProgressiveSummarizerPrompt (the template
    fill). Only the two identifiers are replaced; Go's other template actions are not supported.
    Raises ValueError for a prompt missing either identifier.
    """
    validate_summarizer_prompt(prompt)
    return prompt.replace(PREV_SUMMARY_IDENTIFIER, previous_summary).replace(
        MESSAGES_JOINED_IDENTIFIER, messages_joined
    )


def is_due(messages: Sequence[Message], settings: SummarizerSettings) -> bool:
    """Say whether a summary is due: the non-empty unsummarised messages reach the message window.

    Original: pkg/tasks/message_summarizer.go, Execute (the check "len(messages) < MessageWindow").
    """
    return len(drop_empty_messages(messages)) >= settings.message_window


async def incremental_summarizer(
    current_summary: str,
    lines: Sequence[str],
    settings: SummarizerSettings,
    complete: Complete,
) -> tuple[str, int]:
    """Fold some message lines into a summary with one model call; return the new text and its tokens.

    Original: pkg/tasks/message_summarizer.go, incrementalSummarizer. The model is asked with the
    rendered prompt and at most summary_max_output_tokens; the answer is trimmed. Raises
    ValueError when there are no lines.
    """
    if len(lines) < 1:
        raise ValueError("no messages provided")
    prompt = render_prompt(settings.prompt, current_summary, "\n".join(lines))
    answer = (await complete(prompt, settings.summary_max_output_tokens)).strip()
    return answer, settings.count_tokens(answer)


async def process_over_limit_messages(
    messages: Sequence[Message],
    max_input_tokens: int,
    summary: str,
    settings: SummarizerSettings,
    complete: Complete,
) -> SummaryResult:
    """Fold messages into a summary in batches that each fit the input budget.

    Original: pkg/tasks/message_summarizer.go, processOverLimitMessages. Each message becomes the
    line "role: content"; a batch is closed and summarised when adding the next line would pass
    the budget, and the last batch is summarised at the end. The summary point is the last message.
    Departure from the original, stated: the original passes the first summary string to every
    batch, so a second batch discards the first batch's result; here each batch starts from the
    result of the one before it. Raises ValueError when there are no messages.
    """
    if not messages:
        raise ValueError("no messages to summarize")
    point = messages[-1].uuid
    pending: list[str] = []
    used = 0
    text, tokens = summary, 0

    async def flush() -> None:
        """Summarise the pending lines onto the running summary and start a new batch."""
        nonlocal pending, used, text, tokens
        text, tokens = await incremental_summarizer(text, pending, settings, complete)
        pending, used = [], 0

    for message in messages:
        line = f"{message.role}: {message.content}"
        cost = settings.count_tokens(line)
        if used + cost > max_input_tokens and pending:
            await flush()
        pending.append(line)
        used += cost
    if pending:
        await flush()
    return SummaryResult(text, tokens, point)


async def summarize(
    messages: Sequence[Message],
    previous: Optional[str],
    settings: SummarizerSettings,
    complete: Complete,
) -> SummaryResult:
    """Summarise the older half of the unsummarised messages onto the previous summary.

    Original: pkg/tasks/message_summarizer.go, summarize. The newest message_window // 2 messages
    stay unsummarised; the rest are folded in, with an input budget of model_context_tokens minus
    summary_max_output_tokens minus prompt_headroom_tokens. Raises ValueError when the model gives
    an empty summary ("no summary found after summarization") or there is nothing to fold in.
    """
    keep = settings.message_window // 2
    older = list(messages)[: len(messages) - keep]
    budget = (
        settings.model_context_tokens
        - settings.summary_max_output_tokens
        - settings.prompt_headroom_tokens
    )
    result = await process_over_limit_messages(older, budget, previous or "", settings, complete)
    if not result.content:
        raise ValueError("no summary found after summarization")
    return result
