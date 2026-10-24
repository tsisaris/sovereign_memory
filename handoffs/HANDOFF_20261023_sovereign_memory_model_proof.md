# Handoff, 2026-10-23, sovereign_memory (model proof)

Branch `fix/model_proof_20261023`, from `origin/main` at `363e723` (pull request 22 merged). Python: `export PATH=$HOME/micromamba/envs/sovereign_memory/bin:$PATH`; the checkers run straight from that environment (`uv run` is blocked on this laptop).

## What was done

1. Models. `ornith_35b_a3b_cpu` was created in Ollama from `/media/tsisaris/DATA/lmstudio-models/lmstudio-community/Ornith-1.5-35B-A3B-AD-Q6_K.gguf` (35 billion parameters, architecture qwen35moe, processor only: `PARAMETER num_gpu 0`, context 8192). `qwen38_4b_distill` had no `num_gpu 0` stored, so a processor-only copy `qwen38_4b_distill_cpu` was made (nothing else was changed or removed). Main model setting: `ornith_35b_a3b_cpu`; second opinion setting `language_model.second_opinion_model`: `qwen38_4b_distill_cpu`. Both are set through `set-setting`; no model name is in code. Graphics card readings: 1535 MiB before the first model, 1535 MiB after creating Ornith and while it answered; later readings of 1599 to 1683 MiB were dictation's own process growing (1546 to 1630 MiB) plus the agent_embedding process (46 MiB); Ollama reported "processor only" (0 bytes in the card) for every model call.
2. Every part takes its model from the setting, inside the service. New file `packages/sovereign_memory_owner/service/memory_model_parts.py` (the router and the recorder of which model and device really answered) with one part per engine: `memory_model_graphiti.py`, `memory_model_cognee.py`, `memory_model_letta.py`, `memory_model_zep.py` and `src/sovereign_memory/model_mem0.py`. Two new socket operations: `model_parts` (what each part asks for and last got an answer from) and `model` (run one step of one part, with "main" or "second" opinion). `src/sovereign_memory/runtime.py` builds the parts; `config.py` accepts the new settings sections `zep`, `cognee`, `letta` and the optional `language_model.second_opinion_model`. Switch demonstration: the setting was changed to `qwen38_4b_distill_cpu`, only the service was restarted, and one call to each of Mem0, Cognee, Letta, Zep and Graphiti each reported `achieved_model` `qwen38_4b_distill_cpu`; then the setting was put back to Ornith.
3. The service's `context` operation now runs Graphiti's own keyword search over the stored graph (no numbers needed) and returns FACTS with dates, ENTITIES, EPISODES and COMMUNITIES.
4. Three abilities rebuilt, each by faithful translation with the original file named in every docstring: Letta compaction, sleep-time consolidation and dreaming (row 9, Letta commit 56ba9c25); Zep thread and user summaries and summary instructions (row 20, Zep commit `411142c^`, Graphiti's summary prompt, Zep's published instruction limits); Cognee's event tasks (row 25). Row 25 history: Cognee itself removed its old temporal package on 2026-10-06 in commit `1513b1f41` by Milenko Gavric (last used in release 1.6.2; release 1.6.5 never had it); its current flow covers dates, question windows and time search; the translated pieces are only what it lacks (events, entities around events, event descriptions). Also restored for row 30: Mem0's graph-memory prompts and tools (v1.0.11; Mem0 removed graph memory on 2026-04-14, commit `a488e190` by soumil-rathi) and a translation of the model half of `graph_memory.py`.
5. Every constant the originals do not publish is a setting with no default (`zep.*`, `letta.compaction.*`, `letta.sleeptime.max_steps`, `letta.dream.*`, `cognee.data_directory`); the new `language_model.options.max_tokens` is a ceiling on one answer (cut after the 4B ran for 45 minutes in one call).

## Ledger

Wholly proven rows: 6 before, 8 after (rows 9 and 19 added). Language-model parts that pass through the real service, with Ornith: rows 9, 12, 14, 16 (saga summary only), 17 (graph behind it), 18, 19, 22, 23, 24 (rewrite only), 25, 28, 29, 30, 31. Not proven: row 13 (right with every stored fact as candidates, one miss with same-entity candidates), row 20 (the user-summary update is wrong on both models), and every row whose other half waits on numbers. Rows 33, 34, 35: nothing in open code to translate (Mem0 Platform features described in documents only; Cognee's prompt deliberately does not guess "last week"). See the cells of `docs/capability_ledger.md` for the sample, time and verdict of each run.

The 4B side: it fails Graphiti's extraction (answers run on until cut), fails the Cognee entity rewrite (answers with the schema), gives no time window for "What happened in March 2026?" (wrong), writes a filler community name ("This summary provides a concise overview ..."), is wrong on the Zep user summary update, and passes compaction, dreaming, sleep-time consolidation, Zep thread summary and Mem0 fact extraction in small tests.

## Not done, and why

- Rows waiting on numbers stay waiting: storing Mem0 facts and decisions, Cognee vector indexing and temporal search, Graphiti add_triplet and bulk, the MCP tools search_nodes, search_memory_facts, add_triplet and add_memory extraction, summary embedding.
- Cognee's translated event flow hangs Timestamps on Event nodes, not on the text chunk, so `get_temporal_anchors` finds no chunk for a date window; the chunk join is open.
- Letta dreaming edits blocks in the DuckDB store; it does not write the git memory repository (row 10).
- Not run: Cognee conflict review, feedback weights and session promotion; the json episode kind.
- Without numbers Graphiti's searches by similarity are replaced by name-word overlap and by shared entities; the reply of each ingest says which condition ran.

## Next

Rerun rows 13 and 20 when numbers exist (candidate search by meaning) or on a stronger model; give the user-summary update a step that retires an old preference; join the event Timestamps to the chunk; wire dreaming to the git repository.

## Notes

- Scratch helpers, Modelfiles and upstream clones were in the session scratch folder and are deleted. `local/proof/model_proof_20261023.duckdb` (never committed) holds the proof database and its settings; `local/proof_variants/` was deleted.
- The service is stopped. The models stay in Ollama: `ornith_35b_a3b_cpu`, `qwen38_4b_distill_cpu` and the earlier ones.
- `python -c` was not used.
