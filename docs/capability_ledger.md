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
