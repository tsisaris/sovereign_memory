"""Counting tokens the way Letta does for a server it has no tokenizer for.

It is for compaction's size checks, translated from ApproxTokenCounter in
letta/services/context_window_calculator/token_counter.py and from count_tokens
and count_tokens_with_tools in summarizer_sliding_window.py. What goes in is
messages (and tool definitions) and the two numbers from the compaction
settings; what comes out is a whole-number estimate. It never loads a tokenizer
and never calls a model.
"""

import json
from collections.abc import Sequence
from typing import Any

from letta_memory_model.messages import to_openai_dicts
from letta_memory_model.schemas.message import Message
from letta_memory_model.summarizer.settings import CompactionSettings


def approx_token_count(text: str, bytes_per_token: int) -> int:
    """Count tokens as the text's UTF-8 bytes divided by bytes_per_token, rounded up.

    Translated from ApproxTokenCounter._approx_token_count (the original fixes
    4 bytes per token; here it is a setting). An empty text counts 0. It never
    looks at the words.
    """
    if not text:
        return 0
    byte_len = len(text.encode("utf-8"))
    return (byte_len + bytes_per_token - 1) // bytes_per_token


def count_tokens(messages: Sequence[Message], settings: CompactionSettings) -> int:
    """Estimate the tokens of messages, with the safety margin the original adds.

    Translated from count_tokens in summarizer_sliding_window.py: the messages are
    written as OpenAI-style JSON, counted by bytes per token, then multiplied by
    the approximate-count safety margin because the heuristic runs low. An empty
    list counts 0. It never changes the messages.
    """
    if not messages:
        return 0
    tokens = approx_token_count(json.dumps(to_openai_dicts(messages)), settings.approx_bytes_per_token)
    return int(tokens * settings.approx_safety_margin)


def count_tokens_with_tools(
    messages: Sequence[Message], tools: Sequence[dict[str, Any]], settings: CompactionSettings
) -> int:
    """Estimate the tokens of messages plus the tool definitions sent with them.

    Translated from count_tokens_with_tools in summarizer_sliding_window.py: the
    tool schemas are counted as JSON the same way and given the same safety margin,
    then added to the message count. With no tools it equals count_tokens. It never
    changes either list.
    """
    message_tokens = count_tokens(messages, settings)
    definitions = [{"type": "function", "function": tool} for tool in tools if tool]
    if not definitions:
        return message_tokens
    tool_tokens = approx_token_count(json.dumps(definitions), settings.approx_bytes_per_token)
    return message_tokens + int(tool_tokens * settings.approx_safety_margin)
