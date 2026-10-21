"""
Copyright 2024, Zep Software, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from graphiti_core.driver.driver import GraphProvider
from graphiti_core.helpers import validate_node_labels


class ComparisonOperator(Enum):
    """The comparison operators a search filter can use: equals, not equals, greater, less, their or-equal forms, is null and is not null.

    It names operators only.
    """

    equals = "="
    not_equals = "<>"
    greater_than = ">"
    less_than = "<"
    greater_than_equal = ">="
    less_than_equal = "<="
    is_null = "IS NULL"
    is_not_null = "IS NOT NULL"


class DateFilter(BaseModel):
    """A filter on a date: an optional date and the comparison operator to apply.

    It holds values only.
    """

    date: datetime | None = Field(default=None, description="A datetime to filter on")
    comparison_operator: ComparisonOperator = Field(
        description="Comparison operator for date filter"
    )


class PropertyFilter(BaseModel):
    """A filter on a property: the property name, an optional value and the comparison operator to apply.

    It holds values only.
    """

    property_name: str = Field(description="Property name")
    property_value: str | int | float | None = Field(
        default=None, description="Value you want to match on for the property"
    )
    comparison_operator: ComparisonOperator = Field(
        description="Comparison operator for the property"
    )


class SearchFilters(BaseModel):
    """The filters a search can apply: node labels, edge types and date filters on valid, invalid, created and expired times, plus property filters.

    It holds values only; node labels are checked against a safe pattern when set.
    """

    node_labels: list[str] | None = Field(
        default=None, description="List of node labels to filter on"
    )
    edge_types: list[str] | None = Field(
        default=None, description="List of edge types to filter on"
    )
    valid_at: list[list[DateFilter]] | None = Field(default=None)
    invalid_at: list[list[DateFilter]] | None = Field(default=None)
    created_at: list[list[DateFilter]] | None = Field(default=None)
    expired_at: list[list[DateFilter]] | None = Field(default=None)
    edge_uuids: list[str] | None = Field(default=None)
    edge_source_node_uuids: list[str] | None = Field(
        default=None, description="Restrict edge results to these source node UUIDs"
    )
    edge_target_node_uuids: list[str] | None = Field(
        default=None, description="Restrict edge results to these target node UUIDs"
    )
    property_filters: list[PropertyFilter] | None = Field(default=None)

    @field_validator("node_labels")
    @classmethod
    def validate_node_label_filters(cls, value: list[str] | None) -> list[str] | None:
        """Check that the node labels given as a filter are safe label names.

        Goes in: a list of labels or None; comes out: the same value. It raises if a label is not
        allowed and never changes the value.
        """
        validate_node_labels(value)
        return value


def cypher_to_opensearch_operator(op: ComparisonOperator) -> str:
    """Translate a comparison operator into the word OpenSearch uses (gt, lt, gte, lte).

    Goes in: a comparison operator; comes out: the OpenSearch word, or the operator's own text when
    there is no special word. It touches nothing.
    """
    mapping = {
        ComparisonOperator.greater_than: "gt",
        ComparisonOperator.less_than: "lt",
        ComparisonOperator.greater_than_equal: "gte",
        ComparisonOperator.less_than_equal: "lte",
    }
    return mapping.get(op, op.value)


def node_search_filter_query_constructor(
    filters: SearchFilters,
    provider: GraphProvider,
) -> tuple[list[str], dict[str, Any]]:
    """Build the query conditions and parameters that restrict a node search to the given filters.

    Goes in: the search filters and the graph provider; comes out: a list of condition strings and a
    dict of parameters. It checks labels before use and never runs a query.
    """
    filter_queries: list[str] = []
    filter_params: dict[str, Any] = {}

    if filters.node_labels is not None:
        # Defense-in-depth for model_construct()/other validation bypasses.
        validate_node_labels(filters.node_labels)
        if provider == GraphProvider.KUZU:
            node_label_filter = "list_has_all(n.labels, $labels)"
            filter_params["labels"] = filters.node_labels
        else:
            node_labels = "|".join(filters.node_labels)
            node_label_filter = "n:" + node_labels
        filter_queries.append(node_label_filter)

    return filter_queries, filter_params


def date_filter_query_constructor(
    value_name: str, param_name: str, operator: ComparisonOperator
) -> str:
    """Build one date condition of a query.

    Goes in: the value name, the parameter name and a comparison operator; comes out: the condition
    text, which has no parameter for is-null tests. It never runs a query.
    """
    query = "(" + value_name + " "

    if operator == ComparisonOperator.is_null or operator == ComparisonOperator.is_not_null:
        query += operator.value + ")"
    else:
        query += operator.value + " " + param_name + ")"

    return query


def edge_search_filter_query_constructor(
    filters: SearchFilters,
    provider: GraphProvider,
) -> tuple[list[str], dict[str, Any]]:
    """Build the query conditions and parameters that restrict an edge search to the given filters.

    Goes in: the search filters and the graph provider; comes out: a list of condition strings and a
    dict of parameters. It never runs a query.
    """
    filter_queries: list[str] = []
    filter_params: dict[str, Any] = {}

    if filters.edge_types is not None:
        edge_types = filters.edge_types
        filter_queries.append("e.name in $edge_types")
        filter_params["edge_types"] = edge_types

    if filters.edge_uuids is not None:
        filter_queries.append("e.uuid in $edge_uuids")
        filter_params["edge_uuids"] = filters.edge_uuids

    # Edge endpoint filters: every edge query binds n as the source and m as
    # the target endpoint. Backends that can push these down pre-rank an
    # endpoint-scoped candidate set instead of scanning the graph.
    if filters.edge_source_node_uuids is not None:
        filter_queries.append("n.uuid IN $edge_source_node_uuids")
        filter_params["edge_source_node_uuids"] = filters.edge_source_node_uuids

    if filters.edge_target_node_uuids is not None:
        filter_queries.append("m.uuid IN $edge_target_node_uuids")
        filter_params["edge_target_node_uuids"] = filters.edge_target_node_uuids

    if filters.node_labels is not None:
        # Defense-in-depth for model_construct()/other validation bypasses.
        validate_node_labels(filters.node_labels)
        if provider == GraphProvider.KUZU:
            node_label_filter = (
                "list_has_all(n.labels, $labels) AND list_has_all(m.labels, $labels)"
            )
            filter_params["labels"] = filters.node_labels
        else:
            node_labels = "|".join(filters.node_labels)
            node_label_filter = "n:" + node_labels + " AND m:" + node_labels
        filter_queries.append(node_label_filter)

    if filters.valid_at is not None:
        valid_at_filter = "("
        for i, or_list in enumerate(filters.valid_at):
            for j, date_filter in enumerate(or_list):
                if date_filter.comparison_operator not in [
                    ComparisonOperator.is_null,
                    ComparisonOperator.is_not_null,
                ]:
                    filter_params["valid_at_" + str(j)] = date_filter.date

            and_filters = [
                date_filter_query_constructor(
                    "e.valid_at", f"$valid_at_{j}", date_filter.comparison_operator
                )
                for j, date_filter in enumerate(or_list)
            ]
            and_filter_query = ""
            for j, and_filter in enumerate(and_filters):
                and_filter_query += and_filter
                if j != len(and_filters) - 1:
                    and_filter_query += " AND "

            valid_at_filter += and_filter_query

            if i == len(filters.valid_at) - 1:
                valid_at_filter += ")"
            else:
                valid_at_filter += " OR "

        filter_queries.append(valid_at_filter)

    if filters.invalid_at is not None:
        invalid_at_filter = "("
        for i, or_list in enumerate(filters.invalid_at):
            for j, date_filter in enumerate(or_list):
                if date_filter.comparison_operator not in [
                    ComparisonOperator.is_null,
                    ComparisonOperator.is_not_null,
                ]:
                    filter_params["invalid_at_" + str(j)] = date_filter.date

            and_filters = [
                date_filter_query_constructor(
                    "e.invalid_at", f"$invalid_at_{j}", date_filter.comparison_operator
                )
                for j, date_filter in enumerate(or_list)
            ]
            and_filter_query = ""
            for j, and_filter in enumerate(and_filters):
                and_filter_query += and_filter
                if j != len(and_filters) - 1:
                    and_filter_query += " AND "

            invalid_at_filter += and_filter_query

            if i == len(filters.invalid_at) - 1:
                invalid_at_filter += ")"
            else:
                invalid_at_filter += " OR "

        filter_queries.append(invalid_at_filter)

    if filters.created_at is not None:
        created_at_filter = "("
        for i, or_list in enumerate(filters.created_at):
            for j, date_filter in enumerate(or_list):
                if date_filter.comparison_operator not in [
                    ComparisonOperator.is_null,
                    ComparisonOperator.is_not_null,
                ]:
                    filter_params["created_at_" + str(j)] = date_filter.date

            and_filters = [
                date_filter_query_constructor(
                    "e.created_at", f"$created_at_{j}", date_filter.comparison_operator
                )
                for j, date_filter in enumerate(or_list)
            ]
            and_filter_query = ""
            for j, and_filter in enumerate(and_filters):
                and_filter_query += and_filter
                if j != len(and_filters) - 1:
                    and_filter_query += " AND "

            created_at_filter += and_filter_query

            if i == len(filters.created_at) - 1:
                created_at_filter += ")"
            else:
                created_at_filter += " OR "

        filter_queries.append(created_at_filter)

    if filters.expired_at is not None:
        expired_at_filter = "("
        for i, or_list in enumerate(filters.expired_at):
            for j, date_filter in enumerate(or_list):
                if date_filter.comparison_operator not in [
                    ComparisonOperator.is_null,
                    ComparisonOperator.is_not_null,
                ]:
                    filter_params["expired_at_" + str(j)] = date_filter.date

            and_filters = [
                date_filter_query_constructor(
                    "e.expired_at", f"$expired_at_{j}", date_filter.comparison_operator
                )
                for j, date_filter in enumerate(or_list)
            ]
            and_filter_query = ""
            for j, and_filter in enumerate(and_filters):
                and_filter_query += and_filter
                if j != len(and_filters) - 1:
                    and_filter_query += " AND "

            expired_at_filter += and_filter_query

            if i == len(filters.expired_at) - 1:
                expired_at_filter += ")"
            else:
                expired_at_filter += " OR "

        filter_queries.append(expired_at_filter)

    return filter_queries, filter_params
