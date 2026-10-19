# Capability Ledger

| Origin | Capability | Where it exists today | Version, licence | Status | Proven by |
|---|---|---|---|---|---|
| Letta | Pinned in-context memory blocks (`label`, `value`, `limit`, `read_only`), attach/detach, shared blocks | Letta V1 server source, `archive` branch | 0.16.8, Apache-2.0 | Open (retired code) | unproven |
| Letta | Block editing tools `core_memory_append/replace`, `memory_insert/replace/rethink/apply_patch`, concurrency rules | Same; documented semantics | Apache-2.0 | Open | unproven |
| Letta | Recall: every message stored; `conversation_search`; conversations sharing one agent | Same | Apache-2.0 | Open | unproven |
| Letta | Archival passages with tags, semantic search, `archival_memory_insert/search` | Same (pgvector columns) | Apache-2.0 | Open | unproven |
| Letta | Sleep-time consolidation, compaction modes, tool rules | Same; MemGPT paper CC BY 4.0 | Apache-2.0 / CC BY 4.0 | Open | unproven |
| Letta | Git-versioned memory files, history, backups, skills in memory, dreaming | Letta Code local backend; plain git | 0.34.12, Apache-2.0 | Open | unproven |
| Letta | Shared memory repositories across agents | Docs disclose fully: a git repo cloned at `$MEMORY_DIR/../<name>/`, pull/push | Docs | Paper/docs (cloud-only in product) | unproven |
| Zep/Graphiti | Episode ingestion (message/json/text/fact_triple), bulk ingestion, provenance, `remove_episode` | graphiti-core | 0.31.1, Apache-2.0 | Open | unproven |
| Zep/Graphiti | Four-timestamp facts and invalidation on contradiction | graphiti-core `edges.py`; paper §2.1–2.2 | Apache-2.0 | Open | unproven |
| Zep/Graphiti | Entity resolution, custom Pydantic ontology, `add_triplet`, communities (label propagation), sagas, `group_id` namespaces | graphiti-core; paper §2.2–2.3 | Apache-2.0 | Open | unproven |
| Zep/Graphiti | Hybrid search (cosine + BM25 + breadth-first), 5 rerankers, 16 recipes, injection-safe filters, local BGE reranker, Ollama via `OpenAIGenericClient` | graphiti-core `search_config.py` | Apache-2.0 | Open | unproven |
| Zep/Graphiti | MCP server with 13 memory tools | graphiti `mcp_server/` | mcp-v1.1.0, Apache-2.0 | Open | unproven |
| Zep CE | Users, sessions, messages, "relevant facts" for a session | Zep CE Go source in `legacy/` | v1.0.2, Apache-2.0 | Open (discontinued) | unproven |
| Zep Cloud | Context block assembly (FACTS with date ranges, ENTITIES, episodes, summaries) | Paper §3; docs template `%{user_summary} / %{edges limit=10} / %{entities limit=5}` | — | Paper/docs | unproven |
| Zep Cloud | Entity summaries ("previous summary plus the new information") | Docs; Graphiti node `summary` field | — | Paper/docs | unproven |
| Zep Cloud | Thread and user summaries, summary instructions | Docs (thin); Mem0 paper §2.1 describes the same asynchronous summary pattern | — | Paper/docs, under-documented | unproven |
| Zep Cloud | Observations (pattern detection over the graph) | Docs give behaviour and API only; method described as "clusters of facts and entities" | — | **Opaque** | unproven |
| Zep Cloud | Proprietary extraction LLM, reranker, embedder; Konig; dashboards; SOC 2 | — | — | Opaque but substitutable (any local model slots into Graphiti) | unproven |
| Cognee | `add/cognify/search` and `remember/recall/improve/forget`, 21 search types, OWL/RDF ontology, loaders for text/CSV/code/image/audio/video/PDF/Office | cognee | 1.6.5, Apache-2.0 | Open | unproven |
| Cognee | Feedback weights, truth state, conflict review, entity consolidation, cross-connecting, session-to-graph promotion | cognee `tasks/memify/`, graph interface | Apache-2.0 | Open | unproven |
| Cognee | Temporal search, timestamps-in-range, temporal anchors | cognee | Apache-2.0 | Open | unproven |
| Cognee | Users, roles, tenants, dataset permissions, per-dataset database isolation | cognee relational layer | Apache-2.0 | Open | unproven |
| Cognee | MCP server, REST, UI, visualiser, importers from Mem0/Zep/Graphiti/Letta, eval framework (LoCoMo, BEAM) | cognee | Apache-2.0 | Open | unproven |
| Mem0 | Scoped memories (`user_id/agent_id/run_id`), `add/get/get_all/search/update/delete/history`, metadata filters with ten operators, reranking, async, vision input, OpenAI-compatible chat endpoint, self-hosted REST server | mem0ai | 2.2.1, Apache-2.0 | Open | unproven |
| Mem0 | ADD/UPDATE/DELETE/NONE consolidation on `add()` | mem0ai 1.0.11 sdist: `mem0/configs/prompts.py` line 443, `mem0/memory/main.py` lines 591–660; paper §2.1 | 1.0.11, Apache-2.0 | Open (removed from current OSS) | unproven |
| Mem0 | Graph memory: typed triplets, node matching by similarity, invalid-not-deleted edges, neighbourhood retrieval | mem0ai 1.0.11 `mem0/memory/graph_memory.py` and backends; paper §2.2 | 1.0.11, Apache-2.0 | Open (removed); strict subset of Graphiti's model | unproven |
| Mem0 | Procedural memory type | mem0ai 2.2.1 (marked for removal) | Apache-2.0 | Open | unproven |
| Mem0 Platform | Memory decay (search-time bias 0.3×–1.5×, score clamped to [0,1], never filters) | Platform changelog 2026-05-04 | — | Paper/docs; time constant undisclosed | unproven |
| Mem0 Platform | Temporal reasoning ("last week", `reference_date`) | Changelog 2026-05-13; Graphiti filters supply the data side | — | Paper/docs; boost function undisclosed | unproven |
| Mem0 Platform | Dream background consolidation | Platform description; Mem0's public six-step "dream" skill; 1.0.11 UPDATE/DELETE logic | — | Paper/docs | unproven |
| Mem0 Platform | Feedback (`POSITIVE/NEGATIVE/VERY_NEGATIVE`), custom categories, summaries, webhooks, export, batch ops | API shapes documented | — | Paper/docs; ranking effect of feedback undisclosed | unproven |
| Mem0 Platform | Exact ranking constants (graph boost, temporal boost, decay time constant) | — | — | **Opaque** | unproven |

Rows N1 to N12 were added on 2026-10-19 from the owner's new requirements. They add to the 32 rows above and replace none of them. "Where it exists today" is measured at commit 47e50fc; the plan's amendment section of the same date gives each measurement and the decisions still open.

| Origin | Capability | Where it exists today | Version, licence | Status | Proven by |
|---|---|---|---|---|---|
| Owner requirement 2026-10-19 | N1 Models are swappable parts: memory names no model in code and works with any number of embedding and classifier models, now and later; a model is a registered row of data | Absent. The relational tier is built around one embedding engine fixed at tier creation (`memory_cognee_chunks.py`) | Project code | Open, new requirement | unproven |
| Owner requirement 2026-10-19 | N2 Side-by-side numbers: every model's numbers are stored beside the others, each labelled with model, revision and width; adding a model touches no other model's numbers | Absent. One identity is stamped per tier; a second model raises `EmbeddingIndexMismatch` and demands a rebuild; the rebuilds table holds one identity | Project code | Open, new requirement | unproven |
| Owner requirement 2026-10-19 | N3 Originals are the permanent record: any model's numbers can be thrown away and rebuilt from them, and no rebuild alters another model's numbers or the originals | Partly. Originals are kept in `memory_payloads` and nothing in the Owner core deletes a payload row. Rebuild is per tier, not per model | Project code | Open, new requirement | unproven |
| Owner requirement 2026-10-19 | N4 One ranking per model, merged by a combining step; the current combiner is recorded | Absent. Retrieval returns one answer per memory kind and merges nothing (`memory_retrieval.py`) | Project code | Open, new requirement | unproven |
| Owner requirement 2026-10-19 | N5 Fixed starting combiner: reciprocal rank fusion, recorded as the current combiner until a better one is accepted | Named only as a glossary term and as a worked example from a retired candidate in the plan; not adopted and not built | Published method | Open, new requirement | unproven |
| Owner requirement 2026-10-19 | N6 A new combiner replaces the current one only if it scores better on held-back ground truth, which is what guarantees "never worse" | Absent. No ground truth, held-back split or comparison exists | Project code | Open, new requirement | unproven |
| Owner requirement 2026-10-19 | N7 Permanent ground truth: Shawn's judgments (retrieved item right or wrong; which answer is correct where models disagree) kept together with every model's output for that case | Absent. The Owner creates six tables (events, receipts, removals, payloads, rebuilds, rebuild receipts); none holds a judgment | Project code | Open, new requirement | unproven |
| Owner requirement 2026-10-19 | N8 Review screen: disagreements shown beside the original; Shawn picks or types the right answer | Absent | Project code | Open, new requirement | unproven |
| Owner requirement 2026-10-19 | N9 Everything needed to train and retrain decision models automatically is recorded; whether one model per memory kind or one shared model is better is decided by measurement. Training itself belongs to agent_uagent_builder | Absent | Project code | Open, new requirement | unproven |
| Owner requirement 2026-10-19 | N10 Supply: memory receives its numbers from agent_corpus and asks it to recompute missing or stale ones; memory never computes numbers, never measures text and never cuts text | Absent. The Mem0 adapter calls agent_embedding directly; agent_corpus's number-saving node M_2_B_1 is not built (no `M_2_*` file in its source), and its design lists no door that hands stored numbers to another agent (whole HTTP endpoint list read) | Project code | Open, new requirement | unproven |
| Owner requirement 2026-10-19 | N11 The direct-call adapter is retired from the memory path and its history is kept | Present and active: `packages/mem0_duckdb/src/mem0/embeddings/agent_embedding.py`, and the `agent_embedding` branch of the relational tier's identity code | Project code | Open, new requirement | unproven |
| Owner requirement 2026-10-19 | N12 Search-time numbers: the numbers for a search question come from the supplier's door, never from memory | Absent. The relational tier embeds the question itself (`prepare_query`, measured); the Mem0 tier is expected to do so through its configured embedder (reasoning, not read). agent_corpus's design lists no door for a single question (whole HTTP endpoint list read) | Project code | Open, new requirement, needs an agent_corpus door | unproven |
