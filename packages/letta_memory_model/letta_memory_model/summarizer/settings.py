"""The compaction settings, translated from letta/services/summarizer/summarizer_config.py.

It is for the numbers and switches that steer compaction. What goes in is a
plain dict read from the memory database's settings; what comes out is a
checked CompactionSettings object. Every field is required and none has a
default, because the original publishes some of these values (for example 0.30
for the share of messages evicted, 500 and 300 for the word limits, 50000 for the
clip length) but this project keeps every tunable number in its settings, never
in code. It never reads the database itself.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from letta_memory_model.summarizer.prompts import (
    ALL_PROMPT,
    SELF_ALL_PROMPT,
    SELF_SLIDING_PROMPT,
    SLIDING_PROMPT,
)

Mode = Literal["all", "sliding_window", "self_compact_all", "self_compact_sliding_window"]


class CompactionSettings(BaseModel):
    """Every setting compaction needs; all required, none with a default.

    Translated from CompactionSettings in summarizer_config.py (mode, prompt,
    prompt_acknowledgement, clip_chars, sliding_window_percentage) and from the
    values the original wrote into its code: the 10 per cent eviction step
    (summarizer_sliding_window.py), the byte-per-token guess and safety margin
    (token_counter.py ApproxTokenCounter and summarizer_sliding_window.py), the
    trigger multiplier (constants.py), the tool-return clamp (constants.py) and the
    transcript-truncation budget (summarizer.py simple_summary). prompt may be
    None, meaning the mode's own prompt. It never reads storage.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Mode = Field(description="Which way of summarizing to use.")
    prompt: str | None = Field(
        description="The summary instructions; null means the prompt that belongs to the mode."
    )
    prompt_acknowledgement: bool = Field(
        description="Whether to add an assistant acknowledgement before the transcript."
    )
    clip_chars: int | None = Field(
        description="Longest summary kept, in characters; null means no clipping."
    )
    sliding_window_percentage: float = Field(
        description="Share of the context window left after a sliding-window summary."
    )
    eviction_step: float = Field(
        description="How much more of the history is evicted on each retry of a sliding window."
    )
    all_word_limit: int = Field(description="Word limit written into the all-mode prompts.")
    sliding_word_limit: int = Field(description="Word limit written into the sliding prompts.")
    context_window_tokens: int = Field(description="Size of the model's context window in tokens.")
    trigger_multiplier: float = Field(
        description="Share of the context window at which compaction starts."
    )
    approx_bytes_per_token: int = Field(
        description="Bytes counted as one token by the approximate counter."
    )
    approx_safety_margin: float = Field(
        description="Factor added to approximate counts because they run low."
    )
    tool_return_truncation_chars: int = Field(
        description="Longest tool reply kept when the first summary request is too long."
    )
    fallback_budget_fraction: float = Field(
        description="Share of the context window the transcript may fill after truncation."
    )
    fallback_bytes_per_token: int = Field(
        description="Bytes per token used to turn that share into a byte budget."
    )
    fallback_min_budget_bytes: int = Field(description="Smallest transcript byte budget allowed.")
    fallback_overhead_bytes: int = Field(
        description="Bytes reserved for the prompt wrapper when the transcript is cut."
    )
    truncate_head_fraction: float = Field(
        description="Share of the budget kept from the start of a truncated transcript."
    )
    truncate_tail_fraction: float = Field(
        description="Share of the budget kept from the end of a truncated transcript."
    )
    timezone: str = Field(description="The time zone the summary note's time is written in.")


def parse_compaction_settings(raw: dict[str, Any]) -> CompactionSettings:
    """Check a settings dict and return it as CompactionSettings.

    Goes in: the dict read from the database; comes out: the checked object.
    A missing key, an unknown key, a wrong type or a sliding_window_percentage
    above 1.0 (the original's assertion) raises a ValueError naming the problem.
    It never fills in a missing value.
    """
    settings = CompactionSettings.model_validate(raw)
    if settings.sliding_window_percentage > 1.0:
        raise ValueError("Sliding window percentage must be less than or equal to 1.0")
    return settings


def get_default_prompt_for_mode(mode: Mode, settings: CompactionSettings) -> str:
    """Return the prompt that belongs to a mode, with the word limits from the settings.

    Translated from get_default_prompt_for_mode in summarizer_config.py. The four
    prompts are the original's text word for word; only the two word limits come
    from the settings instead of two constants. It never changes the settings.
    """
    if mode == "self_compact_sliding_window":
        return SELF_SLIDING_PROMPT.format(sliding_word_limit=settings.sliding_word_limit)
    if mode == "self_compact_all":
        return SELF_ALL_PROMPT.format(all_word_limit=settings.all_word_limit)
    if mode == "sliding_window":
        return SLIDING_PROMPT.format(sliding_word_limit=settings.sliding_word_limit)
    return ALL_PROMPT.format(all_word_limit=settings.all_word_limit)
