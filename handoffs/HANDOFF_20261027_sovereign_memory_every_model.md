# HANDOFF 2026-10-27 — sovereign_memory: every Embedding model, weights, second stage, batch loading

## What changed

- Seven Embedding readers registered and fresh; combiner `rrf-all-embedding-models` adopted by the owner's ruling (`python -m sovereign_memory adopt-combiner`).
- Question and passage words per model are the store setting `embedding_door.prefixes` (the door adds none).
- Search answers carry each reader's own order, the merged order, the second stage (`rerank`) and the facts held back as no longer true (`not_current`).
- `retrieve_batch` (socket operation): the numbers of all questions are fetched once per model, so the door loads each reader once per batch.
- Store settings added: `ranking.model_weights`, `ranking.words_weight`, `ranking.rerank_capability`, `ranking.rerank_candidates`, `ranking.rerank_weight`. Weights are equal (1.0) for now. To be learned: once hand-verified ground-truth questions exist, learn the weights with ranx's optimiser against qrels (ranx MIT, `optimize_fusion`); the starting rerank values (8 candidates, weight 0.6) are guesses to be tuned the same way.

## Ground-truth store (Shawn's ruling 13.1: memory starts learning; no decision model yet)

- Built on the existing `memory_gt_*` tables, in the memory database. New: `memory_gt_known` (the answer known to be right for a test question, as the events that state it; append-only, a later row supersedes, so every correction from Shawn is a new row with `said_by`, `basis`, `recorded_by`, `recorded_at`) and `memory_gt_runs` (the settings in force and the reports of each run: cross-scores, keyword ranking, decay, facts held back, models used or left out; no event text).
- Every search of a known question, and every disagreement, now records a case holding each reader's ranking, the keyword ranking (`words`), the merged order (`combined`) and the order after the second stage (`reranked`), each item with its event, kind and score (cross-score for the second stage).
- Views, recomputed from the stored rankings against the current known answer, so a correction re-scores everything with no rewrite: `memory_gt_current_known`, `memory_gt_ranking_items` (one row per ranked item) and `memory_gt_ranking_scores` (per ranking: first right rank, right first, reciprocal rank).
- Socket operations: `record_known` (add or correct a known answer) and `ground_truth_table` (any ground-truth table or view, whole, filtered to what the caller may see). The Ada set of eight questions is recorded as known by construction (`basis` "construction").
- Not built, by the ruling: the decision model (designed after agent_uagent_builder's prior-art research). The data is complete and exposed for it and for AutoML; the hand verdicts of the review screen stay in `memory_gt_judgments`.

## Measured

- Batch of three questions: 206 s and 368 s on two runs (a lone question took 700 to 824 s before).
- Cats, personalization tier, rerank weight 0.6: 'Has three cats' first. At 0.5 the fusion ties and 'Name is Ada' keeps first place on the tie-break.
- Work and manager questions: 'Name is Ada' stays first in the personalization tier for every reader and for the cross-scorer. Not fixed.
- Work, temporal tier: two ended facts are held back; the old note (an episode) 'joined the Bergen Marine Institute' stays first because episodes carry no validity.

## For the toolkit window

1. Declare the environment: no project declares `sovereign_memory`, so the Law 11 hook refuses `uv pip install` against it (`.copier-answers.yml` and the declaration name `sovereign_tools`). Run the template update for this project (Copier) or `project_tool.py environment set sovereign_memory sovereign_memory`; it was not run here because it is the toolkit's and an update may rewrite governed files. After it: install `ranknexus==0.1.0` (Apache-2.0; weighted reciprocal rank fusion; ranx 0.3.21 has no weighted reciprocal rank fusion) with uv alone, declare it, `uv lock`, and replace `reciprocal_rank_fusion` in `memory_combiners.py` by the library call.
2. `env-guard venvs` refuses `.venv` in the project root, dated 2026-10-21, not made in this session. Left in place; the owner or the toolkit decides.
3. The four doorway-install gaps of the 2026-10-24 handoff still stand (fixed newer-modules folder, non-repeatable tenancy step, no retire step for `shared_memory_anatid.py`, no port-registry declaration).

## For the Embedding window

- The door lists no Embedding model memory cannot reach (bge_m3 and dinghy_law are served).
- The door holds one card reader at a time (25 to 70 s a swap); nomic answered 500 once and recovered; bge_m3 refused 23,600 characters (409).
- The door adds no question or passage words.

## Disclosures

- `python -c` was used once by mistake in an earlier round.
- Not built: Letta archival search, Graphiti node matching and `add_triplet`, Cognee other search types, Zep summary embedding, Mem0 time search, per-model rebuild (row 44), a validity for episodes.
- The service `agent-sovereign-memory.service` is running; parakeet was not touched; nothing under `~/sovereign` was written.
