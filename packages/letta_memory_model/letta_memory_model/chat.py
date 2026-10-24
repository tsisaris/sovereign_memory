"""The one door through which Letta's model steps talk to a language model.

It is for the translated summarizer and sleep-time code, which in Letta call a
provider client; here they call a chat function handed in by the caller. What
goes in is a list of OpenAI-style messages and an optional list of tool
definitions; what comes out is a ChatReply holding the text, the tool calls and
the model name the server reported. It never chooses a model, opens a
connection or reads a setting, and it holds no provider code.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any


class ChatError(RuntimeError):
    """A chat call failed; the message says why in the server's own words.

    It is raised by the function that talks to the model and is read by the
    translated code, which decides whether to retry smaller. It carries no
    request data and never wraps a success.
    """


class ContextWindowExceededError(ChatError):
    """The model said the request was longer than its context window.

    Translated from letta.errors.ContextWindowExceededError, which the original
    summarizer catches to retry with clamped tool returns and then a truncated
    transcript. The chat function raises it only when the server says so. It is
    never raised for any other failure.
    """


@dataclass(frozen=True)
class ChatReply:
    """What one chat call answered: text, tool calls, the model reported and why it stopped.

    text is None when the model answered only with tool calls; tool_calls are
    OpenAI-style dicts (id, type, function with name and arguments text); model
    is the name the server put in its reply, or None when it gave none. It never
    holds the request.
    """

    text: str | None
    tool_calls: tuple[dict[str, Any], ...]
    model: str | None
    finish_reason: str | None


Chat = Callable[
    [Sequence[dict[str, Any]], Sequence[dict[str, Any]] | None],
    Awaitable[ChatReply],
]
