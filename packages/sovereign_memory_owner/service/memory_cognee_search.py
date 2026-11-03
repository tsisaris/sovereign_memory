"""Cognee's other search types, answered from the memory the asker may see.

It is for the search types of Cognee that are not plain chunk ranking: CHUNKS_LEXICAL (Cognee's own
BM25 chunk retriever, no model and no numbers), SUMMARIES (the Zep summaries ranked by meaning),
RAG_COMPLETION, HYBRID_COMPLETION, GRAPH_COMPLETION and TRIPLET_COMPLETION (an answer written by the
language model from the best chunks, from the dated facts, episodes and summaries, or from both, with
Cognee's own completion prompts), GRAPH_SUMMARY_COMPLETION (the facts summarised first by Cognee's own
summariser), GRAPH_COMPLETION_CONTEXT_EXTENSION, GRAPH_COMPLETION_COT and GRAPH_COMPLETION_DECOMPOSITION
(Cognee's reasoning loops and question splitting over the same facts, with Cognee's own prompts and its
own default number of rounds), FEELING_LUCKY (Cognee's own selector picks the type), TEMPORAL (the time
window Cognee's own interval step reads out of the question, then the search narrowed to it) and CHUNKS
(the ordinary per-model ranking). It takes the asker, the search type, the question, the limit, an
optional filter and which opinion (main or second model) writes any answer. It gives back the type, the
hits or the answer with the context it was written from, and the model that really answered. It never
reads what the asker may not see, never writes, and refuses a search type that cannot run here (Cypher,
natural language, code, rules, skills, graph report, broad and the agent) by naming the measured
reason, never by answering something else.

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
    "HYBRID_COMPLETION",
    "GRAPH_COMPLETION",
    "TRIPLET_COMPLETION",
    "GRAPH_SUMMARY_COMPLETION",
    "GRAPH_COMPLETION_COT",
    "GRAPH_COMPLETION_CONTEXT_EXTENSION",
    "GRAPH_COMPLETION_DECOMPOSITION",
    "FEELING_LUCKY",
    "TEMPORAL",
)
# The types that cannot run here, each with the measured reason (docs/prior_art.md has the rows).
REFUSED_TYPES = {
    "CYPHER": "Cognee's DuckDB graph adapter raises 'Cypher queries not supported in DuckDB' "
    "(cognee_community_hybrid_adapter_duckdb/duckdb_adapter.py, query), and no Cypher engine "
    "(Kuzu, Ladybug) is installed in the project's environment",
    "NATURAL_LANGUAGE": "it turns the question into Cypher for a Cypher engine, and none runs here "
    "(see CYPHER)",
    "CODE": "it searches a code graph built by Cognee's code pipeline; no code is stored here",
    "CODING_RULES": "it searches coding rules built by Cognee's rules pipeline; none are stored here",
    "SKILLS": "it searches Cognee skills built by Cognee's skills pipeline; none are stored here",
    "GRAPH_REPORT": "it reads graph reports that only Cognee's cognify pipeline writes; "
    "this service runs no cognify",
    "BROAD": "it reads the global context buckets that only Cognee's cognify pipeline writes; "
    "this service runs no cognify",
    "AGENTIC_COMPLETION": "it runs a tool-using agent over a Cognee dataset and user, which this "
    "service does not keep",
}
MODEL_REPORT_KEYS = ("requested_model", "achieved_model", "placement", "seconds", "opinion")


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
        opinion: str | None,
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
        if opinion is not None and opinion not in OPINIONS:
            raise ValueError(f"opinion must be one of {', '.join(OPINIONS)}")
        if search_type == "CHUNKS_LEXICAL":
            return await self._lexical(principal, query, limit, filters)
        if search_type == "TEMPORAL":
            return await self._temporal(principal, query, limit, filters)
        if search_type == "FEELING_LUCKY":
            return await self._lucky(principal, query, limit, filters, opinion)
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
        chunks = self._items(answers["relational"], "chunk_id")[:limit]
        facts = self._items(answers["temporal"], None)[:limit]
        if search_type == "RAG_COMPLETION":
            context = "\n".join(item["text"] for item in chunks)
            return await self._complete(search_type, "chunks", query, context, chunks, opinion)
        if search_type == "HYBRID_COMPLETION":
            context = (
                "PASSAGES:\n"
                + "\n".join(f"- {item['text']}" for item in chunks)
                + "\nFACTS, EPISODES AND SUMMARIES:\n"
                + "\n".join(self._graph_line(item) for item in facts)
            )
            return await self._complete(
                search_type, "hybrid", query, context, chunks + facts, opinion
            )
        context = "\n".join(self._graph_line(item) for item in facts)
        if search_type in ("GRAPH_COMPLETION", "TRIPLET_COMPLETION"):
            reply = await self._complete(search_type, "graph", query, context, facts, opinion)
            if search_type == "TRIPLET_COMPLETION":
                reply["ran_as"] = "GRAPH_COMPLETION (Cognee's own deprecation mapping)"
            return reply
        if search_type == "GRAPH_SUMMARY_COMPLETION":
            summary = await self.parts_run("summarize", {"text": context}, opinion)
            reply = await self._complete(
                search_type, "graph", query, summary["summary"], facts, opinion
            )
            reply["summary_of_context"] = summary["summary"]
            return reply
        if search_type == "GRAPH_COMPLETION_DECOMPOSITION":
            return await self._decomposed(principal, query, limit, filters, opinion)
        return await self._reasoned(principal, search_type, query, limit, filters, opinion, facts)

    async def parts_run(
        self, step: str, args: dict[str, Any], opinion: str | None
    ) -> dict[str, Any]:
        """Run one Cognee model step through the router; raises ValueError when no model is configured."""
        if self.parts is None:
            raise ValueError("no language model is configured on this service")
        return await self.parts.run("cognee", step, args, opinion)

    async def _more_facts(
        self,
        principal: AuthenticatedPrincipal,
        question: str,
        limit: int,
        filters: RetrievalFilters | None,
        known: dict[str, dict[str, Any]],
    ) -> int:
        """Search again for a new question and add the facts, episodes and summaries not yet known.

        known maps item id to item and grows by what is new; the return value is how many were added.
        """
        answers = await self.retrieval.retrieve(principal, question, limit, filters)
        added = 0
        for item in self._items(answers["temporal"], None):
            if item["item_id"] not in known:
                known[item["item_id"]] = item
                added += 1
        return added

    async def _reasoned(
        self,
        principal: AuthenticatedPrincipal,
        search_type: str,
        query: str,
        limit: int,
        filters: RetrievalFilters | None,
        opinion: str | None,
        facts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run Cognee's chain-of-thought or context-extension search type with memory as its graph.

        The number of rounds is Cognee's own default for that type (read from its code by the defaults
        step). Context extension answers, searches again with the answer as the question and stops when
        nothing new comes or the rounds are used; chain of thought has the model say what is missing
        from the answer, ask one follow-up question about it, search for that and answer again. The
        prompts are Cognee's own files. It returns the final answer, every round and the context used.
        """
        defaults = await self.parts_run("defaults", {}, None)
        known = {item["item_id"]: item for item in facts}
        rounds: list[dict[str, Any]] = []
        extending = search_type == "GRAPH_COMPLETION_CONTEXT_EXTENSION"
        total = defaults["context_extension_rounds" if extending else "cot_rounds"]
        answer = None
        for number in range(1, total + 1):
            context = "\n".join(self._graph_line(item) for item in known.values())
            written = await self._complete(search_type, "graph", query, context, [], opinion)
            answer = written.get("answer")
            if answer is None:
                break
            if extending:
                question = answer
                step: dict[str, Any] = {"round": number, "answer": answer}
            else:
                reasoning = await self.parts_run(
                    "prompted",
                    {
                        "system_prompt_file": "cot_validation_system_prompt.txt",
                        "user_prompt_file": "cot_validation_user_prompt.txt",
                        "variables": {"query": query, "answer": answer, "context": context},
                    },
                    opinion,
                )
                followup = await self.parts_run(
                    "prompted",
                    {
                        "system_prompt_file": "cot_followup_system_prompt.txt",
                        "user_prompt_file": "cot_followup_user_prompt.txt",
                        "variables": {
                            "query": query,
                            "answer": answer,
                            "reasoning": reasoning["reply"],
                        },
                    },
                    opinion,
                )
                question = followup["reply"]
                step = {
                    "round": number,
                    "answer": answer,
                    "missing": reasoning["reply"],
                    "follow_up_question": question,
                }
            step["new_items"] = await self._more_facts(principal, question, limit, filters, known)
            rounds.append(step)
            if extending and step["new_items"] == 0:
                break
        context = "\n".join(self._graph_line(item) for item in known.values())
        final = await self._complete(
            search_type, "graph", query, context, list(known.values()), opinion
        )
        final["rounds"] = rounds
        final["rounds_allowed"] = total
        return final

    async def _decomposed(
        self,
        principal: AuthenticatedPrincipal,
        query: str,
        limit: int,
        filters: RetrievalFilters | None,
        opinion: str | None,
    ) -> dict[str, Any]:
        """Split the question into sub-questions with Cognee's decomposition, gather facts for each, answer once.

        This is Cognee's combined-context mode: the facts found for every sub-question are put together
        and one answer is written from them. The sub-questions and what each found are returned.
        """
        parts = await self.parts_run("decompose", {"question": query}, None)
        known: dict[str, dict[str, Any]] = {}
        found: list[dict[str, Any]] = []
        for sub in parts["subqueries"]:
            found.append(
                {
                    "sub_question": sub,
                    "new_items": await self._more_facts(principal, sub, limit, filters, known),
                }
            )
        context = "\n".join(self._graph_line(item) for item in known.values())
        final = await self._complete(
            "GRAPH_COMPLETION_DECOMPOSITION", "graph", query, context, list(known.values()), opinion
        )
        final["sub_questions"] = found
        final["fell_back"] = parts["fell_back"]
        return final

    async def _lucky(
        self,
        principal: AuthenticatedPrincipal,
        query: str,
        limit: int,
        filters: RetrievalFilters | None,
        opinion: str | None,
    ) -> dict[str, Any]:
        """Let Cognee's own selector pick the search type, then run that one if it can run here.

        Raises ValueError, naming the picked type and why, when the pick is one this service refuses;
        never answers with a different type than the one picked.
        """
        picked = (await self.parts_run("select_search_type", {"question": query}, None))[
            "search_type"
        ]
        if picked == "FEELING_LUCKY":
            raise ValueError("the selector picked FEELING_LUCKY itself; name a search type")
        reply = await self.run(principal, picked, query, limit, filters, opinion)
        return {"search_type": "FEELING_LUCKY", "picked": picked, "result": reply}

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
        opinion: str | None,
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
            "model": {key: written[key] for key in MODEL_REPORT_KEYS},
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
    ) -> dict[str, Any]:
        """Read the time window out of the question with Cognee's interval step, then search inside it.

        The window is what Cognee's extract_query_interval says the question is about; when the
        question names no time the search is refused with Cognee's reason rather than run unnarrowed.
        A filter the caller gave (tags) is kept; a time window the caller gave is replaced by the question's.
        """
        if self.parts is None:
            raise ValueError("no language model is configured on this service")
        window = await self.parts.run("cognee", "query_window", {"question": query}, None)
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
