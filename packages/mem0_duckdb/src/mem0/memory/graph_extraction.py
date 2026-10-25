"""The model half of Mem0's graph memory, translated from graph_memory.py without Neo4j.

It is for the three steps of Mem0's graph memory that need a language model: finding the entities
in a text, establishing the relations among them, and deciding which stored relations new
information ends. It takes a model object with generate_response(messages, tools) and plain
text; it gives back plain dictionaries. It never stores a relation, never searches by similarity
(that needs numbers) and never touches a database.

Original: mem0ai/mem0 release v1.0.11, mem0/memory/graph_memory.py, class MemoryGraph, methods
_retrieve_nodes_from_data, _establish_nodes_relations_from_data and
_get_delete_entities_from_search_output. The prompts and tools are in mem0/graphs.
"""

from typing import Any

from mem0.graphs.tools import DELETE_MEMORY_TOOL_GRAPH, EXTRACT_ENTITIES_TOOL, RELATIONS_TOOL
from mem0.graphs.utils import EXTRACT_RELATIONS_PROMPT, get_delete_messages
from mem0.memory.utils import format_entities, remove_spaces_from_entities


def identity_text(user_id: str, agent_id: str | None = None, run_id: str | None = None) -> str:
    """Return the identity string the graph prompts name, as _establish_nodes_relations_from_data builds it.

    It takes the user id and optionally an agent id and a run id. It returns "user_id: ..." with the
    other two appended when given. It never calls a model.
    """
    text = f"user_id: {user_id}"
    if agent_id:
        text += f", agent_id: {agent_id}"
    if run_id:
        text += f", run_id: {run_id}"
    return text


def retrieve_nodes_from_data(llm: Any, data: str, user_id: str) -> dict[str, str]:
    """Ask the model for every entity in a text and return a map of entity name to entity type.

    Original: _retrieve_nodes_from_data. It takes the model, the text and the user id (self
    references such as "I" become that user). It returns names and types lower-cased with spaces
    turned into underscores. A reply with no extract_entities call gives an empty map; it never
    invents an entity and never answers a question in the text.
    """
    reply = llm.generate_response(
        messages=[
            {
                "role": "system",
                "content": f"You are a smart assistant who understands entities and their types in a given text. If user message contains self reference such as 'I', 'me', 'my' etc. then use {user_id} as the source entity. Extract all the entities from the text. ***DO NOT*** answer the question itself if the given text is a question.",
            },
            {"role": "user", "content": data},
        ],
        tools=[EXTRACT_ENTITIES_TOOL],
    )
    found: dict[str, str] = {}
    for call in reply.get("tool_calls", []):
        if call["name"] != "extract_entities":
            continue
        for item in call.get("arguments", {}).get("entities", []):
            found[item["entity"]] = item["entity_type"]
    return {
        name.lower().replace(" ", "_"): kind.lower().replace(" ", "_")
        for name, kind in found.items()
    }


def establish_relations_from_data(
    llm: Any, data: str, identity: str, entity_type_map: dict[str, str], custom_prompt: str | None
) -> list[dict[str, Any]]:
    """Ask the model for the relations among the entities of a text and return them as triplets.

    Original: _establish_nodes_relations_from_data. It takes the model, the text, the identity
    string, the entity map and an optional extra instruction (the original's graph_store
    custom_prompt). It returns a list of source, relationship and destination dictionaries, cleaned
    as the original cleans them. It never stores them.
    """
    system = EXTRACT_RELATIONS_PROMPT.replace("USER_ID", identity)
    if custom_prompt:
        messages = [
            {"role": "system", "content": system.replace("CUSTOM_PROMPT", f"4. {custom_prompt}")},
            {"role": "user", "content": data},
        ]
    else:
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"List of entities: {list(entity_type_map.keys())}. \n\nText: {data}",
            },
        ]
    reply = llm.generate_response(messages=messages, tools=[RELATIONS_TOOL])
    entities: list[dict[str, Any]] = []
    if reply.get("tool_calls"):
        entities = reply["tool_calls"][0].get("arguments", {}).get("entities", [])
    return remove_spaces_from_entities(entities, sanitize_relationship=True)


def delete_decisions(
    llm: Any, stored: list[dict[str, str]], data: str, identity: str
) -> list[dict[str, Any]]:
    """Ask the model which stored relations new information ends and return them.

    Original: _get_delete_entities_from_search_output. It takes the model, the stored relations
    (source, relationship, destination), the new text and the identity string. It returns the
    relations the model asked to delete, cleaned as the original cleans them. It deletes nothing.
    """
    system_prompt, user_prompt = get_delete_messages(format_entities(stored), data, identity)
    reply = llm.generate_response(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tools=[DELETE_MEMORY_TOOL_GRAPH],
    )
    wanted = [
        call.get("arguments")
        for call in reply.get("tool_calls", [])
        if call.get("name") == "delete_graph_memory"
    ]
    return remove_spaces_from_entities(wanted, sanitize_relationship=True)
