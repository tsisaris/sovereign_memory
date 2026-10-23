# Handoff, 2026-10-22 (second window), sovereign_memory

Branch `fix/model_and_settings_20261022`, from `origin/main` at `0312195`. Python: `export PATH=$HOME/micromamba/envs/sovereign_memory/bin:$PATH`. Checkers run straight from that environment.

## What was done

1. Settings live only in DuckDB. `python -m sovereign_memory show-settings <db> [--history]` prints name, value, plain-words description, when last changed and by whom, built from the database each run. `set-setting <db> name=value ... [--describe name=words] [--unset name] [--by who]` checks the complete result with the existing validator, then writes values, descriptions and one history row per change (table `memory_runtime_settings_history`) in one transaction; it refuses plainly on a wrong value, a new setting with no description, or a locked file. The text loader and `local/settings/sovereign_memory_settings.toml` are gone; descriptions live in each setting's row (`settings_store.py`).
2. Language model is a setting: table `language_model` (provider, model, base_url, api_key, request_timeout_seconds, options). Mem0's llm section is made from it (a stored `llm` section is refused). Mem0's OpenAI-style client now honours `request_timeout_seconds` (before, it waited 600 s and retried). Not wired from settings: Graphiti, Cognee and Letta engines inside the service (my scratch helper that did this was dropped because deptry refused its imports; a copy is not kept). The proof scripts built those clients from the same settings by hand.
3. Leftovers: SQLite files handed to Cognee are read through DuckDB's `sqlite` extension (text-to-SQL engines and dlt ingestion; `sqlite_scan`/`sqlite_attach` are barred in generated SQL); Turso is refused naming duckdb for the relational database and the cache; decay bounds `ranking.decay_lower_bound` and `decay_upper_bound` are settings (declared together with `decay_time_constant_seconds`; no default in code).

## Model facts, measured
- Both Qwen3.8 files (9B and 4B Distill) import into Ollama 0.23.3 but fail to load: "qwen3next: layer 32 missing attn_qkv/attn_gate projections". Ollama may not be upgraded this round. They stay in Ollama as `qwen38_9b_cpu` and `qwen38_4b_distill` (unusable until Ollama is upgraded).
- Proof model used instead: `qwen3_8b_cpu`, a processor-only copy (num_gpu 0, num_ctx 8192) of the installed `qwen3:8b`. About 3.5 tokens per second. Also `lfm25_8b_cpu`, a processor-only copy of the installed LFM2.5 8B, used for the model-switch demonstration.
- Dictation was moved to the processor with `ctl.py cpu` and back with `ctl.py gpu` (the card was not needed in the end). Final reading: dictation on the graphics card, 1473 MiB used, no model loaded.

## Ledger
Proven count: 6 before, 6 after. Ten rows got run evidence (12, 13, 14, 18, 19, 23, 24, 28, 29, 31); none is wholly proven. Passed for the model part: Graphiti entity and fact extraction, entity resolution, entity summaries, the context block's formatting, Mem0 fact extraction, Cognee graph extraction. Bad: Graphiti contradiction check (one of four invalidations wrong), Mem0 action decisions (wrong ids, empty reply through the real function), Cognee entity rewrite (wrong attachment), Mem0 procedural text (echoes the instructions).
Not runnable because nothing is built: Letta consolidation, compaction, dreaming (row 9); Zep thread and user summaries and summary instructions (row 20); Cognee temporal tasks (row 25, the package was removed). Rows 16, 17, 22, 30, 33, 34, 35 were not run. Rows 21 and 36 are opaque.

## Next
Upgrade Ollama (needs Shawn's go-ahead) so the Qwen3.8 files load; rerun the bad rows on a larger model; wire Graphiti's and Cognee's model from the settings table inside the service; the service's `context` operation with facts present has not been shown.

## Notes
- Existing `TODO` at `packages/mem0_duckdb/src/mem0/llms/openai.py` (about line 167) is Mem0's own comment; the edit hook flags it.
- `local/proof/memory.duckdb` holds the settings of the proof database (never committed); its scratch siblings were deleted.
