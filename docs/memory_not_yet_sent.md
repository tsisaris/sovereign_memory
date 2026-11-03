# Who produces something worth remembering and does not yet send it

Measured 2026-11-02. How to send is in `docs/how_an_agent_sends_memory.md`. Each owner's own build goal should include the line for its program.

## What was measured

- The live store holds 9 events, all test events from one user (`os-uid-1000`, event types `note`): no agent has ever sent anything to it. (Read through the `ground_truth_table` and `load` operations, and the earlier measurement of the database.)
- Code that calls the memory router today, found by searching the agents' source for `MemoryRouter`, `shared_memory_router` and `shared_save` (the code trees `~/sovereign/agent_*/src`, `~/sovereign/shared`, `~/portable/projects/*/src`): `agent_jurisprudence` (`M_8_A_5.py` the decision door, `M_0_A_1.py` boot), `agent_compute` (`M_1_B_1.py`, `M_1_C_3.py`), `agent_ideation` (`M_2_A_2.py`), `agent_incubator` (`M_2_A_2.py`), and `shared_agent_client.py`. Whether their events reach this service was not measured; none is in the live store.
- Each agent's project document declares per node whether it `emits_memory_events` and of which types. The extraction below reads those declarations (`~/sovereign/agent_*/*_project_document.md` and `~/portable/projects/agent_*/*_project_document.md`).

## Agents whose declared memory events nothing sends yet

Name, node, what it produces (its declared event types). Code that calls the router is marked "calls the router"; the rest has no call at all.

| Agent (path) | Node | Produces |
|---|---|---|
| agent_attorney (`~/sovereign/agent_attorney`, `~/portable/projects/agent_attorney`) | M_7_A_1 Ruling capture | corrections and preferences |
| agent_corpus (`~/sovereign/agent_corpus`, `~/portable/projects/agent_corpus`) | M_4_B_1 Record a Ruling; M_6_D_1 Answer-Key Calibration | correction; calibration_measured |
| agent_doc_pipeline (`~/sovereign/agent_doc_pipeline`) | M_7_E_2 Artifact Index Writer; M_9_C_2 Lowest-Accuracy Reporter; M_9_F_1 Memory Event Emitter | observation; observation; correction and fact |
| agent_ideation (`~/portable/projects/agent_ideation`; calls the router in `M_2_A_2.py`) | M_2_A_2 Decision Recorder | preference, correction |
| agent_incubator (`~/portable/projects/agent_incubator`; calls the router in `M_2_A_2.py`) | M_2_A_2 Decision Recorder | correction |
| agent_ivr_navigator (`~/sovereign/agent_ivr_navigator`, `~/portable/projects/agent_ivr_navigator`) | M_2_A_1 LLM Graph Extractor | observation |
| agent_jurisprudence (`~/sovereign/agent_jurisprudence`; calls the router in `M_8_A_5.py`) | M_8_A_5 Serve /decide | correction |
| agent_knowledge_agent (`~/sovereign/agent_knowledge_agent`, `~/portable/projects/agent_knowledge_agent`) | M_0_B_1 Disk Governor; M_1_A_3 Capability Package Installer; M_1_B_2 Ratification Gate; M_4_C_1 Coverage Reconciler; M_8_C_1 Job Worker | decisions and results of each (event types listed in its document) |
| agent_llm_exporter (`~/sovereign/agent_llm_exporter`) | M_6_A_4 Four-Tier Event Publisher | observation, correction, inference |
| agent_pcb (`~/sovereign/agent_pcb`, `~/portable/projects/agent_pcb`) | M_2_A_1, M_2_B_2, M_3_C_1, M_4_C_1, M_4_D_1, M_7_B_1, M_7_B_2, M_10_C_1, M_11_D_2 | intent revisions, constraint conflicts, scores, overrides, locks, verification and swap decisions, frozen netlists, convergence, release packages |
| agent_validator (`~/sovereign/agent_validator`, `~/portable/projects/agent_validator`) | M_4_A_2 Answer Authority, Ruling and Learning Record | validator_judgment_accepted |
| agent_compute (`~/portable/projects/agent_compute`; calls the router in `M_1_B_1.py`, `M_1_C_3.py`) | M_1_A_2 Provider Terms Recorder; M_1_B_2 Price Book Keeper; M_2_A_2 Agent Job Approval Gate; M_2_B_1 Placement Chooser | fact, inference; fact; preference; inference |

The other agents (`agent_architect`, `agent_coder`, `agent_consensus`, `agent_embedding`, `agent_github`, `agent_social_media`, `agent_uagent_builder`, `agent_project_manager`) declare no memory-emitting node in their project documents, though each produces records worth remembering (see the next table); their owners should rule on it.

## Programs that are not agents and produce something worth remembering

| Program or source | Path | Produces | Sends? |
|---|---|---|---|
| Voice archive (timer `voice-archive.timer`) | `~/dictate_transcripts.txt` (4.9 MB, written 2026-11-02) and `~/Downloads/WHAT_SHAWN_SAID_20260901_20261101.md` | everything Shawn said, with times | no |
| Claude Code sessions | `~/.claude/projects/*/*.jsonl` (484 files, 1.4 GB, 42 projects) | every window's conversation and decisions | no |
| Handoffs | `~/portable/projects/*/handoffs/`, `~/sovereign/handoffs/` (111 files) | what each window changed and left | no |
| Rulings and records | `~/portable/projects/architect/rulings/`, `~/sovereign/records/` | the decisions that govern work | no |
| Architect reports | `~/portable/projects/architect/reports/` | measurements and finish reports | no |
| Agent Consensus run records | `~/sovereign/agent_consensus/logs/`, `~/sovereign/agent_consensus/sovereign.jsonl` | every language-model request and answer through the ecosystem | no |
| Task record (timer `task-record.timer`) and leftover check (`leftover-check.timer`) | portable_system `task_record.py`, `leftover_judgment.py` | what each task did and what was left | no |
| Decision log | `~/portable/projects/portable_system/src/decision_log.py` | decisions of the checkpoint and gate | no |
| Jules loop packets | `~/portable/projects/*/local/packets/`, `jules_*` programs in portable_system | what each Jules session returned and why a check was red | no |

"Sends? no" means a search of that program's code finds no call to the memory door and the live store holds none of it. Each owner adds its line to its own build goal; nothing here loads anything into the live store.
