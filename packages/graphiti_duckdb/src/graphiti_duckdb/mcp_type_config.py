"""Helpers that turn server settings into graphiti-core arguments.

Ported from Graphiti's mcp_server/src/utils/type_config.py. Goes in: settings values and
ISO-8601 text. Comes out: dates, type mappings and search filters in the shape graphiti-core
expects. It does no input or output of its own and keeps no state.
"""

from datetime import datetime, timezone

from graphiti_core.search.search_filters import ComparisonOperator, DateFilter, SearchFilters
from pydantic import BaseModel, create_model

from .mcp_config import EdgeTypeConfig, EdgeTypeMapEntry, EntityTypeConfig
from .mcp_type_registry import EDGE_TYPES, ENTITY_TYPES


def parse_reference_time(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string into a timezone-aware UTC datetime.

    Goes in: the text or None. Comes out: the datetime or None. A trailing Z means UTC and a
    value with no timezone is taken as UTC; an unreadable text raises ValueError.
    """
    if value is None:
        return None
    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def coerce_group_ids(group_ids: str | list[str] | None) -> list[str] | None:
    """Turn a single group id or a list of them into a list, or None when none was given.

    Goes in: a text, a list or None. Comes out: a list or None. A blank text counts as not
    given, so a destructive call never acts on the group named by an empty string.
    """
    if isinstance(group_ids, str):
        return [group_ids] if group_ids else None
    return group_ids


def _doc_only_model(name: str, description: str) -> type[BaseModel]:
    """Build a model with no fields whose docstring is the description.

    Goes in: the type name and its description. Comes out: the model class. The description is
    what the extraction model reads; the class never holds data.
    """
    model = create_model(name)
    model.__doc__ = description
    return model


def build_entity_types(
    entity_type_configs: list[EntityTypeConfig] | None,
) -> dict[str, type[BaseModel]] | None:
    """Build the entity-type mapping for ``Graphiti.add_episode``.

    Goes in: the configured entries. Comes out: name to model, preferring the registered rich
    model and falling back to a description-only one; None when nothing is configured.
    """
    if not entity_type_configs:
        return None
    result: dict[str, type[BaseModel]] = {}
    for config in entity_type_configs:
        registered = ENTITY_TYPES.get(config.name)
        result[config.name] = (
            registered
            if registered is not None
            else _doc_only_model(config.name, config.description)
        )
    return result


def build_edge_types(
    edge_type_configs: list[EdgeTypeConfig] | None,
) -> dict[str, type[BaseModel]] | None:
    """Build the edge-type mapping for ``Graphiti.add_episode``.

    Goes in: the configured entries. Comes out: name to model, in the same way as entity
    types; None when nothing is configured.
    """
    if not edge_type_configs:
        return None
    result: dict[str, type[BaseModel]] = {}
    for config in edge_type_configs:
        registered = EDGE_TYPES.get(config.name)
        result[config.name] = (
            registered
            if registered is not None
            else _doc_only_model(config.name, config.description)
        )
    return result


def build_edge_type_map(
    edge_type_map_entries: list[EdgeTypeMapEntry] | None,
) -> dict[tuple[str, str], list[str]] | None:
    """Build the ``{(source, target): [edge types]}`` mapping graphiti-core expects.

    Goes in: the configured entries. Comes out: the mapping, or None when nothing is configured.
    """
    if not edge_type_map_entries:
        return None
    return {(entry.source, entry.target): list(entry.edge_types) for entry in edge_type_map_entries}


def _date_range_or_group(
    after: datetime | None, before: datetime | None
) -> list[list[DateFilter]] | None:
    """Build one date filter for an optional lower and upper bound.

    Goes in: the two bounds. Comes out: one group of conditions joined by AND, or None when
    neither bound was given.
    """
    conditions: list[DateFilter] = []
    if after is not None:
        conditions.append(
            DateFilter(date=after, comparison_operator=ComparisonOperator.greater_than_equal)
        )
    if before is not None:
        conditions.append(
            DateFilter(date=before, comparison_operator=ComparisonOperator.less_than_equal)
        )
    if not conditions:
        return None
    return [conditions]


def build_fact_search_filters(
    edge_types: list[str] | None = None,
    valid_at_after: str | None = None,
    valid_at_before: str | None = None,
    invalid_at_after: str | None = None,
    invalid_at_before: str | None = None,
) -> SearchFilters | None:
    """Build the filter for a fact search from optional edge types and date ranges.

    Goes in: edge type names and four ISO-8601 bounds. Comes out: the filter, or None when
    none was asked for. A bad timestamp raises ValueError.
    """
    valid_at = _date_range_or_group(
        parse_reference_time(valid_at_after), parse_reference_time(valid_at_before)
    )
    invalid_at = _date_range_or_group(
        parse_reference_time(invalid_at_after), parse_reference_time(invalid_at_before)
    )
    if not edge_types and valid_at is None and invalid_at is None:
        return None
    return SearchFilters(edge_types=edge_types or None, valid_at=valid_at, invalid_at=invalid_at)
