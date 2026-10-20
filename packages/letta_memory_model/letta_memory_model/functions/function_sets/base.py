"""Base functions docstring."""

from typing import TYPE_CHECKING, Any, Optional

from letta_memory_model.constants import (
    CORE_MEMORY_LINE_NUMBER_WARNING as LETTA_LINE_NUMBER_BANNER,
)
from letta_memory_model.constants import (
    MEMORY_TOOLS_LINE_NUMBER_PREFIX_REGEX,
    READ_ONLY_BLOCK_EDIT_ERROR,
)
from letta_memory_model.schemas.block import Block as PydanticBlock

if TYPE_CHECKING:
    from letta_memory_model.schemas.agent import AgentState

# Licensed under Apache-2.0 from Letta archive
CORE_MEMORY_LINE_NUMBER_WARNING = "Line numbers are for display purposes only"
SNIPPET_LINES = 3


def core_memory_append(agent_state: "AgentState", label: str, content: str) -> str:
    """Method docstring."""
    if getattr(agent_state.memory.get_block(label), "read_only", False):
        raise ValueError(f"Block {label} is read-only and cannot be edited.")
    current_value = str(agent_state.memory.get_block(label).value)
    new_value = current_value + str(content)
    agent_state.memory.update_block_value(label=label, value=new_value)
    return new_value


def core_memory_replace(
    agent_state: "AgentState", label: str, old_content: str, new_content: str
) -> str:
    """Method docstring."""
    if getattr(agent_state.memory.get_block(label), "read_only", False):
        raise ValueError(f"Block {label} is read-only and cannot be edited.")
    current_value = str(agent_state.memory.get_block(label).value)
    if old_content not in current_value:
        raise ValueError(f"Old content '{old_content}' not found in memory block '{label}'")
    new_value = current_value.replace(str(old_content), str(new_content))
    agent_state.memory.update_block_value(label=label, value=new_value)
    return new_value


def memory_replace(agent_state: "AgentState", label: str, old_string: str, new_string: str) -> str:
    """Method docstring."""
    if getattr(agent_state.memory.get_block(label), "read_only", False):
        raise ValueError(f"Block {label} is read-only and cannot be edited.")
    import re

    if bool(re.search("\\nLine \\d+: ", old_string)):
        raise ValueError(
            "old_string contains a line number prefix, which is not allowed. Do not include line numbers when calling memory tools (line numbers are for display purposes only)."
        )
    if CORE_MEMORY_LINE_NUMBER_WARNING in old_string:
        raise ValueError(
            "old_string contains a line number warning, which is not allowed. Do not include line number information when calling memory tools (line numbers are for display purposes only)."
        )
    if bool(re.search("\\nLine \\d+: ", new_string)):
        raise ValueError(
            "new_string contains a line number prefix, which is not allowed. Do not include line numbers when calling memory tools (line numbers are for display purposes only)."
        )
    old_string = str(old_string).expandtabs()
    new_string = str(new_string).expandtabs()
    current_value = str(agent_state.memory.get_block(label).value).expandtabs()
    occurences = current_value.count(old_string)
    if occurences == 0:
        raise ValueError(
            f"No replacement was performed, old_string `{old_string}` did not appear verbatim in memory block with label `{label}`."
        )
    elif occurences > 1:
        content_value_lines = current_value.split("\n")
        lines = [idx + 1 for idx, line in enumerate(content_value_lines) if old_string in line]
        raise ValueError(
            f"No replacement was performed. Multiple occurrences of old_string `{old_string}` in lines {lines}. Please ensure it is unique."
        )
    new_value = current_value.replace(str(old_string), str(new_string))
    agent_state.memory.update_block_value(label=label, value=new_value)
    return new_value


def memory_insert(
    agent_state: "AgentState", label: str, new_string: str, insert_line: int = -1
) -> str:
    """Method docstring."""
    if getattr(agent_state.memory.get_block(label), "read_only", False):
        raise ValueError(f"Block {label} is read-only and cannot be edited.")
    import re

    if bool(re.search("\\nLine \\d+: ", new_string)):
        raise ValueError(
            "new_string contains a line number prefix, which is not allowed. Do not include line numbers when calling memory tools (line numbers are for display purposes only)."
        )
    if CORE_MEMORY_LINE_NUMBER_WARNING in new_string:
        raise ValueError(
            "new_string contains a line number warning, which is not allowed. Do not include line number information when calling memory tools (line numbers are for display purposes only)."
        )
    current_value = str(agent_state.memory.get_block(label).value).expandtabs()
    new_string = str(new_string).expandtabs()
    current_value_lines = current_value.split("\n")
    n_lines = len(current_value_lines)
    if insert_line == -1:
        insert_line = n_lines
    elif insert_line < 0 or insert_line > n_lines:
        raise ValueError(
            f"Invalid `insert_line` parameter: {insert_line}. It should be within the range of lines of the memory block: {[0, n_lines]}, or -1 to append to the end of the memory block."
        )
    new_string_lines = new_string.split("\n")
    new_value_lines = (
        current_value_lines[:insert_line] + new_string_lines + current_value_lines[insert_line:]
    )
    new_value = "\n".join(new_value_lines)
    agent_state.memory.update_block_value(label=label, value=new_value)
    return new_value


def _apply_unified_patch_to_value(current_value: str, patch_text: str) -> str:
    """Apply the '@@' hunks of a simplified unified diff to one block's text.

    Goes in: the block's current text and the diff text; comes out: the new text. Each hunk's
    context and removed lines must match exactly one place in the text. It never edits a block
    itself and refuses a diff with no hunk, with a hunk that matches nowhere, or with one that
    matches in several places.
    """
    current_lines = str(current_value).expandtabs().split("\n")
    patch_lines = [
        line
        for line in str(patch_text).expandtabs().splitlines()
        if not line.startswith(("*** ", "---", "+++"))
    ]
    hunks: list[list[str]] = []
    hunk: list[str] = []
    for line in patch_lines:
        if line.startswith("@@"):
            if hunk:
                hunks.append(hunk)
                hunk = []
        elif line.startswith((" ", "-", "+")):
            hunk.append(line)
        elif line.strip() == "":
            hunk.append(" ")
    if hunk:
        hunks.append(hunk)
    if not hunks:
        raise ValueError(
            "No applicable hunks found in patch. Ensure lines start with ' ', '-', or '+'."
        )
    for hunk in hunks:
        expected = [line[1:] for line in hunk if line.startswith((" ", "-"))]
        replacement = [line[1:] for line in hunk if line.startswith((" ", "+"))]
        if not expected:
            current_lines = current_lines + replacement
            continue
        size = len(expected)
        matches = [
            start
            for start in range(len(current_lines) - size + 1)
            if current_lines[start : start + size] == expected
        ]
        if not matches:
            sample = "\n".join(expected[:4])
            raise ValueError(
                "Failed to apply patch: expected hunk context not found in the memory block. "
                f"Verify the target lines exist and try providing more context. Expected start:\n{sample}"
            )
        if len(matches) > 1:
            raise ValueError(
                "Failed to apply patch: hunk context matched multiple places in the memory block. "
                "Please add more unique surrounding context to disambiguate."
            )
        start = matches[0]
        current_lines = current_lines[:start] + replacement + current_lines[start + size :]
    return "\n".join(current_lines)


_EXTENDED_PATCH_HEADERS = (
    "*** Add Block:",
    "*** Delete Block:",
    "*** Update Block:",
    "*** Move to:",
)


def _parse_patch_operations(patch: str) -> list[dict[str, Any]]:
    """Split a multi-block patch into its add, delete, update and rename operations, in order.

    Goes in: the patch text with '*** Add Block:', '*** Delete Block:', '*** Update Block:' and
    '*** Move to:' headers; comes out: one dictionary per operation. It never touches a block and
    refuses an empty label, a 'Move to' with nothing before it, and a patch with no operation.
    """
    actions: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None
    last_label: Optional[str] = None
    lines = patch.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        header = next((h for h in _EXTENDED_PATCH_HEADERS if line.startswith(h)), None)
        if header is None:
            if current is not None:
                if current["kind"] == "add":
                    if line.startswith("+"):
                        current["content_lines"].append(line[1:])
                    elif line.strip() == "":
                        current["content_lines"].append("")
                elif current["kind"] == "update":
                    current["patch_lines"].append(line)
            index += 1
            continue
        name = line.split(":", 1)[1].strip()
        if not name:
            raise ValueError(f"{header} must specify a non-empty label")
        index += 1
        if header == "*** Move to:":
            if last_label is None:
                raise ValueError("*** Move to: must follow an Add/Update/Delete header")
            if current is not None:
                actions.append(current)
                current = None
            actions.append({"kind": "rename", "old_label": last_label, "new_label": name})
            last_label = name
            continue
        if current is not None:
            actions.append(current)
            current = None
        last_label = name
        if header == "*** Delete Block:":
            actions.append({"kind": "delete", "label": name})
        elif header == "*** Update Block:":
            current = {"kind": "update", "label": name, "patch_lines": []}
        else:
            current = {"kind": "add", "label": name, "description": "", "content_lines": []}
            if index < len(lines) and lines[index].startswith("Description:"):
                current["description"] = lines[index].split(":", 1)[1].strip()
                index += 1
    if current is not None:
        actions.append(current)
    if not actions:
        raise ValueError(
            "No operations found. Provide at least one of: *** Add Block, *** Delete Block, *** Update Block."
        )
    return actions


def memory_apply_patch(agent_state: "AgentState", label: str, patch: str) -> str:
    """Apply a simplified unified diff to one memory block, or a multi-block patch to several.

    Goes in: the agent's state, the label of the block to patch, and the patch text; comes out:
    the block's new text for a plain diff, or a report of the operations done for a patch that
    carries '*** Add Block', '*** Delete Block', '*** Update Block' or '*** Move to' headers.
    It never accepts line-number prefixes, never edits a read-only block, and writes nothing
    itself: the store saves what changed on the agent's memory.
    """
    if MEMORY_TOOLS_LINE_NUMBER_PREFIX_REGEX.search(patch or ""):
        raise ValueError(
            "Patch contains a line number prefix, which is not allowed. Do not include line numbers (they are for display only)."
        )
    if LETTA_LINE_NUMBER_BANNER in (patch or ""):
        raise ValueError(
            "Patch contains the line number warning banner, which is not allowed. Provide only the text to edit."
        )
    patch = str(patch).expandtabs()
    memory = agent_state.memory
    if not any(line.startswith(_EXTENDED_PATCH_HEADERS) for line in patch.splitlines()):
        try:
            block = memory.get_block(label)
        except KeyError as error:
            raise ValueError(f"Error: Memory block '{label}' does not exist") from error
        if block.read_only:
            raise ValueError(READ_ONLY_BLOCK_EDIT_ERROR)
        new_value = _apply_unified_patch_to_value(str(block.value), patch)
        memory.update_block_value(label=label, value=new_value)
        return new_value
    results: list[str] = []
    for action in _parse_patch_operations(patch):
        kind = action["kind"]
        if kind == "add":
            if action["label"] in memory.list_block_labels():
                raise ValueError(f"Error: Memory block '{action['label']}' already exists")
            memory.set_block(
                PydanticBlock(
                    label=action["label"],
                    value="\n".join(action["content_lines"]).rstrip("\n"),
                    description=action["description"],
                )
            )
            results.append(f"Created memory block '{action['label']}'")
        elif kind == "delete":
            doomed = memory.get_block(action["label"])
            memory.blocks = [block for block in memory.blocks if block.id != doomed.id]
            results.append(f"Deleted memory block '{action['label']}'")
        elif kind == "rename":
            if action["new_label"] in memory.list_block_labels():
                raise ValueError(f"Error: Memory block '{action['new_label']}' already exists")
            memory.get_block(action["old_label"]).label = action["new_label"]
            results.append(
                f"Renamed memory block '{action['old_label']}' to '{action['new_label']}'"
            )
        else:
            try:
                block = memory.get_block(action["label"])
            except KeyError as error:
                raise ValueError(
                    f"Error: Memory block '{action['label']}' does not exist"
                ) from error
            if block.read_only:
                raise ValueError(READ_ONLY_BLOCK_EDIT_ERROR)
            memory.update_block_value(
                label=action["label"],
                value=_apply_unified_patch_to_value(
                    str(block.value), "\n".join(action["patch_lines"])
                ),
            )
            results.append(f"Updated memory block '{action['label']}'")
    return (
        "Successfully applied memory patch operations.\n\nOperations completed:\n- "
        + "\n- ".join(results)
    )


def memory_rethink(agent_state: "AgentState", label: str, new_memory: str) -> str:
    """Method docstring."""
    import re

    if bool(re.search("\\nLine \\d+: ", new_memory)):
        raise ValueError(
            "new_memory contains a line number prefix, which is not allowed. Do not include line numbers when calling memory tools (line numbers are for display purposes only)."
        )
    if CORE_MEMORY_LINE_NUMBER_WARNING in new_memory:
        raise ValueError(
            "new_memory contains a line number warning, which is not allowed. Do not include line number information when calling memory tools (line numbers are for display purposes only)."
        )

    if getattr(agent_state.memory.get_block(label), "read_only", False):
        raise ValueError(f"Block {label} is read-only and cannot be edited.")

    agent_state.memory.update_block_value(label=label, value=new_memory)
    return new_memory
