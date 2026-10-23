"""Cognee's language-model steps, driven only by the memory database's language_model settings.

The service builds one CogneeModel from the validated language_model settings and every Cognee
step that needs a language model goes through it, so changing the setting and restarting the
service changes the model Cognee uses and nothing else. Nothing here names a model, an address
or a time limit; they all come from the settings it is given. It makes no numbers, no embeddings
and no classifier calls.
"""

import asyncio
import json
from datetime import datetime
from typing import Any

from cognee.infrastructure.databases.graph import get_graph_engine
from cognee.infrastructure.llm.config import get_llm_config
from cognee.infrastructure.llm.extraction.knowledge_graph.extract_content_graph import (
    extract_content_graph,
)
from cognee.infrastructure.llm.extraction.knowledge_graph.extract_event_graph import (
    extract_event_graph,
)
from cognee.infrastructure.llm.LLMGateway import LLMGateway
from cognee.infrastructure.llm.prompts import render_prompt
from cognee.modules.engine.models.Event import Event
from cognee.modules.engine.utils.generate_event_datapoint import generate_event_datapoint
from cognee.modules.engine.utils.timestamp_from_text import timestamp_from_text
from cognee.modules.retrieval.temporal_hybrid.matching import extract_query_interval, to_epoch_ms
from cognee.shared.data_models import KnowledgeGraph
from cognee.tasks.memify.consolidate_entity_descriptions.constants import prompt_name
from cognee.tasks.memify.consolidate_entity_descriptions.read_neighborhood import (
    get_all_entity_nodes,
    get_entity_neighborhood,
)
from cognee.tasks.memify.consolidate_entity_descriptions.rewrite_entities import (
    build_node_neighborhood_prompt,
    query_LLM,
)
from cognee.tasks.temporal_graph.add_entities_to_event import add_entities_to_event
from cognee.tasks.temporal_graph.enrich_events import enrich_events
from cognee.tasks.temporal_graph.models import Event as FoundEvent
from cognee.tasks.temporal_graph.models import EventList

STEP_NAMES: dict[str, tuple[str, ...]] = {
    "extract_graph": ("text",),
    "extract_events": ("text",),
    "attach_event_entities": ("events",),
    "query_window": ("question",),
    "date_anchors": ("starts_at", "ends_at", "chunk_ids", "entity_ids"),
    "rewrite_entity_description": ("name",),
}
LANGUAGE_MODEL_KEYS = (
    "provider",
    "model",
    "base_url",
    "api_key",
    "request_timeout_seconds",
    "options",
)
OPTIONS_FOR_COGNEE = ("temperature", "max_tokens", "top_p", "reasoning_effort")
STAGE_FIELDS_CLEARED = (
    "llm_extraction_model",
    "llm_extraction_provider",
    "llm_extraction_endpoint",
    "llm_summarization_model",
    "llm_summarization_provider",
    "llm_summarization_endpoint",
    "llm_query_model",
    "llm_query_provider",
    "llm_query_endpoint",
    "fallback_api_key",
    "fallback_endpoint",
    "fallback_model",
)


def cognee_llm_fields(language_model: dict[str, Any]) -> dict[str, Any]:
    """Return the Cognee LLM configuration fields made from the language_model settings.

    It takes the validated settings dict (provider, model, base_url, api_key,
    request_timeout_seconds and options) and gives back the field names and values for Cognee's
    own LLM configuration, reaching Ollama through its OpenAI-style door at base_url plus /v1.
    The options temperature, top_p and reasoning_effort become model arguments, max_tokens
    becomes the completion limit and every other option is left out; the time limit becomes the
    request timeout. It raises ValueError for a missing key, a provider other than ollama or a
    value of the wrong kind, and never supplies a default.
    """
    if not isinstance(language_model, dict):
        raise ValueError("language_model must be a dict of settings")
    missing = [key for key in LANGUAGE_MODEL_KEYS if key not in language_model]
    if missing:
        raise ValueError(f"language_model is missing: {', '.join(missing)}")
    if language_model["provider"] != "ollama":
        raise ValueError("language_model.provider must be ollama")
    for key in ("model", "base_url", "api_key"):
        if not isinstance(language_model[key], str) or not language_model[key]:
            raise ValueError(f"language_model.{key} must be a non-empty string")
    timeout = language_model["request_timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("language_model.request_timeout_seconds must be a positive number")
    options = language_model["options"]
    if not isinstance(options, dict):
        raise ValueError("language_model.options must be a table of model options")

    arguments: dict[str, Any] = {"timeout": timeout}
    for key in ("temperature", "top_p", "reasoning_effort"):
        if key in options:
            arguments[key] = options[key]
    fields: dict[str, Any] = {
        "llm_provider": "openai",
        "llm_model": f"openai/{language_model['model']}",
        "llm_endpoint": language_model["base_url"].rstrip("/") + "/v1",
        "llm_api_key": language_model["api_key"],
        "llm_args": arguments,
    }
    if "max_tokens" in options:
        fields["llm_max_completion_tokens"] = options["max_tokens"]
    return fields


_REPORTED: dict[str, str | None] = {"model": None}


def _note_reported_model() -> None:
    """Make Cognee's structured-output call note which model the server says answered.

    It takes nothing and wraps `LLMGateway.acreate_structured_output` once per process; a
    second call finds the wrapper and does nothing. After each answer the wrapper stores the
    model name carried by the raw server response, when there is one. It never changes the
    arguments or the result of the call.
    """
    original = LLMGateway.acreate_structured_output
    if getattr(original, "notes_reported_model", False):
        return

    async def noting(*args: Any, **kwargs: Any) -> Any:
        """Await Cognee's structured-output call and note the model the server reports."""
        result = await original(*args, **kwargs)
        reported = getattr(getattr(result, "_raw_response", None), "model", None)
        if reported is not None:
            _REPORTED["model"] = reported
        return result

    setattr(noting, "notes_reported_model", True)
    setattr(LLMGateway, "acreate_structured_output", staticmethod(noting))


def _iso(moment: datetime | None) -> str | None:
    """Return a moment as an ISO 8601 string, or None when there is no moment."""
    return None if moment is None else moment.isoformat()


class CogneeModel:
    """Cognee's language-model steps, configured only from the language_model settings.

    It takes the validated language_model settings dict, sets Cognee's LLM configuration from
    it (clearing the per-stage and fallback overrides so nothing else can choose a model) and
    wraps Cognee's structured-output call to note which model the server says answered. It
    answers the router's ModelPart protocol: a name, its step names, the achieved model and
    run_step, which points Cognee at the model the router chose for that one call. It never
    makes numbers and never falls back to another model.
    """

    name = "cognee"

    def __init__(self, language_model: dict[str, Any]) -> None:
        """Keep the settings, check them once and start noting which model answers.

        It takes the validated language_model settings dict and gives back the ready part with
        no answer recorded. The settings are checked here by building the Cognee fields once;
        Cognee itself is pointed at a model only inside run_step. It raises ValueError for any
        invalid setting and never touches Cognee's configuration outside a step.
        """
        cognee_llm_fields(language_model)
        self.language_model = dict(language_model)
        self.lock = asyncio.Lock()
        _REPORTED["model"] = None
        _note_reported_model()

    def steps(self) -> tuple[str, ...]:
        """Return the names of the steps this part answers, in the order they are documented.

        It takes nothing and gives back a tuple of step names. Each can be run by run_step with
        plain JSON arguments. It never includes a step that is not implemented.
        """
        return tuple(STEP_NAMES)

    def achieved_model(self) -> str | None:
        """Return the model name the server reported for the latest Cognee call, or None.

        It takes nothing and gives back the name carried by the server's last answer, which is
        None before any call. It never reports the requested model in its place.
        """
        return _REPORTED["model"]

    async def run_step(self, step: str, args: dict[str, Any], model: str) -> dict[str, Any]:
        """Point Cognee at the given model, run one named step and return plain JSON.

        It takes a step name, its JSON arguments and the model the router chose for this call.
        Cognee's configuration is process-wide, so the whole call holds a lock; it is set from
        the same settings with only the model replaced. It gives back the step's output made
        plain JSON, raises ValueError for an unknown step or a missing argument, and never
        runs two steps at once.
        """
        if step not in STEP_NAMES:
            raise ValueError(f"unknown step {step!r}; the steps are {', '.join(STEP_NAMES)}")
        missing = [key for key in STEP_NAMES[step] if key not in args]
        if missing:
            raise ValueError(f"step {step!r} needs: {', '.join(missing)}")
        async with self.lock:
            configuration = get_llm_config()
            for field in STAGE_FIELDS_CLEARED:
                setattr(configuration, field, "")
            for field, value in cognee_llm_fields({**self.language_model, "model": model}).items():
                setattr(configuration, field, value)
            output = await getattr(self, f"step_{step}")(**{key: args[key] for key in STEP_NAMES[step]})
        return json.loads(json.dumps(output, default=str))

    async def step_extract_graph(self, text: str) -> dict[str, Any]:
        """Extract a knowledge graph from a text with Cognee's own content-graph step.

        It takes the text and gives back the nodes and edges the model found, plus the nodes
        whose names are dates and the Timestamp (precision and span) Cognee makes for each,
        exactly as `cognify` would. It never stores anything.
        """
        graph = await extract_content_graph(text, KnowledgeGraph)
        nodes = [node.model_dump() for node in graph.nodes]
        edges = [edge.model_dump() for edge in graph.edges]
        timestamps = []
        for node in graph.nodes:
            timestamp = timestamp_from_text(node.name)
            if timestamp is not None:
                timestamps.append(
                    {
                        "node": node.name,
                        "timestamp_str": timestamp.timestamp_str,
                        "precision": timestamp.precision,
                        "time_at": timestamp.time_at,
                        "time_until": timestamp.time_until,
                    }
                )
        return {"nodes": nodes, "edges": edges, "timestamps": timestamps}

    async def step_extract_events(self, text: str) -> dict[str, Any]:
        """Find the events in a text with the translated event step.

        It takes the text and gives back each event the model found with its name, description,
        location and the current-flow Timestamp nodes made for its start and end. An event whose
        stated time is not a real date is listed under `refused` with the reason, not dropped
        silently. It never stores anything.
        """
        found = await extract_event_graph(text, EventList)
        events = []
        refused = []
        for event in found.events:
            try:
                node = generate_event_datapoint(event)
            except ValueError as error:
                refused.append({"event": event.name, "reason": str(error)})
                continue
            stamps = [stamp for stamp in (node.at, *_interval_ends(node)) if stamp is not None]
            events.append(
                {
                    "name": node.name,
                    "description": node.description,
                    "location": node.location,
                    "kind": "interval" if node.during is not None else "point",
                    "timestamps": [
                        {
                            "timestamp_str": stamp.timestamp_str,
                            "precision": stamp.precision,
                            "time_at": stamp.time_at,
                            "time_until": stamp.time_until,
                        }
                        for stamp in stamps
                    ],
                }
            )
        return {"events": events, "refused": refused}

    async def step_attach_event_entities(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Name the entities around events and attach each by a relationship.

        It takes a list of dicts with a name and an optional description, asks the model once
        for the entities of all of them and attaches them with the translated task. It gives
        back, per event, the relationship, entity and entity type of each attachment, and says
        how many events the model answered for. It never stores anything.
        """
        nodes = [
            generate_event_datapoint(
                FoundEvent(name=event["name"], description=event.get("description"))
            )
            for event in events
        ]
        enriched = await enrich_events(nodes)
        for node, record in zip(nodes, enriched, strict=False):
            add_entities_to_event(node, record)
        attached = []
        for node in nodes:
            pairs = []
            for edge, entities in node.attributes or []:
                for entity in entities:
                    pairs.append(
                        {
                            "relationship": edge.relationship_type,
                            "entity": entity.name,
                            "entity_type": entity.is_a.name if entity.is_a else None,
                        }
                    )
            attached.append({"event": node.name, "attributes": pairs})
        return {"events": attached, "answered_for": len(enriched), "asked_for": len(nodes)}

    async def step_query_window(self, question: str) -> dict[str, Any]:
        """Turn a question into the time window Cognee's temporal search would use.

        It takes the question and gives back the window start and end as ISO strings (None
        for an open side) and the reason there is no window, or None. It never searches.
        """
        start, end, reason = await extract_query_interval(question)
        return {"starts_at": _iso(start), "ends_at": _iso(end), "reason": reason}

    async def step_date_anchors(
        self,
        starts_at: str | None,
        ends_at: str | None,
        chunk_ids: list[str],
        entity_ids: list[str],
    ) -> dict[str, Any]:
        """Find the dates in a window and the chunks and entities anchored to them.

        It takes a window as ISO strings (None for an open side) and the ids to test, asks
        Cognee's graph for the Timestamp nodes inside the window and for which of the given ids
        are attached to one, and gives back both. It never changes the graph.
        """
        graph = await get_graph_engine()
        start = None if starts_at is None else to_epoch_ms(datetime.fromisoformat(starts_at))
        end = None if ends_at is None else to_epoch_ms(datetime.fromisoformat(ends_at))
        in_window = await graph.get_timestamps_in_range(start, end)
        anchors = await graph.get_temporal_anchors(set(chunk_ids), set(entity_ids), start, end)
        return {
            "timestamps": [dict(node) for node in in_window],
            "anchors": {
                "timestamp_ids": sorted(anchors.get("timestamp_ids") or ()),
                "chunk_ids": sorted(anchors.get("chunk_ids") or ()),
                "entity_ids": sorted(anchors.get("entity_ids") or ()),
            },
        }

    async def read_entity_node(self, name: str) -> dict[str, Any]:
        """Read one entity and its neighbourhood from Cognee's graph, as the rewrite reads it.

        It takes an entity name and gives back the node record Cognee's description rewrite
        works from: properties, edges, neighbours and entity types. It raises ValueError when
        no entity has that name and never changes the graph.
        """
        graph = await get_graph_engine()
        for node_id, properties in await get_all_entity_nodes(graph):
            if properties.get("name") == name:
                return await get_entity_neighborhood(node_id, properties, graph)
        raise ValueError(f"No entity named {name!r} is in the graph")

    async def step_rewrite_entity_description(self, name: str) -> dict[str, Any]:
        """Rewrite an entity's description from its neighbourhood with Cognee's own prompt.

        It takes an entity name, reads that entity's node record from Cognee's graph, builds the
        neighbourhood text and sends it with Cognee's consolidation prompt through Cognee's own
        `query_LLM`. It gives back the old description, the new one and the text the model was
        shown, so the new description can be compared with the facts it was given. It never
        writes the new description to the graph.
        """
        node = await self.read_entity_node(name)
        text = build_node_neighborhood_prompt(node)
        result = await query_LLM(text, render_prompt(prompt_name, {}))
        return {
            "name": node["properties"].get("name"),
            "old_description": node["properties"].get("description"),
            "new_description": result.description,
            "shown_to_model": text,
        }


def _interval_ends(node: Event) -> tuple[Any, ...]:
    """Return the start and end Timestamp nodes of an event's interval, or an empty tuple."""
    if node.during is None:
        return ()
    return (node.during.time_from, node.during.time_to)
