"""Base functions docstring."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from letta_memory_model.schemas.agent import AgentState
"""Module docstring."""

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


def memory_apply_patch(agent_state: "AgentState", label: str, patch: str) -> str:
    """Method docstring."""
    if getattr(agent_state.memory.get_block(label), "read_only", False):
        raise ValueError(f"Block {label} is read-only and cannot be edited.")
    raise NotImplementedError(
        "This should never be invoked directly. Contact Letta if you see this error message."
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
