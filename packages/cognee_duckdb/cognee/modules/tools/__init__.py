"""Tool subsystem: dispatcher, registry, built-in tools, and loaders."""

from importlib import import_module

# Preserve Cognee 1.6.5 initialization order as executable imports.
# Import sorting must not move registration ahead of its prerequisites.

execute_tool = import_module("cognee.modules.tools.execute_tool", __name__).execute_tool
add_skills = import_module("cognee.modules.tools.ingest_skills", __name__).add_skills
looks_like_skill_source = import_module(
    "cognee.modules.tools.ingest_skills", __name__
).looks_like_skill_source
get_tool = import_module("cognee.modules.tools.registry", __name__).get_tool
list_tools_for_dataset = import_module(
    "cognee.modules.tools.registry", __name__
).list_tools_for_dataset
register_builtin_tool = import_module(
    "cognee.modules.tools.registry", __name__
).register_builtin_tool

# Import built-ins to register them at package load time.
_builtin = import_module("cognee.modules.tools.builtin", __name__)

__all__ = [
    "add_skills",
    "execute_tool",
    "get_tool",
    "list_tools_for_dataset",
    "looks_like_skill_source",
    "register_builtin_tool",
]
