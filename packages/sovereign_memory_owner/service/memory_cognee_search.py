"""Cognee's other search types, answered from the memory the asker may see.

It is for the search types of Cognee that are not plain chunk ranking: CHUNKS_LEXICAL (Cognee's own
BM25 chunk retriever, no model and no numbers), SUMMARIES (the Zep summaries ranked by meaning),
RAG_COMPLETION and GRAPH_COMPLETION (an answer written by the language model from the best chunks, or
from the dated facts, episodes and summaries, with Cognee's own completion prompts), TEMPORAL (the time
window Cognee's own interval step reads out of the question, then the search narrowed to it) and CHUNKS
(the ordinary per-model ranking). It takes the asker, the search type, the question, the limit, an
optional filter and which opinion (main or second model) writes any answer. It gives back the type, the
hits or the answer with the context it was written from, and the model that really answered. It never
reads what the asker may not see, never writes, and refuses a search type Cognee has that has no data
here (its code, Cypher and natural-language-graph types need a Cognee graph this service does not keep)
by naming the reason, never by answering something else.

| Prior-art row | Capability | Tools looked at | Verdict |
| memory_cognee_search.py | Cognee's search types over DuckDB memory | cognee.search (needs a Cognee dataset, user and graph file this service does not keep), Cognee BM25ChunksRetriever (reused for its scorer), Cognee generate_completion (reused), Graphiti search recipes (already the temporal tier), Letta archival_memory_search (already the episodic tier) | Cognee's retrievers reused where their data can be handed to them; the composition is the only new code |
"""

from datetime import datetime, timezone
from functools import cache
from heapq import nlargest
from typing import Any

from service.memory_event_ranking import permitted_producers
from service.memory_model_parts import OPINIONS, ModelParts
from service.memory_owner_core import OwnerCore, parse_instant
from service.memory_processor_seam import (
    AuthenticatedPrincipal,
    RetrievalFilters,
    RetrievalScope,
)
from service.memory_retrieval import MemoryRetrievalService

SEARCH_TYPES = (
    "CHUNKS",
    "CHUNKS_LEXICAL",
    "SUMMARIES",
    "RAG_COMPLETION",
    "GRAPH_COMPLETION",
    "TEMPORAL",
)
REFUSED_TYPES = {
    "CYPHER": "it runs a Cypher query on a Cognee graph file, which this service does not keep",
    "NATURAL_LANGUAGE": "it turns the question into Cypher for a Cognee graph file, which this service does not keep",
    "CODE": "it searches a code graph; no code is stored here",
    "CODING_RULES": "it searches coding rules; none are stored here",
    "SKILLS": "it searches Cognee skills; none are stored here",
    "FEELING_LUCKY": "it picks another search type by asking the model; name the type you want",
}


@cache
def memory_bm25_class() -> type:
    """Return the class of Cognee's BM25 chunk retriever that is handed memory's chunks.

    It is for CHUNKS_LEXICAL. Cognee is imported only here, when a lexical search is first asked
    for, so a service that never searches this way never loads it. The class's load takes the
    chunks the asker may see and builds Cognee's own tokens and corpus statistics from them;
    its ranked returns the best by Cognee's own BM25 scorer. It never reads a graph file and never
    calls a model.
    """
    from cognee.modules.retrieval.bm25_retriever import BM25ChunksRetriever

    class MemoryBm25(BM25ChunksRetriever):
        """Cognee's BM25 chunk retriever, handed memory's chunks instead of Cognee's graph."""

        def load(self, chunks: list[dict[str, Any]]) -> None:
            """Tokenise each chunk's text with Cognee's tokenizer and build the corpus statistics."""
            self.chunks, self.payloads = {}, {}
            for chunk in chunks:
                tokens = self.tokenizer(chunk["text"])
                if tokens:
                    self.chunks[chunk["piece_id"]] = tokens
                    self.payloads[chunk["piece_id"]] = chunk
            self._build_corpus_stats()
            self._stats_built = True
            self._initialized = True

        def ranked(self, query: str, limit: int) -> list[tuple[dict[str, Any], float]]:
            """Return up to limit (chunk, BM25 score) pairs, best first, keeping only scores above 0."""
            query_tokens = self.tokenizer(query)
            if not query_tokens or not self.chunks:
                return []
            scored = [
                (piece_id, self.scorer(query_tokens, tokens))
                for piece_id, tokens in self.chunks.items()
            ]
            best = nlargest(limit, scored, key=lambda pair: pair[1])
            return [(self.payloads[piece_id], score) for piece_id, score in best if score > 0]

    return MemoryBm25


class CogneeSearch:
    """Answers the search op: one named Cognee search type over the memory the asker may see."""

    def __init__(
        self,
        retrieval: MemoryRetrievalService,
        core: OwnerCore,
        read: Any,
        parts: ModelParts | None,
    ) -> None:
        """Keep the retrieval service, the owner core, the owner-worker reader and the model router.

        read runs a function on the owner's worker with its native handle; parts is the language-model
        router or None when no model is configured (the types that write an answer then refuse).
        """
        self.retrieval = retrieval
        self.core = core
        self.read = read
        self.parts = parts

    async def run(
        self,
        principal: AuthenticatedPrincipal,
        search_type: str,
        query: str,
        limit: int,
        filters: RetrievalFilters | None,
        opinion: str,
    ) -> dict[str, Any]:
        """Run one search type and return its reply; raises ValueError for an unknown or refused type.

        CHUNKS_LEXICAL, SUMMARIES, CHUNKS and the narrowing step of TEMPORAL need no answer-writing
        model; RAG_COMPLETION, GRAPH_COMPLETION and TEMPORAL's interval need the language model and
        raise ValueError when none is configured. Nothing is stored.
        """
        if search_type in REFUSED_TYPES:
            raise ValueError(
                f"search type {search_type} is not built here: {REFUSED_TYPES[search_type]}"
            )
        if search_type not in SEARCH_TYPES:
            raise ValueError(f"search_type must be one of {', '.join(SEARCH_TYPES)}")
        if opinion not in OPINIONS:
            raise ValueError(f"opinion must be one of {', '.join(OPINIONS)}")
        if search_type == "CHUNKS_LEXICAL":
            return await self._lexical(principal, query, limit, filters)
        if search_type == "TEMPORAL":
            return await self._temporal(principal, query, limit, filters, opinion)
        answers = await self.retrieval.retrieve(principal, query, limit, filters)
        if search_type == "CHUNKS":
            return {
                "search_type": search_type,
                "tier": "relational",
                "answer": answers["relational"],
            }
        if search_type == "SUMMARIES":
            summaries = self._items(answers["temporal"], "summary_id")
            return {"search_type": search_type, "summaries": summaries[:limit]}
        if search_type == "RAG_COMPLETION":
            chunks = self._items(answers["relational"], "chunk_id")[:limit]
            context = "\n".join(item["text"] for item in chunks)
            return await self._complete(search_type, "chunks", query, context, chunks, opinion)
        facts = self._items(answers["temporal"], None)[:limit]
        context = "\n".join(self._graph_line(item) for item in facts)
        return await self._complete(search_type, "graph", query, context, facts, opinion)

    @staticmethod
    def _items(answer: dict[str, Any], key: str | None) -> list[dict[str, Any]]:
        """Return a tier answer's items best first, only those carrying key when key is given."""
        found = [
            item
            for hit in answer.get("hits", [])
            for item in hit.get("items", [])
            if key is None or key in item
        ]
        return found

    @staticmethod
    def _graph_line(item: dict[str, Any]) -> str:
        """Write one fact, episode or summary as a line of graph context with its dates."""
        if "fact" in item:
            ends = f" until {item['invalid_at']}" if item.get("invalid_at") else ""
            return f"- fact (true from {item.get('valid_at')}{ends}): {item['fact']}"
        if "summary_id" in item:
            return f"- summary of {item.get('summary_of')}: {item['text']}"
        return f"- episode ({item.get('valid_at')}): {item.get('content', item.get('text', ''))}"

    async def _complete(
        self,
        search_type: str,
        style: str,
        query: str,
        context: str,
        used: list[dict[str, Any]],
        opinion: str,
    ) -> dict[str, Any]:
        """Have the language model write the answer from the context with Cognee's completion prompt."""
        if self.parts is None:
            raise ValueError("no language model is configured on this service")
        if not context.strip():
            return {
                "search_type": search_type,
                "answer": None,
                "reason": "nothing was found to answer from",
                "context": used,
            }
        written = await self.parts.run(
            "cognee", "complete", {"question": query, "context": context, "style": style}, opinion
        )
        return {
            "search_type": search_type,
            "answer": written["answer"],
            "context": used,
            "model": {
                key: written[key]
                for key in ("requested_model", "achieved_model", "placement", "seconds", "opinion")
            },
        }

    async def _lexical(
        self,
        principal: AuthenticatedPrincipal,
        query: str,
        limit: int,
        filters: RetrievalFilters | None,
    ) -> dict[str, Any]:
        """Rank the chunks the asker may see by Cognee's BM25 over their words; no model, no numbers."""
        visibility = await self.retrieval.policy.resolve(principal)
        scope = RetrievalScope(principal.agent_id, principal.scope, visibility, filters)
        permitted = permitted_producers(scope)
        if permitted is None:
            return {"search_type": "CHUNKS_LEXICAL", "hits": [], "withheld": 0}
        condition, params = permitted

        def read(handle: Any) -> list[dict[str, Any]]:
            """Read the live chunks of permitted events on the owner's worker."""
            rows = handle.execute(
                "SELECT p.piece_id, p.event_id, p.text, e.actor_agent_id FROM memory_pieces p "
                "JOIN memory_events e ON e.event_id = p.event_id "
                "LEFT JOIN memory_removals r ON r.target_event_id = p.event_id "
                "LEFT JOIN memory_piece_retirements x ON x.piece_id = p.piece_id "
                f"WHERE p.source = 'event' AND r.target_event_id IS NULL AND x.piece_id IS NULL "
                f"AND {condition} ORDER BY p.piece_id",
                params,
            ).fetchall()
            return [
                {"piece_id": r[0], "event_id": r[1], "text": r[2], "actor_agent_id": r[3]}
                for r in rows
            ]

        chunks = await self.read(read)
        retriever = memory_bm25_class()(top_k=limit)
        retriever.load(chunks)
        ranked = retriever.ranked(query, len(chunks) or 1)
        hits = [
            {**chunk, "item_id": chunk["piece_id"], "chunk_id": chunk["piece_id"], "bm25": score}
            for chunk, score in ranked
        ]
        narrowed = await self.retrieval.narrow("relational", hits, scope)
        return {
            "search_type": "CHUNKS_LEXICAL",
            "ranking": "Okapi BM25 by Cognee's own scorer over the words of each chunk; not meaning",
            "hits": narrowed["hits"][:limit],
            "withheld": narrowed["withheld"],
            "narrowed_out": narrowed["narrowed_out"],
            "chunks_searched": len(chunks),
        }

    async def _temporal(
        self,
        principal: AuthenticatedPrincipal,
        query: str,
        limit: int,
        filters: RetrievalFilters | None,
        opinion: str,
    ) -> dict[str, Any]:
        """Read the time window out of the question with Cognee's interval step, then search inside it.

        The window is what Cognee's extract_query_interval says the question is about; when the
        question names no time the search is refused with Cognee's reason rather than run unnarrowed.
        A filter the caller gave (tags) is kept; a time window the caller gave is replaced by the question's.
        """
        if self.parts is None:
            raise ValueError("no language model is configured on this service")
        window = await self.parts.run("cognee", "query_window", {"question": query}, opinion)
        if window["starts_at"] is None and window["ends_at"] is None:
            return {
                "search_type": "TEMPORAL",
                "window": None,
                "reason": window["reason"] or "the question names no time",
            }

        def moment(text: str | None) -> datetime | None:
            """Read Cognee's ISO moment as an aware UTC time, or None for an open side."""
            return None if text is None else parse_instant(text).astimezone(timezone.utc)

        narrowed = RetrievalFilters(
            since=moment(window["starts_at"]),
            until=moment(window["ends_at"]),
            tags=() if filters is None else filters.tags,
            tag_match="any" if filters is None else filters.tag_match,
            period="from the question",
        )
        answers = await self.retrieval.retrieve(principal, query, limit, narrowed)
        return {
            "search_type": "TEMPORAL",
            "window": {"starts_at": window["starts_at"], "ends_at": window["ends_at"]},
            "tiers": answers,
        }
