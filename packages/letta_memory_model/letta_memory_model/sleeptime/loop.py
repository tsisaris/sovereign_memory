"""The step loop of a background memory agent: ask the model, run its tools, repeat.

It is for the loop that LettaAgentV3.step runs for a sleep-time agent in the original
(letta/agents/letta_agent_v3.py, called from letta/groups/sleeptime_multi_agent_v4.py)
reduced to what a memory agent needs. What goes in is a system prompt builder, the
opening user message, the tools and their runner and the step limit; what comes out is a
LoopResult listing every model call and tool call. It never edits a block itself and never
hides a tool error.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from letta_memory_model.chat import Chat
from letta_memory_model.sleeptime.tools import TERMINAL_TOOLS, SleeptimeTools


@dataclass
class LoopResult:
    """What a loop run did: steps taken, why it stopped, every tool call and the models reported.

    stop_reason is terminal_tool (a terminal tool was called), end_turn (the model made no
    tool call) or max_steps. tool_calls holds the name, arguments, ok, result and error of
    each call in order. It never holds the prompts.
    """

    steps: int = 0
    stop_reason: str = ""
    final_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    models: list[str] = field(default_factory=list)


async def run_memory_agent(
    chat: Chat,
    system_prompt: Callable[[], str],
    user_text: str,
    tool_schemas: Sequence[dict[str, Any]],
    tools: SleeptimeTools,
    max_steps: int,
) -> LoopResult:
    """Run a memory agent until it finishes, makes no tool call, or reaches max_steps.

    Translated from the step loop of LettaAgentV3 with the tool rules of
    letta/services/agent_manager.py: calling memory_finish_edits (a TerminalToolRule) ends
    the run, calling any other tool (a ContinueToolRule) continues, and a reply with no tool
    call ends the turn. The system prompt is rebuilt before every step so edits show in the
    next step, as Letta rebuilds its system message. Tool errors go back to the model as the
    tool's reply and are also recorded.
    """
    result = LoopResult()
    conversation: list[dict[str, Any]] = [{"role": "user", "content": user_text}]
    wrapped = [{"type": "function", "function": schema} for schema in tool_schemas]
    while result.steps < max_steps:
        result.steps += 1
        reply = await chat([{"role": "system", "content": system_prompt()}, *conversation], wrapped)
        if reply.model:
            result.models.append(reply.model)
        result.final_text = reply.text or ""
        if not reply.tool_calls:
            result.stop_reason = "end_turn"
            return result
        conversation.append(
            {"role": "assistant", "content": reply.text, "tool_calls": list(reply.tool_calls)}
        )
        finished = False
        for call in reply.tool_calls:
            function = call.get("function", {})
            name = function.get("name", "")
            arguments = function.get("arguments", "")
            outcome = tools.run(
                name, arguments if isinstance(arguments, str) else _as_text(arguments)
            )
            result.tool_calls.append(
                {
                    "name": name,
                    "arguments": arguments,
                    "ok": outcome.ok,
                    "result": outcome.result,
                    "error": outcome.error,
                }
            )
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": outcome.result if outcome.ok else outcome.error,
                }
            )
            if name in TERMINAL_TOOLS and outcome.ok:
                finished = True
        if finished:
            result.stop_reason = "terminal_tool"
            return result
    result.stop_reason = "max_steps"
    return result


def _as_text(arguments: Any) -> str:
    """Write tool arguments that arrived as an object as the JSON text the tools expect.

    Some servers return arguments already parsed; the tools take the text form. It
    changes nothing else.
    """
    return json.dumps(arguments)
