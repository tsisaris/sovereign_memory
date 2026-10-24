"""The memory tools a sleep-time agent may call, their schemas and the rules that gate them.

It is for the tool side of Letta's sleep-time agent: the four tools of
BASE_SLEEPTIME_TOOLS in letta/constants.py (memory_replace, memory_insert,
memory_rethink, memory_finish_edits), their descriptions from
letta/functions/function_sets/base.py, and the two tool rules letta/services/
agent_manager.py gives them (memory_finish_edits is a TerminalToolRule, the rest are
ContinueToolRule). What goes in is a store, the agent and a tool call; what comes out
is the tool's result or its error text. It never calls a model.
"""

import json
from dataclasses import dataclass
from typing import Any, Optional

from letta_memory_model.functions.function_sets.base import (
    memory_insert,
    memory_replace,
    memory_rethink,
)
from letta_memory_model.letta_duckdb_store import LettaMemoryStore, LettaStoreError

MEMORY_FINISH_EDITS = "memory_finish_edits"
SLEEPTIME_TOOL_NAMES = ("memory_replace", "memory_insert", "memory_rethink", MEMORY_FINISH_EDITS)
TERMINAL_TOOLS = frozenset({MEMORY_FINISH_EDITS})


def _function(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict:
    """Wrap one tool's name, description and parameters in the OpenAI function-tool shape.

    Goes in: the tool's parts; comes out: the schema dict a chat call sends. It never
    checks the parameters against anything.
    """
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }


SLEEPTIME_TOOL_SCHEMAS: tuple[dict[str, Any], ...] = (
    _function(
        "memory_replace",
        "The memory_replace command allows you to replace a specific string in a memory block "
        "with a new string. This is used for making precise edits.\nDo NOT attempt to replace "
        "long strings, e.g. do not attempt to replace the entire contents of a memory block "
        "with a new string.",
        {
            "label": {
                "type": "string",
                "description": "Section of the memory to be edited, identified by its label.",
            },
            "old_string": {
                "type": "string",
                "description": "The text to replace (must match exactly, including whitespace "
                "and indentation).",
            },
            "new_string": {
                "type": "string",
                "description": "The new text to insert in place of the old text. Do not include "
                "line number prefixes.",
            },
        },
        ["label", "old_string", "new_string"],
    ),
    _function(
        "memory_insert",
        "The memory_insert command allows you to insert text at a specific location in a "
        "memory block.",
        {
            "label": {
                "type": "string",
                "description": "Section of the memory to be edited, identified by its label.",
            },
            "new_string": {
                "type": "string",
                "description": "The text to insert. Do not include line number prefixes.",
            },
            "insert_line": {
                "type": "integer",
                "description": "The line number after which to insert the text (0 for beginning "
                "of file). Defaults to -1 (end of the file).",
            },
        },
        ["label", "new_string"],
    ),
    _function(
        "memory_rethink",
        "The memory_rethink command allows you to completely rewrite the contents of a memory "
        "block. Use this tool to make large sweeping changes (e.g. when you want to condense or "
        "reorganize the memory blocks), do NOT use this tool to make small precise edits (e.g. "
        "add or remove a line, replace a specific string, etc).",
        {
            "label": {
                "type": "string",
                "description": "The memory block to be rewritten, identified by its label.",
            },
            "new_memory": {
                "type": "string",
                "description": "The new memory contents with information integrated from "
                "existing memory blocks and the conversation context.",
            },
        },
        ["label", "new_memory"],
    ),
    _function(
        MEMORY_FINISH_EDITS,
        "Call the memory_finish_edits command when you are finished making edits (integrating "
        "all new information) into the memory blocks. This function is called when the agent is "
        "done rethinking the memory.",
        {},
        [],
    ),
)

_EDITORS = {
    "memory_replace": memory_replace,
    "memory_insert": memory_insert,
    "memory_rethink": memory_rethink,
}


@dataclass(frozen=True)
class ToolOutcome:
    """What one tool call did: whether it worked, its result text and its error text.

    Letta sends a tool's error back to the model as the tool's reply so it can try
    again; this keeps the same text and also records it for the caller. Exactly one
    of result and error is set. It never raises.
    """

    ok: bool
    result: Optional[str]
    error: Optional[str]


class SleeptimeTools:
    """Runs a sleep-time agent's tool calls on its stored memory blocks.

    It is built from a store, the organization and the sleep-time agent. run takes a
    tool name and the arguments text the model wrote and returns a ToolOutcome. Every
    edit goes through the store's run_memory_tool, so a bad edit (read-only block, text
    not found, limit exceeded) writes nothing. It never calls a model.
    """

    def __init__(
        self, store: LettaMemoryStore, organization_id: str, agent_id: str, actor_id: Optional[str]
    ) -> None:
        """Remember the store, the organization, the agent whose blocks are edited and the editor."""
        self._store = store
        self._organization_id = organization_id
        self._agent_id = agent_id
        self._actor_id = actor_id

    def run(self, name: str, arguments: str) -> ToolOutcome:
        """Run one tool call and return its outcome; an error becomes the outcome's error text.

        Unknown tools, arguments that are not a JSON object, wrong argument names and
        every refusal of the memory tools or the store are returned as errors, which
        the loop shows to the model. memory_finish_edits changes nothing and returns
        None as a result of "None", as in the original.
        """
        if name == MEMORY_FINISH_EDITS:
            return ToolOutcome(True, "None", None)
        editor = _EDITORS.get(name)
        if editor is None:
            return ToolOutcome(False, None, f"Unknown tool {name}")
        try:
            parsed = json.loads(arguments) if arguments else {}
            if not isinstance(parsed, dict):
                raise ValueError("Tool arguments must be a JSON object")
            result = self._store.run_memory_tool(
                self._organization_id, self._agent_id, editor, actor_id=self._actor_id, **parsed
            )
        except (ValueError, TypeError, LookupError, LettaStoreError) as error:
            return ToolOutcome(False, None, f"{type(error).__name__}: {error}")
        return ToolOutcome(True, "None" if result is None else str(result), None)
