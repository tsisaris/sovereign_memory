"""Answer Graphiti's hybrid-search questions with native DuckDB SQL over current rows.

Keyword search scores a BM25 formula (Lucene's default constants, but this module's own
simple tokenizer, so scores and tokenization are not claimed identical to Lucene's) in plain
SQL, so every query reads the text that is stored right now: edits, deletions and group
changes are visible immediately and no extension download or rebuilt index is needed.
Keyword queries are plain words; quoted phrases and Boolean operators are refused, never
reinterpreted as words. Similarity search uses Neo4j's normalized
cosine, ``(1 + cosine) / 2``, over stored embeddings. Graph walks are bounded recursive
SQL. Nothing here calls a model, opens a connection or returns a placeholder result.
"""

import re

from graphiti_core.driver.operations.search_ops import SearchOperations
from graphiti_core.edges import EntityEdge
from graphiti_core.helpers import validate_group_ids, validate_node_labels
from graphiti_core.nodes import CommunityNode, EntityNode, EpisodicNode
from graphiti_core.search.search_filters import ComparisonOperator
from graphiti_core.search.search_utils import MAX_QUERY_LENGTH, rrf

# Lucene's BM25Similarity default constants, used here in this module's own BM25 formula.
LUCENE_BM25_K1 = 1.2
LUCENE_BM25_B = 0.75

# Lowercased runs of Unicode letters and digits: a plain tokenizer, not Lucene's analyzer.
TOKEN_PATTERN = r"[\p{L}\p{N}]+"

# Text fields of Graphiti's four Neo4j fulltext indexes; group_id is enforced as a filter.
NODE_TEXT_FIELDS = ["$.name", "$.summary"]
EDGE_TEXT_FIELDS = ["$.name", "$.fact"]
EPISODE_TEXT_FIELDS = ["$.content", "$.source", "$.source_description"]
COMMUNITY_TEXT_FIELDS = ["$.name"]

_DATE_FIELDS = ("valid_at", "invalid_at", "created_at", "expired_at")
_ENDPOINTS = (
    " JOIN graphiti.graph_node n ON n.uuid = e.source_node_uuid AND n.label = 'Entity'"
    " JOIN graphiti.graph_node m ON m.uuid = e.target_node_uuid AND m.label = 'Entity'"
)


class SqlParameters:
    """Collect named bound values while SQL text is written, so the two never drift apart.

    ``bind`` takes a value and returns its ``$name`` placeholder. ``values`` is the
    dictionary DuckDB receives. It never interpolates a value into SQL text.
    """

    def __init__(self, prefix):
        """Start an empty collection whose placeholder names begin with ``prefix``."""
        self.prefix = prefix
        self.values = {}

    def bind(self, value):
        """Store ``value`` and return the placeholder that refers to it."""
        name = f"{self.prefix}{len(self.values)}"
        self.values[name] = value
        return f"${name}"


def _labels_clause(alias, labels, parameters):
    """Match a node holding any listed label, the meaning of Neo4j's ``n:A|B``."""
    validate_node_labels(labels)
    return (
        f"list_has_any(CAST(json_extract({alias}.properties, '$.labels') AS VARCHAR[]), "
        f"{parameters.bind(list(labels))}::VARCHAR[])"
    )


# Lucene syntax this driver cannot honor: a double-quoted phrase, the uppercase operators
# AND OR NOT, && and ||, and + - ! written directly in front of a term.
_UNSUPPORTED_SYNTAX = (
    (re.compile(r'"[^"]*"'), "a quoted phrase"),
    (re.compile(r"(?<!\w)(?:AND|OR|NOT)(?!\w)"), "a Boolean operator (AND, OR, NOT)"),
    (re.compile(r"&&|\|\|"), "a Boolean operator (&&, ||)"),
    (re.compile(r"(?:^|\s)[+\-!]\S"), "a required, prohibited or negated term (+, -, !)"),
)


def _refuse_unsupported_syntax(query):
    """Raise ValueError when the keyword query uses phrase or Boolean syntax.

    This driver ranks plain keywords only (any matching word counts, like Graphiti's
    sanitized keyword search). Matching those forms as ordinary words would return
    results that contradict what the caller wrote, so they are refused instead.
    Anything else is a plain keyword query and never refused.
    """
    for pattern, name in _UNSUPPORTED_SYNTAX:
        if pattern.search(query):
            raise ValueError(
                f"Keyword search does not support {name}; send plain words or remove the operator"
            )


def _refuse_property_filters(search_filters):
    """Raise rather than silently ignore property filters no Graphiti backend applies."""
    if search_filters.property_filters is not None:
        raise ValueError(
            "SearchFilters.property_filters is not applied by any maintained Graphiti "
            "backend; the DuckDB driver refuses it instead of ignoring the restriction"
        )


def _date_clause(field, groups, parameters):
    """Translate one OR-of-AND date filter list on an entity edge into bound SQL."""
    column = f"CAST(json_extract_string(e.properties, '$.{field}') AS TIMESTAMPTZ)"
    alternatives = []
    for group in groups:
        conditions = []
        for date_filter in group:
            operator = ComparisonOperator(date_filter.comparison_operator)
            if operator in (ComparisonOperator.is_null, ComparisonOperator.is_not_null):
                conditions.append(f"({column} {operator.value})")
            else:
                placeholder = parameters.bind(date_filter.date)
                conditions.append(f"({column} {operator.value} {placeholder})")
        alternatives.append("(" + (" AND ".join(conditions) or "TRUE") + ")")
    return "(" + (" OR ".join(alternatives) or "FALSE") + ")"


def _group_clause(alias, group_ids, parameters):
    """Restrict ``alias`` to the requested groups; ``None`` means every group."""
    if group_ids is None:
        return ""
    validate_group_ids(group_ids)
    return (
        f" AND json_extract_string({alias}.properties, '$.group_id') "
        f"IN (SELECT unnest({parameters.bind(list(group_ids))}::VARCHAR[]))"
    )


def _filter_sql(filters):
    """Join filter clauses from a builder into an ``AND`` suffix for a WHERE clause."""
    clauses, _ = filters
    return "".join(f" AND {clause}" for clause in clauses)


def _merge(parameters, filters):
    """Return the main query's values together with the filter builder's values."""
    return {**parameters.values, **filters[1]}


class DuckDBSearchOps(SearchOperations):
    """Run Graphiti's keyword, vector and graph searches and rerankers on native SQL."""

    def build_node_search_filters(self, search_filters):
        """Return ``(clauses, values)`` restricting entity alias ``n`` by search filters."""
        parameters = SqlParameters("node_filter_")
        clauses = []
        if search_filters is not None:
            _refuse_property_filters(search_filters)
            if search_filters.node_labels is not None:
                clauses.append(_labels_clause("n", search_filters.node_labels, parameters))
        return clauses, parameters.values

    def build_edge_search_filters(self, search_filters):
        """Return ``(clauses, values)`` restricting edge ``e`` and endpoints ``n``/``m``."""
        parameters = SqlParameters("edge_filter_")
        clauses = []
        if search_filters is None:
            return clauses, parameters.values
        _refuse_property_filters(search_filters)
        for values, column in (
            (search_filters.edge_types, "json_extract_string(e.properties, '$.name')"),
            (search_filters.edge_uuids, "e.uuid"),
            (search_filters.edge_source_node_uuids, "e.source_node_uuid"),
            (search_filters.edge_target_node_uuids, "e.target_node_uuid"),
        ):
            if values is not None:
                placeholder = parameters.bind(list(values))
                clauses.append(f"{column} IN (SELECT unnest({placeholder}::VARCHAR[]))")
        if search_filters.node_labels is not None:
            clauses.append(_labels_clause("n", search_filters.node_labels, parameters))
            clauses.append(_labels_clause("m", search_filters.node_labels, parameters))
        for field in _DATE_FIELDS:
            groups = getattr(search_filters, field)
            if groups is not None:
                clauses.append(_date_clause(field, groups, parameters))
        return clauses, parameters.values

    def build_fulltext_query(self, query, group_ids=None, max_query_length=MAX_QUERY_LENGTH):
        """Return the lowercased keyword query, or "" when it exceeds Graphiti's term budget.

        Raises ValueError for a quoted phrase or Boolean operator (see
        ``_refuse_unsupported_syntax``).

        The budget is Graphiti's own rule: query words plus requested groups must stay
        below ``max_query_length``. An empty result means the search is not run.
        """
        validate_group_ids(group_ids)
        _refuse_unsupported_syntax(query)
        if len(query.split(" ")) + len(group_ids or []) >= max_query_length:
            return ""
        return query.lower()

    async def _fulltext(
        self,
        executor,
        table,
        label,
        alias,
        fields,
        query,
        group_ids,
        limit,
        filters=([], {}),
        joins="",
    ):
        """Score current rows of one label with BM25 and return their stored properties.

        Statistics come from the rows visible in the requested groups. A field's score
        uses this BM25 form: ``idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))``,
        summed over matching terms and fields like a multi-field OR query.
        """
        keywords = self.build_fulltext_query(query, group_ids)
        if keywords == "":
            return []
        p = SqlParameters("p")
        k1, b = p.bind(LUCENE_BM25_K1), p.bind(LUCENE_BM25_B)
        sql = (
            f"WITH docs AS (SELECT uuid, properties FROM graphiti.{table} AS d "
            f"WHERE label = {p.bind(label)}{_group_clause('d', group_ids, p)}), "
            "fields AS (SELECT d.uuid, f.field, regexp_extract_all(lower(coalesce("
            f"json_extract_string(d.properties, f.field), '')), '{TOKEN_PATTERN}') AS toks "
            f"FROM docs d CROSS JOIN (SELECT unnest({p.bind(fields)}::VARCHAR[]) AS field) f), "
            "lengths AS (SELECT uuid, field, len(toks) AS dl FROM fields WHERE len(toks) > 0), "
            "stats AS (SELECT field, count(*) AS n, avg(dl) AS avgdl FROM lengths GROUP BY field), "
            "terms AS (SELECT DISTINCT unnest(regexp_extract_all("
            f"{p.bind(keywords)}, '{TOKEN_PATTERN}')) AS term), "
            "postings AS (SELECT uuid, field, tok AS term, count(*) AS tf FROM "
            "(SELECT uuid, field, unnest(toks) AS tok FROM fields) "
            "WHERE tok IN (SELECT term FROM terms) GROUP BY uuid, field, tok), "
            "frequencies AS (SELECT field, term, count(*) AS df FROM postings GROUP BY field, term), "
            "scores AS (SELECT p.uuid, sum(ln(1 + (s.n - f.df + 0.5) / (f.df + 0.5)) * p.tf * "
            f"({k1} + 1) / (p.tf + {k1} * (1 - {b} + {b} * l.dl / s.avgdl))) AS score "
            "FROM postings p JOIN lengths l ON l.uuid = p.uuid AND l.field = p.field "
            "JOIN stats s ON s.field = p.field "
            "JOIN frequencies f ON f.field = p.field AND f.term = p.term GROUP BY p.uuid) "
            f"SELECT {alias}.properties FROM scores sc "
            f"JOIN graphiti.{table} {alias} ON {alias}.uuid = sc.uuid{joins} "
            f"WHERE {alias}.label = {p.bind(label)}{_filter_sql(filters)} "
            f"ORDER BY sc.score DESC, {alias}.uuid LIMIT {p.bind(limit)}"
        )
        records, _, _ = await executor.execute_query(sql, parameters=_merge(p, filters))
        return [record["properties"] for record in records]

    async def _similarity(
        self,
        executor,
        table,
        label,
        alias,
        embedding,
        search_vector,
        group_ids,
        limit,
        min_score,
        extra="",
        filters=([], {}),
        joins="",
    ):
        """Rank current rows by normalized cosine above ``min_score`` and return properties.

        Rows without an embedding never match. An embedding of another dimension raises
        DuckDB's error instead of being skipped.
        """
        p = SqlParameters("p")
        sql = (
            f"SELECT {alias}.properties FROM (SELECT *, (1 + list_cosine_similarity("
            f"CAST(json_extract(d.properties, '$.{embedding}') AS FLOAT[]), "
            f"{p.bind(list(search_vector))}::FLOAT[])) / 2 AS score "
            f"FROM graphiti.{table} AS d WHERE label = {p.bind(label)}"
            f"{_group_clause('d', group_ids, p)}) {alias}{joins} "
            f"WHERE {alias}.score > {p.bind(min_score)}{extra}{_filter_sql(filters)} "
            f"ORDER BY {alias}.score DESC, {alias}.uuid LIMIT {p.bind(limit)}"
        )
        records, _, _ = await executor.execute_query(sql, parameters=_merge(p, filters))
        return [record["properties"] for record in records]

    async def node_fulltext_search(self, executor, query, search_filter, group_ids=None, limit=10):
        """Return entities whose name or summary best match the keywords, best first."""
        rows = await self._fulltext(
            executor,
            "graph_node",
            "Entity",
            "n",
            NODE_TEXT_FIELDS,
            query,
            group_ids,
            limit,
            self.build_node_search_filters(search_filter),
        )
        return [EntityNode.model_validate_json(row) for row in rows]

    async def node_similarity_search(
        self, executor, search_vector, search_filter, group_ids=None, limit=10, min_score=0.6
    ):
        """Return entities whose name embedding is most similar to ``search_vector``."""
        rows = await self._similarity(
            executor,
            "graph_node",
            "Entity",
            "n",
            "name_embedding",
            search_vector,
            group_ids,
            limit,
            min_score,
            filters=self.build_node_search_filters(search_filter),
        )
        return [EntityNode.model_validate_json(row) for row in rows]

    async def node_bfs_search(
        self, executor, origin_uuids, search_filter, max_depth, group_ids=None, limit=10
    ):
        """Return entities reachable from the origins along outgoing RELATES_TO or MENTIONS.

        Only nodes 1..``max_depth`` hops away in the origin's own group qualify, nearest
        first. No origins or a depth below one reach nothing.
        """
        if not origin_uuids or max_depth < 1:
            return []
        filters = self.build_node_search_filters(search_filter)
        p = SqlParameters("p")
        sql = (
            "WITH RECURSIVE reach(origin_uuid, node_uuid, depth) AS ("
            f"SELECT o, o, 0 FROM (SELECT unnest({p.bind(list(origin_uuids))}::VARCHAR[]) AS o) "
            "UNION SELECT r.origin_uuid, g.target_node_uuid, r.depth + 1 FROM reach r "
            "JOIN graphiti.graph_edge g ON g.source_node_uuid = r.node_uuid "
            f"WHERE g.label IN ('RELATES_TO', 'MENTIONS') AND r.depth < {p.bind(max_depth)}), "
            "hits AS (SELECT n.uuid, min(r.depth) AS depth FROM reach r "
            "JOIN graphiti.graph_node o ON o.uuid = r.origin_uuid "
            "JOIN graphiti.graph_node n ON n.uuid = r.node_uuid "
            "WHERE r.depth >= 1 AND n.label = 'Entity' AND "
            "json_extract_string(n.properties, '$.group_id') = "
            "json_extract_string(o.properties, '$.group_id')"
            f"{_group_clause('n', group_ids, p)}{_group_clause('o', group_ids, p)}"
            f"{_filter_sql(filters)} GROUP BY n.uuid) "
            "SELECT n.properties FROM hits h JOIN graphiti.graph_node n ON n.uuid = h.uuid "
            f"ORDER BY h.depth, n.uuid LIMIT {p.bind(limit)}"
        )
        records, _, _ = await executor.execute_query(sql, parameters=_merge(p, filters))
        return [EntityNode.model_validate_json(record["properties"]) for record in records]

    async def edge_fulltext_search(self, executor, query, search_filter, group_ids=None, limit=10):
        """Return entity relationships whose name or fact best match the keywords."""
        rows = await self._fulltext(
            executor,
            "graph_edge",
            "RELATES_TO",
            "e",
            EDGE_TEXT_FIELDS,
            query,
            group_ids,
            limit,
            self.build_edge_search_filters(search_filter),
            _ENDPOINTS,
        )
        return [EntityEdge.model_validate_json(row) for row in rows]

    async def edge_similarity_search(
        self,
        executor,
        search_vector,
        source_node_uuid,
        target_node_uuid,
        search_filter,
        group_ids=None,
        limit=10,
        min_score=0.6,
    ):
        """Return relationships whose fact embedding is most similar, within any endpoints.

        A supplied source or target UUID always restricts the result, whether or not
        groups are also given.
        """
        p = SqlParameters("endpoint_")
        extra = ""
        if source_node_uuid is not None:
            extra += f" AND e.source_node_uuid = {p.bind(source_node_uuid)}"
        if target_node_uuid is not None:
            extra += f" AND e.target_node_uuid = {p.bind(target_node_uuid)}"
        clauses, values = self.build_edge_search_filters(search_filter)
        rows = await self._similarity(
            executor,
            "graph_edge",
            "RELATES_TO",
            "e",
            "fact_embedding",
            search_vector,
            group_ids,
            limit,
            min_score,
            extra,
            (clauses, {**values, **p.values}),
            _ENDPOINTS,
        )
        return [EntityEdge.model_validate_json(row) for row in rows]

    async def edge_bfs_search(
        self, executor, origin_uuids, max_depth, search_filter, group_ids=None, limit=10
    ):
        """Return RELATES_TO edges on outgoing paths of at most ``max_depth`` hops.

        Paths follow RELATES_TO or MENTIONS from the origins, so an episode origin reaches
        the facts about entities it mentions. Nearest edges come first.
        """
        if not origin_uuids or max_depth < 1:
            return []
        filters = self.build_edge_search_filters(search_filter)
        p = SqlParameters("p")
        sql = (
            "WITH RECURSIVE reach(node_uuid, depth) AS ("
            f"SELECT unnest({p.bind(list(origin_uuids))}::VARCHAR[]), 0 "
            "UNION SELECT g.target_node_uuid, r.depth + 1 FROM reach r "
            "JOIN graphiti.graph_edge g ON g.source_node_uuid = r.node_uuid "
            "WHERE g.label IN ('RELATES_TO', 'MENTIONS') "
            f"AND r.depth + 1 < {p.bind(max_depth)}), "
            "nearest AS (SELECT node_uuid, min(depth) AS depth FROM reach GROUP BY node_uuid) "
            "SELECT e.properties FROM graphiti.graph_edge e "
            f"JOIN nearest r ON r.node_uuid = e.source_node_uuid{_ENDPOINTS} "
            f"WHERE e.label = 'RELATES_TO'{_group_clause('e', group_ids, p)}{_filter_sql(filters)} "
            f"ORDER BY r.depth, e.uuid LIMIT {p.bind(limit)}"
        )
        records, _, _ = await executor.execute_query(sql, parameters=_merge(p, filters))
        return [EntityEdge.model_validate_json(record["properties"]) for record in records]

    async def episode_fulltext_search(
        self, executor, query, search_filter, group_ids=None, limit=10
    ):
        """Return episodes whose content, source or description best match the keywords.

        As in every maintained backend, ``search_filter`` does not apply to episodes.
        """
        rows = await self._fulltext(
            executor, "graph_node", "Episodic", "x", EPISODE_TEXT_FIELDS, query, group_ids, limit
        )
        return [EpisodicNode.model_validate_json(row) for row in rows]

    async def community_fulltext_search(self, executor, query, group_ids=None, limit=10):
        """Return communities whose name best matches the keywords."""
        rows = await self._fulltext(
            executor,
            "graph_node",
            "Community",
            "x",
            COMMUNITY_TEXT_FIELDS,
            query,
            group_ids,
            limit,
        )
        return [CommunityNode.model_validate_json(row) for row in rows]

    async def community_similarity_search(
        self, executor, search_vector, group_ids=None, limit=10, min_score=0.6
    ):
        """Return communities whose name embedding is most similar to ``search_vector``."""
        rows = await self._similarity(
            executor,
            "graph_node",
            "Community",
            "x",
            "name_embedding",
            search_vector,
            group_ids,
            limit,
            min_score,
        )
        return [CommunityNode.model_validate_json(row) for row in rows]

    async def _edges_near(self, executor, edge, endpoint_sql, search_filter, min_score, limit):
        """Return entity edges in ``edge``'s group, matching ``endpoint_sql``, ranked by
        normalized cosine to its fact embedding. ``endpoint_sql`` uses ``$src``/``$tgt``.
        """
        if edge.fact_embedding is None:
            return []
        clauses, values = self.build_edge_search_filters(search_filter)
        endpoints = {"src": edge.source_node_uuid, "tgt": edge.target_node_uuid}
        rows = await self._similarity(
            executor,
            "graph_edge",
            "RELATES_TO",
            "e",
            "fact_embedding",
            edge.fact_embedding,
            [edge.group_id],
            limit,
            min_score,
            f" AND ({endpoint_sql})",
            (clauses, {**values, **endpoints}),
            _ENDPOINTS,
        )
        return [EntityEdge.model_validate_json(row) for row in rows]

    async def get_relevant_nodes(self, executor, nodes, search_filter, min_score, limit):
        """Return, per input node, similar entities then extra keyword matches on its name.

        As in the maintained query, all lookups use the first node's group; vector hits
        above ``min_score`` come first, then keyword hits not already present, each part
        capped at ``limit``. Search filters apply to both parts.
        """
        if not nodes:
            return []
        group_ids = [nodes[0].group_id]
        results = []
        for node in nodes:
            similar = []
            if node.name_embedding is not None:
                similar = await self.node_similarity_search(
                    executor, node.name_embedding, search_filter, group_ids, limit, min_score
                )
            keyword = await self.node_fulltext_search(
                executor, node.name, search_filter, group_ids, limit
            )
            seen = {match.uuid for match in similar}
            results.append(similar + [match for match in keyword if match.uuid not in seen])
        return results

    async def get_relevant_edges(self, executor, edges, search_filter, min_score, limit):
        """Return, per input edge, stored edges between the same two entities either way."""
        return [
            await self._edges_near(
                executor,
                edge,
                "(e.source_node_uuid = $src AND e.target_node_uuid = $tgt) OR "
                "(e.source_node_uuid = $tgt AND e.target_node_uuid = $src)",
                search_filter,
                min_score,
                limit,
            )
            for edge in edges
        ]

    async def get_edge_invalidation_candidates(
        self, executor, edges, search_filter, min_score, limit
    ):
        """Return, per input edge, similar stored edges touching either of its entities."""
        return [
            await self._edges_near(
                executor,
                edge,
                "e.source_node_uuid IN ($src, $tgt) OR e.target_node_uuid IN ($src, $tgt)",
                search_filter,
                min_score,
                limit,
            )
            for edge in edges
        ]

    async def existing_duplicate_pairs(self, executor, pairs):
        """Return the ``(source_uuid, target_uuid)`` pairs already joined by IS_DUPLICATE_OF."""
        records, _, _ = await executor.execute_query(
            "SELECT DISTINCT e.source_node_uuid AS source_uuid, e.target_node_uuid AS target_uuid "
            "FROM graphiti.graph_edge e "
            "JOIN (SELECT unnest(?::VARCHAR[]) AS s, unnest(?::VARCHAR[]) AS t) p "
            "ON e.source_node_uuid = p.s AND e.target_node_uuid = p.t "
            f"{_ENDPOINTS} "
            "WHERE e.label = 'RELATES_TO' AND json_extract_string(e.properties, '$.name') = "
            "'IS_DUPLICATE_OF'",
            parameters=[[pair[0] for pair in pairs], [pair[1] for pair in pairs]],
        )
        return {(record["source_uuid"], record["target_uuid"]) for record in records}

    async def rank_by_distance(self, executor, node_uuids, center_node_uuid, min_score=0):
        """Return ``(uuids, scores)`` with Graphiti's node-distance reranking rules.

        Direct RELATES_TO neighbours of the centre score 1, others 0, and the centre
        itself, when listed, comes first with score 10. Scores below ``min_score`` drop.
        """
        others = [uuid for uuid in node_uuids if uuid != center_node_uuid]
        records, _, _ = await executor.execute_query(
            "SELECT DISTINCT CASE WHEN source_node_uuid = ? THEN target_node_uuid "
            "ELSE source_node_uuid END AS uuid FROM graphiti.graph_edge "
            "WHERE label = 'RELATES_TO' AND ((source_node_uuid = ? AND target_node_uuid "
            "IN (SELECT unnest(?::VARCHAR[]))) OR (target_node_uuid = ? AND source_node_uuid "
            "IN (SELECT unnest(?::VARCHAR[]))))",
            parameters=[center_node_uuid, center_node_uuid, others, center_node_uuid, others],
        )
        neighbours = {record["uuid"] for record in records}
        distances = {uuid: 1.0 if uuid in neighbours else float("inf") for uuid in others}
        ordered = sorted(others, key=lambda uuid: distances[uuid])
        if center_node_uuid in node_uuids:
            distances[center_node_uuid] = 0.1
            ordered = [center_node_uuid, *ordered]
        ranked = [(uuid, 1 / distances[uuid]) for uuid in ordered]
        kept = [(uuid, score) for uuid, score in ranked if score >= min_score]
        return [uuid for uuid, _ in kept], [score for _, score in kept]

    async def rank_by_mentions(self, executor, node_uuids, min_score=0):
        """Return ``(uuids, counts)`` fused by RRF, then ordered by episode mention count."""
        ordered, _ = rrf(node_uuids)
        records, _, _ = await executor.execute_query(
            "SELECT e.target_node_uuid AS uuid, count(*) AS mentions FROM graphiti.graph_edge e "
            "JOIN graphiti.graph_node ep ON ep.uuid = e.source_node_uuid AND ep.label = 'Episodic' "
            "WHERE e.label = 'MENTIONS' AND e.target_node_uuid IN (SELECT unnest(?::VARCHAR[])) "
            "GROUP BY e.target_node_uuid",
            parameters=[ordered],
        )
        counts = {record["uuid"]: record["mentions"] for record in records}
        ordered.sort(key=lambda uuid: counts.get(uuid, 0), reverse=True)
        kept = [uuid for uuid in ordered if counts.get(uuid, 0) >= min_score]
        return kept, [counts.get(uuid, 0) for uuid in kept]

    async def _ordered_entities(self, executor, uuids):
        """Load stored entities in the given UUID order, omitting UUIDs not stored."""
        stored = await executor.entity_node_ops.get_by_uuids(executor, uuids)
        by_uuid = {node.uuid: node for node in stored}
        return [by_uuid[uuid] for uuid in uuids if uuid in by_uuid]

    async def node_distance_reranker(self, executor, node_uuids, center_node_uuid, min_score=0):
        """Return the stored entities in node-distance order (see ``rank_by_distance``)."""
        uuids, _ = await self.rank_by_distance(executor, node_uuids, center_node_uuid, min_score)
        return await self._ordered_entities(executor, uuids)

    async def episode_mentions_reranker(self, executor, node_uuids, min_score=0):
        """Return the stored entities in episode-mention order (see ``rank_by_mentions``)."""
        uuids, _ = await self.rank_by_mentions(executor, node_uuids, min_score)
        return await self._ordered_entities(executor, uuids)

    async def community_embeddings(self, executor, communities):
        """Return ``{uuid: name_embedding}`` for listed communities that store one."""
        stored = await executor.community_node_ops.get_by_uuids(
            executor, [community.uuid for community in communities]
        )
        return {
            node.uuid: node.name_embedding for node in stored if node.name_embedding is not None
        }
