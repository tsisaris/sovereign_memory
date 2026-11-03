# What DSPy will connect to (DSPy itself is not built)

Written 2026-11-02. DSPy is the later item in the build order. This page records exactly where it will attach to the memory service, so that building it is wiring and not discovery. Nothing here installs, imports or calls DSPy.

Shawn's words (2026-10-23 16:29:15): the memory suite is plumbed together "With DuckDB only and with one doorway" and "it goes through DSPY." DSPy is a tool that improves prompts from collected data. The plan (`docs/sovereign_memory_plan.md`, the prompt-learning paragraphs) says DSPy "is not another storage engine", that it starts from recorded runs, and that it never opens the live database itself. So every attachment below is through the doorway.

## The doorway

The one Unix socket named by the setting `service.socket_path` (live: `local/service/sovereign_memory.sock`). One request per connection: 4 bytes of length (big-endian), then that many bytes of JSON with `"op"`. The caller's identity comes from the socket, never from the request. The ecosystem's own client for it is `ServiceDoor` in `~/sovereign/shared/shared_memory_router.py`.

## What DSPy reads (the examples and the score)

All through the `ground_truth_table` operation: `{"op": "ground_truth_table", "table": <name>, "limit": <n>}`. It returns the columns and the rows, leaving out anything the caller may not see.

| Table or view | What a row is | Use by DSPy |
|---|---|---|
| `memory_gt_known` and `memory_gt_current_known` | A test question, its right answer in words, the events that state it, who said it and why | The examples: question in, right events out |
| `memory_gt_cases`, `memory_gt_case_events`, `memory_gt_outputs` | One search that was recorded: each reader's ranking, the keyword order, the merged order and the order after the second stage | What the program produced, to be scored |
| `memory_gt_runs` | The settings in force and the reports of each run | Which settings produced which result |
| `memory_gt_ranking_items`, `memory_gt_ranking_scores` | One row per ranked item; per ranking, the first right rank and the reciprocal rank | The metric (mean reciprocal rank is the declared `ranking.comparison_score`) |
| `memory_model_runs` | One language-model step run through the doorway: part, step, opinion, model asked for, model that answered, seconds, arguments, output or error, events named, who asked | The traces of every model step: examples of inputs and outputs for each step, and which steps fail or run slowly |

A correction of a known answer is a new row through `record_known`, and the scoring views recompute against it with no rewrite.

## What DSPy would optimise (the programs)

Every step that needs a language model runs through `{"op": "model", "part": ..., "step": ..., "args": {...}, "opinion": "main" or "second"}` and nowhere else. The prompts those steps send are written inside the upstream code the packages carry, not in this repository's settings (Law 37):

| Part and step | Where its prompts are |
|---|---|
| graphiti `ingest`, `add_triplet` | `packages/graphiti_duckdb/src/graphiti_core/prompts/` (`extract_nodes.py`, `extract_edges.py`, `dedupe_nodes.py`, `dedupe_edges.py`, `summarize_nodes.py`) |
| graphiti `communities`, `saga_summary` | the same folder (`summarize_nodes.py`, `summarize_sagas.py`) |
| mem0 `extract_facts` and the update decisions | `packages/mem0_duckdb/src/mem0/configs/prompts.py` |
| zep `thread_summary` | `DEFAULT_SUMMARY_PROMPT` in `packages/graphiti_duckdb/src/graphiti_duckdb/zep_summarizer.py`; `user_summary` uses Graphiti's entity-summary prompt |
| letta `compact`, `consolidate`, `dream` | `packages/letta_memory_model/letta_memory_model/summarizer/prompts.py`, `sleeptime/prompts.py`, `sleeptime/dream_prompt.py` |
| cognee `extract_graph`, `extract_events`, `complete` and the rest | `packages/cognee_duckdb/cognee/infrastructure/llm/prompts/` |

## The model DSPy talks to

The one `language_model` setting: `language_model.base_url` plus `/v1` (an OpenAI-style door), `language_model.model` (main) and `language_model.second_opinion_model`. DSPy's language-model object should be pointed at that same address and model so that an optimised prompt is judged on the model that will run it. The model that really answered is recorded in `memory_model_runs.achieved_model` for every run.

## What is not built, and so what building DSPy must add

1. The optimiser and its schedule (bounded batches, never in the foreground path of a request).
2. A place for an accepted optimised instruction and its version, and the reading of it by the steps above. The prompts live in upstream code, so each step needs one declared override seam (a setting that names the accepted artifact for a part and step); none exists yet, and the run record already has the columns to say which version a run used once it does.
3. The rule for accepting an artifact: it replaces the current one only if it scores better on held-back known answers, as combiners already do (`memory_combiners.py`).

One open conflict is recorded, not decided: Law (SA_40) says every language-model request goes through Agent Consensus, while the memory service calls its local model server directly through the `language_model` setting. This page changes neither.
