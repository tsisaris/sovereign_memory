# Handoff, 2026-10-22, sovereign_memory

Branch `fix/existing_20261022`, from `origin/main` at `d13750a`. Python: `export PATH=$HOME/micromamba/envs/sovereign_memory/bin:$PATH`. `uv run` is blocked on this laptop (it would make a virtual environment), so the checkers are called straight from that environment: `pre-commit run --all-files`, `deptry src tests`, `pytest -q`, `uv lock --check`. Shawn's goal for this round: fix and prove what already exists before building more.

## What was done (each measured in this window)

1. SQLite out.
   - Graphiti's answer store (`packages/graphiti_duckdb/src/graphiti_core/llm_client/cache.py`) now keeps its rows in the table `graphiti.llm_cache` of the memory DuckDB file, only through the memory service's own connection (Shawn's ruling: no second connection, no standalone file). A client created with caching on and no `cache_connection` raises a plain error naming what is missing. `get` and `set` are now `await`ed in `client.py` and `gliner2_client.py`; `cache_connection` is passed through the Groq, Gemini, Anthropic and GLiNER2 clients (the OpenAI clients already refuse caching). Two swallowed errors in `gemini_client.py` that the edit hook flagged were turned into logged `JSONDecodeError` handling.
   - The Cognee cache-repair migration (`c3d5e7f9a1b2`) no longer imports `sqlite3`: a stock SQLite cache file is read and healed through DuckDB's own `sqlite` extension (a Turso cache still uses pyturso). Measured: it removed the older duplicate row, added the unique index, did nothing on a second run, and the downgrade dropped the index. On the DuckDB path it is only reached when someone sets a separate cache database; the default cache backend is `duckdb`.
   - `DB_PROVIDER=sqlite` (relational) and `CACHE_BACKEND=sqlite` now refuse with a plain message naming `duckdb`. `aiosqlite` is not installed in this environment at all, so those branches could only have crashed.
   - dlt database ingestion refused on DuckDB (it accepted only SQLite or PostgreSQL). It now uses dlt's own DuckDB destination; measured: three rows ingested and read back.
   - Mem0's telemetry label `history_store` said `sqlite`; now `duckdb`.
   - NOT changed, left for Shawn: the readers of a user's own outside SQLite database (`cognee/modules/tools/text_to_sql/engine_factory.py`, `tools/connections.py`, `tasks/ingestion/create_dlt_source.py`) still name `sqlite+aiosqlite` and need a package this environment lacks; and `TursoAdapter` / `turso/` is a SQLite-compatible engine, optional and not installed. Both are outside memory's own storage.
2. Settings into DuckDB. Every setting except the database path is the table `memory_runtime_settings` (columns `path`, `value` as JSON, `source_file`, `loaded_at`) in the memory DuckDB file. `src/sovereign_memory/config.py` reads it at start and checks it with the same `parse()` as before (no defaults; a missing or unknown setting is refused by name). `python -m sovereign_memory load-settings <settings.toml> <memory.duckdb>` checks the file the way the service would, then replaces the table in one transaction. `python -m sovereign_memory <memory.duckdb>` serves; the path may also come from `SOVEREIGN_MEMORY_DATABASE`. The old variable `SOVEREIGN_MEMORY_CONFIG` and a `.toml` given as the database are refused with a message naming the new way. Searched read-only (`/usr/bin/grep`) in this project, `~/sovereign`, `~/portable/projects` and `~/agent_project_manager_recovery`: the only place that sets or reads that variable is this project's `runtime.py` (plus a checkpoint copy of the same file under `~/sovereign/local/checkpoint_independent_20261013/baseline_worktree`). `memory_settings` (values in effect) is kept; its source text now reads "memory_runtime_settings table, [ranking]". A readable example is in `local/settings/sovereign_memory_settings.toml` (never committed).
3. Run for real. The service ran on `local/proof/memory.duckdb` (deleted afterwards) and was talked to over its Unix socket. Measured: capture, background processing with honest receipts, retrieve, forget, a repeated identical capture, a conflicting capture refused, eight parallel captures, a version 2 event with payload, a wrong payload fingerprint refused, expiry of a short-lived event, review select/show/judge, a clean stop and restart with data kept. The goal's proof lines are in the ledger cells for rows 7, 12, 18, 23, N1, N3, N10, N12.
4. The path a prompt takes. New socket operation `context` (`OwnerService._context`): calls Graphiti's own `search_results_to_context_string` and appends a `WAITING` section ("waiting on a language model", "waiting on numbers", "not configured"). Recording an exchange needs no new code: one version 2 event with a payload of role messages. Also: a tier waiting on numbers now logs one plain line per retry instead of a full stack trace (`memory_dispatcher.py`).

## Ledger counts

Rows marked "proven": 6 before, 6 after. No row was newly proven: every row with something left to run waits on a language model or on numbers from agent_corpus, and the partial live evidence was added to those rows without changing their status. The rows whose live evidence grew: 7, 12, 18, 23, N1, N3, N10, N12.

## Still waiting (row by row, as in the ledger cells)

- Language model: 9, 12 (extraction, bulk), 13, 14, 16 (five tools), 17 (facts), 18 (facts, entities, communities, summaries), 19, 20, 22, 23 (cognify), 24, 25, 28 (extraction), 29, 30, 31, 33, 34, 35 (summaries).
- Numbers from agent_corpus / agent_embedding: 7 and 8 (meaning-based search), 15, 32 (live), N1 to N4, N6 to N10, N12.
- Opaque, nothing to run: 21, 36.
- agent_corpus does not boot (see the 2026-10-21 handoff); nothing here was changed in it.

## Rules kept and things to know

- Nothing was written under `~/sovereign`; no `--no-verify`, no ignores; no new test code; no patch files.
- Not touched: `memory_ground_truth.py`, `memory_review.py`, `memory_combiners.py` (only run through the socket).
- The graphiti `OwnerConnection` type is a local protocol so `graphiti_core` does not import the owner's bridge.
- Cognee's own settings still come from its environment variables (`DB_PATH`, `SYSTEM_ROOT_DIRECTORY`, ...). Those are Cognee's, not memory's, and were not moved.
- The decay constant in `ContextAssembler.__init__` still has default bounds 0.3 and 1.5 in code (Law 32 question for the architect); not changed this round.
- Not done: a crash (kill -9) replay proof; the openAI-style clients' caching (they refuse it).
