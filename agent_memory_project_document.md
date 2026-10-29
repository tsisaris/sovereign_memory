# Agent Memory — Project Document
agent_id: agent_memory
agent_name: Agent Memory
agent_directory: ~/sovereign/agent_memory/
mission: Answer every agent's questions from the memories it has captured, ranked by every registered Embedding reader.
build_mode: BUILD

## GOVERNING DOCUMENTS
- SOVEREIGN_LAWS.md
- SOVEREIGN_ARCHITECTURE.md
- Universal_MultiModule_System_Template.md
- SOVEREIGN_SHARED_LIBRARY.md
- SOVEREIGN_PROMPTS.md

## INVARIANTS

The invariants UMST_37 declares are inherited and enforced at every node. The set is read from that block and is never restated here: a range or a count written out in prose is a second copy that stops at the day it was written (Law 37).

Agent-specific invariants:
agent_memory:INV-001: One process owns the memory database file for its whole life; no other process writes it.
         check: while the service runs, a second process opening the file, even read-only, is refused by the database lock
         severity: HARD

agent_memory:INV-002: Capture is acknowledged only after the event, its verified payload bytes and one pending receipt per configured tier are committed together.
         check: after a capture reply, the event row, its payload row and one pending receipt per tier exist, and a capture interrupted before commit leaves none of them
         severity: HARD

agent_memory:INV-003: A receipt says processed only after real processing, and each projection change commits with its own receipt on the same connection.
         check: no receipt is processed while its tier's projection of that event is absent, and a failed apply leaves the projection rolled back and the receipt in a failure state
         severity: HARD

agent_memory:INV-004: Memory never computes numbers, never cuts text and never loads an embedding model; every number comes from the supplier's door.
         check: with the supplier stopped, a relational or personalization capture ends retryable with "numbers not yet supplied" and no number is stored
         severity: HARD

agent_memory:INV-005: No model is named in code; every model is a registered row of data.
         check: the model registry lists only rows that came from the stored settings or the register operation, and removing a row removes the model from every ranking
         severity: HARD

agent_memory:INV-006: Memory never deletes an original; forgetting and expiry withhold the event from every tier, index and answer.
         check: after forget, the event is absent from every retrieve, context and review answer, and its original payload row is still present
         severity: HARD

agent_memory:INV-007: Every search answer reports the combiner, the models used and each model left out with its reason; there is never an invented ranking or an empty success.
         check: a search with the supplier stopped answers unavailable with each model's reason, never ok with no hits
         severity: HARD

agent_memory:INV-008: A combiner replaces the current one only when it scores strictly better on held-back judged cases, or by the owner's recorded ruling.
         check: every change of the current combiner has a decision row naming either the held-back scores or the owner's ruling
         severity: HARD

agent_memory:INV-009: Ground truth is append-only; a correction is a new row that supersedes, never an edit.
         check: no update or delete statement runs against a memory_gt table, and a corrected known answer leaves the earlier row in place
         severity: HARD

agent_memory:INV-010: Every request carries the caller's identity from the operating system's peer credentials, and the same visibility constrains every tier and every result.
         check: a request from a second operating-system user receives none of the first user's restricted events from any tier, context block, review screen or export
         severity: HARD

agent_memory:INV-011: No tunable number is written into code; every setting is a described row of the memory database with its change history and no code default.
         check: every tunable value a node uses is read from memory_runtime_settings, and a missing required setting stops the start with exit code 2
         severity: SOFT

agent_memory:INV-012: Language-model work runs outside any write transaction, and the model that actually answered is recorded.
         check: no write transaction is open while a model call is in flight, and every model step's reply names the achieved model
         severity: HARD

agent_memory:INV-013: A fact that its graph has ended before the question's time is held back from the answer and reported as no longer current.
         check: after a contradiction ends a fact, a question asked later does not rank that fact and lists it under not_current
         severity: HARD

## ARCHITECTURE OVERVIEW

Agent Memory is the one place the ecosystem keeps what its agents remember. Any agent sends it a memory event; it stores the event durably with its original bytes, hands it to four memory engines (Letta for episodes, Graphiti and Zep for dated facts, Cognee for documents, Mem0 for personal facts), and answers questions with the memories that fit, ranked by the numbers every registered Embedding reader gives them. It reads only the events it is given and the numbers agent_embedding computes; computing numbers, cutting text, choosing what work to do and training decision models belong to agent_embedding, agent_corpus, the agent that asks, and agent_uagent_builder.

M_0 starts the service from the settings stored in its own database file and makes it known to the ecosystem. M_1 receives events over a local socket, validates them, stores them with one pending receipt per memory engine, and propagates forgetting and expiry. M_2 hands each owed event to its engine and records an honest receipt, and routes language-model steps. M_3 to M_6 are the four engines, each running on the one DuckDB file through the shared asynchronous dialect. M_7 receives each reader's numbers from agent_embedding's door; M_8 asks every engine, ranks with every reader, merges the rankings with the keyword ranking, applies the second-stage cross-scorer and the time-validity hold-back, and reports how each answer was made; M_9 keeps the ground truth and the review screen; M_10 holds the growth capabilities not yet built; M_11 is the socket and health interface.

Module summary:
- M_0 — Infrastructure: start, settings, presence and the database foundation
- M_1 — Event Capture and Lifecycle: durable capture, removal and expiry
- M_2 — Engine Dispatch: honest receipts per engine and the model-step router
- M_3 — Episodic Memory (Letta): memory blocks, recall, archival passages, compaction and sleep-time work
- M_4 — Temporal Memory (Graphiti and Zep): episodes, dated facts, contradiction, communities, sessions and summaries
- M_5 — Relational Memory (Cognee): document pieces, graph extraction, permissions and event timelines
- M_6 — Personalization Memory (Mem0): scoped personal facts with consolidation and history
- M_7 — Numbers From Embedding: the model registry and every reader's numbers
- M_8 — Search and Ranking: per-reader rankings merged, reranked, held back and reported
- M_9 — Ground Truth and Review: known answers, recorded rankings, judgments and exports
- M_10 — Growth Capabilities: foresight, skills and boundary detection, not yet built
- M_11 — Service Interface: socket operations and the health door

Key data flows:
- agent -> socket capture -> M_1_B_2 -> event, payload and pending receipts -> M_2_A_2 -> each engine's tier -> projection with its receipt
- agent_embedding door -> M_7_B_1 -> M_7_A_2 -> one numbers table per reader
- agent -> socket retrieve -> M_8_A_2 -> every tier ranked per reader -> M_8_B_1 -> M_8_B_3 -> M_8_B_4 -> answer with its report -> M_9_A_1 ground-truth case
- maintenance timer -> M_0_A_3 -> expiry, numbers sync and owed work
- HTTP GET /health -> M_11_A_2 -> health report

## DEPENDENCIES

Agent Dependencies:
- agent_embedding — every registered reader's numbers for each memory and each question, the keyword ranking and the cross-scores, through its door
- agent_corpus — the alternative number supplier and the one that will cut text and recompute numbers; its hand-over door is not built yet
- agent_uagent_builder — the future trainer of decision models over the ground truth memory records; memory trains nothing
- agent_consensus — the ecosystem's route for language-model calls; memory's model steps today call a local Ollama model named by its language_model setting directly, which this document records as it is

Shared Library Dependencies:
- shared_logger — all logging
- shared_agent_registry — registration, heartbeat and deregistration
- shared_redis_bus — the registry's connection
- shared_service_supervisor — claiming the health port and replacing an earlier copy of this service
- shared_schema_validator — imported by the registration path for its error type

Infrastructure Dependencies:
- Redis: yes — registry registration and heartbeat only; memory subscribes and publishes no channel of its own
- DuckDB: yes — one memory database file holding the owner's event, receipt, payload, removal, settings, numbers, combiner and ground-truth tables and each engine's schema (letta, graphiti, zep_ce, Cognee's tables and dataset schemas, Mem0's collections and history)
- GPU: no — memory loads no model and allocates no card memory; the readers run inside agent_embedding

## DATA CONTRACTS

Inputs:
memory_socket_request.json — from any local process via the Unix socket, one per connection, framed as a 4-byte big-endian length then UTF-8 JSON (M_1_A_2)
  op: string, optional — one of capture, retrieve, retrieve_batch, record_known, ground_truth_table, forget, status, models, context, review_list, review_show, review_page, review_judge, review_select, model_parts, model, load, record_claim, record_get; absent means capture
  event: object, required for capture — the memory event envelope (M_1_B_1)
  payload_content_hex: string, optional — the payload bytes as hexadecimal
  query: string, required for retrieve, context and record_known — the question
  queries: array of string, required for retrieve_batch — the questions
  limit: integer, required for retrieve, retrieve_batch, context and ground_truth_table — at least 1
  event_id: string, required for forget, status and load — the event named
  answer, basis, said_by: string, required for record_known — the known answer, its basis and who says so
  events: array of string, required for record_known and review_select — event ids
  table: string, required for ground_truth_table — one of the nine ground-truth tables and views
  case_id: string, required for review_show, review_page and review_judge — the case
  verdict: string, required for review_judge — right, wrong, pick, typed or none_correct
  chosen_items: array of string, optional for review_judge — item ids
  typed_answer, note: string, optional for review_judge — the typed answer and a note
  question: string, required for review_select — the question of the case
  memory_kind: string, required for review_select — episodic, temporal, relational or personalization
  key, kind, writer, content: string, required for record_claim (key alone for record_get) — the router record
  episode, episode_source: string, optional for record_claim — the record's episode
  part, step: string, required for model — the language-model part and its step
  args: object, optional for model — the step's arguments
  opinion: string, optional for model — main (default) or second
machine.json — at the protocol root, two folders above AGENT_ROOT; read by M_0_C_1 only when SOVEREIGN_ROOT is not given
  sovereign_root.value: string, required — the Sovereign tree's path
memory_event.json — from any local agent via the Unix socket (op capture, field "event")
  schema_version: string, required — "1.0.0" for legacy, 2.x.y for the version 2 envelope
  event_id: string, required — the event's permanent identity; reuse with different content is refused
  source_artifact_id: string or null, required — the artifact the memory came from (both versions)
  agent_id: string, optional — version 1 only, where it is required; overwritten by the authenticated principal
  tier: string, optional — version 1 only, where it is required; episodic, temporal, relational or personalization, the one tier a legacy event is owed to
  content: string, optional — version 1 only, where it is required; the memory text, stored as the payload
  content_type: string, optional — version 1 only, where it is required; fact, observation, correction, inference or preference
  ttl_seconds: integer or null, optional — version 1 only, where it is required; a positive value expires the event
  entity_refs: list of string, optional — version 1 only, where it is required; entities the memory is about
  confidence: number, optional — version 1 only, where it is required; 0 to 1
  source: string, optional — version 1 only, where it is required; observation, inference, human_correction or llm_output
  timestamp: string, optional — version 1 only, where it is required; ISO 8601 creation time
  actor_agent_id: string, optional — version 2 only, where it is required; overwritten by the authenticated principal
  event_type: string, optional — version 2 only, where it is required; the "system." prefix is reserved for the owner
  created_at: string, optional — version 2 only, where it is required; creation time
  work_id: string or null, optional — version 2 only, where it is required; the work the event belongs to
  parent_event_id: string or null, optional — version 2 only, where it is required; lineage
  causation_event_id: string or null, optional — version 2 only, where it is required; the event that caused this one (the target, for an owner removal)
  source_revision: string, optional — version 2 only, where it is required; the source's revision
  recipe_version: string or null, optional — version 2 only, where it is required; provenance
  model_occupant_id: string or null, optional — version 2 only, where it is required; provenance
  program_version: string or null, optional — version 2 only, where it is required; provenance
  payload_ref: string, optional — version 2 only, where it is required; must be "inline"
  payload_sha256: string, optional — version 2 only, where it is required; 64 lowercase hexadecimal characters, the SHA-256 of the payload bytes
  outcome: string, optional — version 2 only, where it is required; the event's outcome
  exactness: string, optional — version 2 only, where it is required; observed, derived or inferred
  tier_receipts: object, optional — version 2 only, where it is required; all four tiers, each receipt with event_id, processor_id, processor_version, projection_version, status, attempt_id, attempted_at, completed_at and error_ref; every state is stored as pending
socket_request.json — from any local agent via the Unix socket, framed by a 4-byte big-endian length, at most service.max_request_bytes
  op: string, optional — one of the 19 operations; missing means capture
  event: object, required for capture — a memory_event.json envelope
  payload_content_hex: string, optional — the payload bytes in hexadecimal
  event_id: string, optional — required for load, status and forget; the event named
  key: string, optional — required for record_claim and record_get; the bookkeeping note's key
  kind: string, optional — required for record_claim; what the note is
  writer: string, optional — required for record_claim; who wrote it
  content: string, optional — required for record_claim; the note's text
  episode: string, optional — record_claim only; the note's episode
  episode_source: string, optional — record_claim only; the episode's source
  part: string, optional — required for model; the part to run
  step: string, optional — required for model; the step to run
  args: object, optional — model only; the step's arguments
  opinion: string, optional — model only; main (the default) or second
memory_event_schema.json — read at start from service/schemas beside the code
  definitions.legacy_v1: object, required — the version 1 shape, no extra fields
  definitions.envelope_v2: object, required — the version 2 shape, no extra fields
  definitions.receipt: object, required — the nine receipt fields and the seven receipt states
supplied_piece.json — from agent_corpus or the Embedding door, through the owner's numbers store (memory_pieces), read by M_5_B_1 and M_6_A_2
  piece_id: string, required — the piece's permanent identity
  event_id: string, required — the captured event the piece was cut from
  text: string, required — the piece's text, cut by the supplier
  position: integer, required — the piece's place in its event
  text_fingerprint: string, required — sha256 of the text
  source: string, required — event (a captured event's piece) or derived (a Mem0 memory text)
mem0_capture_payload.json — from any agent via the socket capture operation (version 2 payload of a personalization event), read by M_6_A_2
  messages: list of {role: string, content: string}, required — the conversation, non-empty
  infer: boolean, optional — false stores each message as written; true (the default) extracts facts
model_step_request.json — from any caller via the socket model operation, read by M_5_B_2, M_5_B_3, M_6_B_1, M_6_B_2 and M_6_B_3
  part: string, required — cognee or mem0
  step: string, required — one of the part's steps (cognee: extract_graph, extract_events, store_events, attach_event_entities, query_window, date_anchors, rewrite_entity_description; mem0: extract_facts, decide_actions, procedural, graph_triplets, graph_delete_decision)
  args: object, required — the step's arguments
  opinion: string, optional — main (the default) or second
retrieve_request.json — from any agent via the local Unix socket (one framed request)
  op: string, required — "retrieve"
  query: string, required — the question
  limit: integer, required — at least 1
retrieve_batch_request.json — from any agent via the local Unix socket
  op: string, required — "retrieve_batch"
  queries: list of string, required — non-empty, every entry text
  limit: integer, required — at least 1
context_request.json — from any agent via the local Unix socket
  op: string, required — "context"
  query: string, required — the question
  limit: integer, required — at least 1
supplied_numbers.json — from a supplier client (M_7_B_1 or M_7_B_2) to the numbers receiver (M_7_A_2), in process
  model_id: string, required — the model the numbers belong to
  revision: string, required — its revision
  width: integer, required — numbers per piece; must equal the registered width
  pieces: list of object, required — piece_id, event_id, position, text, text_fingerprint (SHA-256 of the UTF-8 text), source (event or derived)
  numbers: object, required — piece_id to a list of exactly width finite numbers, one per piece
  retires: list of string, optional — for a re-cut only, exactly the event's current piece ids
embedding_capabilities_reply.json — from agent_embedding GET /capabilities
  capabilities: list of object, required — capability_id, occupant_id, revision, dim (integer width)
embedding_embed_reply.json — from agent_embedding POST /embed
  model_id: string, required — the occupant that answered; must equal the one asked for
  revision: string, required — its revision; must equal the one asked for
  dim: integer, required — the width of every vector
  vectors: object, required — passage id to a list of dim numbers
embedding_feature_reply.json — from agent_embedding POST /feature/{capability}
  values: object, required — for pairs.cross_score, candidate id to a number; for words.bm25, "question" to an object of candidate id to score
corpus_catalogue_reply.json — from agent_corpus GET /saved_numbers/models
  catalogue: list of object, required — model_id, revision, width, last_saved
  reason: string, optional — why the catalogue is empty
corpus_saved_numbers_page.json — from agent_corpus GET /saved_numbers (collection_id, model_id, revision, after, requester_agent_id)
  numbers: list of object, required — passage_fingerprint, unit_id, vector, width
  next: string, required — the next page's cursor, or null on the last page
corpus_question_reply.json — from agent_corpus POST /question_numbers
  numbers: list of object, required — model_id, revision, vector
  unreached: list of object, optional — model_id, reason

Outputs:
memory_socket_reply.json — to the caller via the Unix socket, plain JSON with no length prefix, then the connection closes
  status: string, required — success or error
  message: string, required on error — Payload Too Large, Invalid JSON payload, Malformed Hex Payload, Unsupported operation, Missing 'event' in payload, Authentication Error, Configuration Error, Visibility Refused, Validation Error or Internal Error
  details: string, required on error — the reason
  (operation fields): required on success — the fields each operation answers, as listed in M_11_A_1
health_report.json — to any caller via HTTP GET /health
  status: string, required — ok or starting
  service: string, required — sovereign_memory
  socket: string, required — the socket path
  database: string, required — the database path
  parts: array of string, required — the language-model part names, empty when none
  agent_id: string, required — agent_sovereign_memory today
  registration: string, required — the latest registration note
agent_registration.json — to shared_agent_registry over the shared Redis bus, at the first successful attempt (M_0_C_2)
  agent_id: string, required — agent_sovereign_memory today
  name: string, required — sovereign_memory
  version: string, required — the installed package's version
  description: string, required — one line
  capabilities: array of string, required — memory.capture, memory.retrieve, memory.context, memory.forget, memory.model_steps
  http_endpoints: array of object, required — [{path /health, method GET, description}]
rebuild_result.json — printed on standard output by the command router's rebuild route (M_0_A_2)
  request_id: string, required — the rebuild's id
  tier: string, required — relational or personalization
  model_labels: array of string, required — the models whose numbers the rebuilt tier uses
  status: string, required — completed, or any other state (exit code 1)
capture_reply.json — to the capturing agent via the Unix socket, plain JSON with no length prefix
  status: string, required — "success"
  event: object, required — the stored envelope with its durable tier_receipts
  created: boolean, required — true for the call that wrote the event, false for an identical repeat
error_reply.json — to any socket caller
  status: string, required — "error"
  message: string, required — Payload Too Large, Invalid JSON payload, Malformed Hex Payload, Unsupported operation, Missing 'event' in payload, Authentication Error, Configuration Error, Visibility Refused, Validation Error or Internal Error
  details: string, required — the cause
receipt_status.json — to the caller of status or forget
  status: string, required — "success"
  event_id: string, required — the event reported (for forget, the removal event)
  removed_by: string or null, required — the removal event that removed it
  receipts: object, required — tier to receipt (processor_id, processor_version, projection_version, status, attempt_id, attempted_at, completed_at, error_ref)
  complete: boolean, required — true only when every receipt is processed or processed_no_new_fact
loaded_event.json — to the caller of load
  status: string, required — "success"
  found: boolean, required — false for an unknown event
  event: object, optional — the envelope with its current receipts, when found
  payload_text: string, optional — the original bytes decoded as UTF-8, when found
router_record.json — to the caller of record_claim or record_get
  status: string, required — "success"
  created: boolean, optional — record_claim only; whether this call wrote the record
  found: boolean, optional — record_get only; whether the record exists
  record: object, required — record_key, kind, writer, content, episode, episode_source, created_at (null when not found)
removal_payload.json — stored as the payload bytes of every owner-authored system.forget or system.expire event
  operation: string, required — forget or expire
  target_event_id: string, required — the event removed
  target_actor_agent_id: string, required — the removed event's producer
  requested_by: string, optional — forget only; the principal who asked
  ttl_seconds: integer, optional — expire only; the elapsed time to live
  reason: string, required — "forgotten by its producer" or "ttl_seconds elapsed"
model_step_reply.json — to the caller of model
  status: string, required — "success"
  part: string, required — the part that ran
  step: string, required — the step that ran
  opinion: string, required — main or second
  requested_model: string, required — the model the settings named
  achieved_model: string or null, required — the model the server reported answering
  placement: object, required — loaded, size_bytes, size_in_graphics_card_bytes and processor, or loaded false with unknown_because
  seconds: number, required — time the step took
  step_output: object, required — the step's own output fields, merged into the reply beside the fields above
model_parts_report.json — to the caller of model_parts
  status: string, required — "success"
  requested: object, required — main and second model names from the settings
  main_model_placement: object, required — where the server holds the main model
  parts: object, required — part name to steps and achieved_model
letta_block.json — written by M_3_A_1 (and by M_3_B_3 through the editing tools) to letta.block in the memory database; read by M_3_A_1, M_3_A_4, M_3_B_2 and M_3_B_3
  id: string, required — the block's id
  organization_id: string, required — the Letta organization
  label: string, required — the block's label, unique per attached agent
  value: string, required — the block's text, never longer than limit
  limit: integer, required — the character limit (Letta's 100000 when not given)
  description: string, optional — what the block is for
  metadata_: JSON object, optional — free-form metadata
  read_only: boolean, required — whether the agent may edit it
  hidden: boolean, optional — whether it is hidden
  is_template: boolean, required — Letta's template flag
  version: integer, required — raised by one on every change; the compare-and-set key
  created_at: timestamp, required — when stored
  updated_at: timestamp, required — when last changed
  is_deleted: boolean, required — Letta's soft-delete flag
letta_blocks_agents.json — written by M_3_A_1 and M_3_B_3 to letta.blocks_agents; read by M_3_A_1
  agent_id: string, required — the agent holding the block
  block_id: string, required — the block attached
  block_label: string, required — the block's label, one per agent
letta_conversation.json — written by M_3_A_1 (seed_agent) to letta.conversations; read by M_3_A_2 and M_3_B_2
  id: string, required — the conversation's id
  organization_id: string, required — the Letta organization
  agent_id: string, required — the agent whose conversation it is
  summary: string, optional — Letta's conversation summary field
  last_message_at: timestamp, optional — time of the last message
letta_message.json — written by M_3_B_1 (one per role message of a captured event), M_3_A_1 (seed_agent) and M_3_B_2 (summary messages) to letta.messages; read by M_3_A_2, M_3_B_1, M_3_B_2 and M_3_B_3
  id: string, required — "message-" plus a uuid5 of the event id and index for captured events
  organization_id: string, required — the Letta organization
  agent_id: string, required — the producing actor
  role: string, required — user, assistant, tool, system, approval or summary
  text: string, optional — the message text
  content: list of JSON object, optional — content parts
  otid: string, optional — the captured event's id for tier messages
  sender_id: string, optional — the producing actor
  conversation_id: string, optional — the conversation, when it has one
  tool_calls: list of JSON object, optional — tool calls the message made
  sequence_id: integer, required — the store's order
  created_at: timestamp, required — when stored
  is_deleted: boolean, required — Letta's soft-delete flag
letta_archival_passage.json — written by M_3_A_3 to letta.archival_passages; read by M_3_A_3
  id: string, required — the passage's id
  organization_id: string, required — the Letta organization
  archive_id: string, required — the archive it belongs to
  text: string, required — the passage text, never blank
  embedding: list of float, optional — numbers the supplier made, stored unpadded; null when none
  embedding_config: JSON object, optional — how the embedding was made
  metadata_: JSON object, required — free-form metadata
  tags: list of string, optional — the passage's tags
  created_at: timestamp, required — when stored
letta_passage_tag.json — written by M_3_A_3 to letta.passage_tags; read by M_3_A_3
  id: string, required — "passage_tag-" plus a random uuid
  tag: string, required — one tag
  passage_id: string, required — the passage tagged
  archive_id: string, required — the passage's archive
  organization_id: string, required — the Letta organization
letta_source_passage.json — written by M_3_A_3 to letta.source_passages; read by M_3_A_3
  id: string, required — the passage's id
  organization_id: string, required — the Letta organization
  source_id: string, required — the source it was cut from
  file_id: string, optional — the file within the source
  file_name: string, required — the file's name
  text: string, required — the passage text
  embedding: list of float, optional — numbers the supplier made
letta_conversation_context.json — written by M_3_B_2 to letta.letta_conversation_context; read by M_3_B_2
  organization_id: string, required — the Letta organization
  conversation_id: string, required — the conversation
  agent_id: string, required — its agent
  system_text: string, required — the system text in context
  message_ids: string, required — the ids now in context, in order
letta_sleeptime_group.json — written by M_3_B_3 to letta.letta_sleeptime_group; read by M_3_B_3
  organization_id: string, required — the Letta organization
  group_id: string, required — the sleep-time group
  foreground_agent_id: string, required — the agent whose turns are reviewed
  agent_ids: string, required — the sleep-time agents
  sleeptime_agent_frequency: integer, optional — turns between runs; null means every turn
  turns_counter: integer, required — turns counted, wrapping at the frequency
  last_processed_message_id: string, optional — the newest message already reviewed
letta_dream_state.json — written by M_3_B_3 to letta.letta_dream_state; read by M_3_B_3
  organization_id: string, required — the Letta organization
  agent_id: string, required — the agent that dreams
  last_reflected_sequence_id: integer, required — the newest message a finished dream read
compact_answer.json — returned by M_3_B_2 to the caller of the model operation
  compacted: boolean, required — whether a summary was made
  mode_used: string, required when compacted — the mode that produced the summary
  fallback_notes: list of string, required when compacted — fallbacks taken
  summary_text: string, required when compacted — the summary
  summary_message_id: string, required when compacted — the stored summary message
  evicted_message_ids: list of string, required when compacted — messages now out of context, still stored
  tokens_before: integer, required — approximate tokens before
  tokens_after: integer, required when compacted — approximate tokens after
  reason: string, required when not compacted — why not
memory_block_file.md — written by M_3_A_4 into an agent's git memory repository under the storage folder; read by M_3_A_4
  frontmatter.description: string, optional — the block's description
  frontmatter.read_only: boolean, optional — whether the block is read-only
  frontmatter.metadata: object, optional — the block's metadata
  body: string, required — the block's text
  path: string, required — the label as a path; skills/<name>/SKILL.md is the block skills/<name>
episodic_hit.json — returned by M_3_B_1 to tier retrieval (M_8_A_2)
  event_id: string, required — the captured event
  item_id: string, required on the numbers path — the message id
  actor_agent_id: string, required — the producing actor
  message_id: string, required — the Letta message
  role: string, required — the message's role
  text: string, required — the message's text
  sequence_id: integer, required — the store's order
  distance: float, required on the numbers path — the event's cosine distance for that model
  ranking: string, required — how the hit was ranked, in words
graphiti_graph_node.json — written by M_4_A_2 (episodes), M_4_B_1 (entities and episodes), M_4_B_3 (communities and sagas) to graphiti.graph_node; read by M_4_A_1, M_4_A_2, M_4_A_3, M_4_B_1, M_4_B_3 and M_4_C_1
  uuid: string, required — the node's uuid; an episode's is a uuid5 of the event id
  label: string, required — Episodic, Entity, Community or Saga
  properties: JSON object, required — the Graphiti model: name, group_id, summary, created_at, and for an episode content, source, source_description, valid_at, entity_edges and the provenance metadata
graphiti_graph_edge.json — written by M_4_B_1 and M_4_B_2 (RELATES_TO facts, MENTIONS links), M_4_B_3 (HAS_MEMBER, HAS_EPISODE) to graphiti.graph_edge; read by M_4_A_1, M_4_A_2, M_4_A_3, M_4_B_1, M_4_B_2, M_4_D_1 and M_4_D_2
  uuid: string, required — the edge's uuid
  source_node_uuid: string, required — the source node
  target_node_uuid: string, required — the target node
  label: string, required — RELATES_TO, MENTIONS, HAS_MEMBER, HAS_EPISODE or NEXT_EPISODE
  properties: JSON object, required — the Graphiti model; for a fact: name, fact, group_id, episodes (first is the creating episode), created_at, valid_at, invalid_at, expired_at
temporal_hit.json — returned by M_4_A_2 to tier retrieval (M_8_A_2)
  event_id: string, required — the captured event the episode or fact came from
  item_id: string, required on the numbers path — the episode or fact uuid
  actor_agent_id: string, required — the producing actor
  content: string, required for an episode — the episode text
  fact: string, required for a fact — the fact's words
  valid_at: string, required — ISO 8601
  invalid_at: string, optional for a fact — ISO 8601, when ended
  expired_at: string, optional for a fact — ISO 8601
  source_revision: string, optional — the event's source revision
  distance: float, required on the numbers path — cosine distance for that model
  ranking: string, required — how the hit was ranked, in words
not_current_fact.json — returned by M_4_A_2 in TierRankings for every fact held back
  item_id: string, required — the fact's uuid
  event_id: string, required — the event the fact came from
  fact: string, required — the fact's words
  valid_at: string, optional — when it began
  invalid_at: string, optional — when Graphiti ended it
ingest_answer.json — returned by M_4_B_1 to the caller of the model operation
  episodes: list of object, required — per event: event_id, fact_numbers_owed, candidates, candidate_facts_shown_per_new_fact, stored_facts_before, entities, facts, ended_facts, and the seconds of each phase
communities_answer.json — returned by M_4_B_3
  communities: list of object, required — name, summary and member names of each community
saga_answer.json — returned by M_4_B_3
  saga: string, required — the saga's name
  summary: string, required — the summary stored on the saga
  episodes_read: integer, required — episodes summarised
  server.transport: string, required — http, stdio or sse
  server.host: string, required — the host to bind
  server.port: integer, required — the port, above 0
  server.semaphore_limit: integer, required — episodes processed at once, above 0
  database.path: string, required — the DuckDB file holding the graph
  graphiti.group_id: string, required — the default group
  graphiti.user_id: string, optional — the user for tracking
  graphiti.entity_types: list, optional — custom entity types
  graphiti.edge_types: list, optional — custom edge types
  graphiti.edge_type_map: list, optional — edge types allowed between entity types
zep_ce_users.json — written by M_4_D_1 to zep_ce.users; read by M_4_D_1 and M_4_D_2
  uuid: UUID, required — the row's id
  user_id: string, required — unique user id
  email: string, optional — email
  first_name: string, optional — first name
  last_name: string, optional — last name
  project_uuid: UUID, required — the Zep project
  metadata: JSON object, optional — merged key by key
  created_at: timestamp, required — when created
  deleted_at: timestamp, optional — soft delete
zep_ce_sessions.json — written by M_4_D_1 to zep_ce.sessions; read by M_4_D_1 and M_4_D_2
  uuid: UUID, required — the row's id
  session_id: string, required — unique session id
  user_id: string, optional — the owning user
  project_uuid: UUID, required — the Zep project
  metadata: JSON object, optional — merged key by key
  ended_at: timestamp, optional — when ended; an ended session refuses changes
  deleted_at: timestamp, optional — soft delete
zep_ce_messages.json — written by M_4_D_1 to zep_ce.messages; read by M_4_D_1 and M_4_D_2
  uuid: UUID, required — the message's id
  id: integer, required — store order
  session_id: string, required — the session
  project_uuid: UUID, required — the Zep project
  role: string, required — the speaker
  role_type: string, required — norole, system, assistant, user, function or tool
  content: string, required — the text
  token_count: integer, required — its token count
  metadata: JSON object, optional — merged key by key; "system" only from a privileged caller
  deleted_at: timestamp, optional — soft delete
zep_ce_graph_owed.json — written by M_4_D_1 to zep_ce.graph_owed when no graph is attached
  seq: integer, required — order recorded
  operation: string, required — add_node or delete_group
  group_id: string, required — the graph group
  detail: JSON object, optional — the step's arguments
  recorded_at: timestamp, required — when recorded
zep_ce_summaries.json — written by M_4_D_2 to zep_ce.summaries; read by M_4_D_2
  uuid: UUID, required — the summary's id
  session_id: string, required — the session
  project_uuid: UUID, required — the Zep project
  content: string, required — the summary text
  summary_point_uuid: UUID, required — the last message folded in
  token_count: integer, required — estimated tokens
  created_at: timestamp, required — when made
zep_ce_user_summary_instructions.json — written by M_4_D_2 to zep_ce.user_summary_instructions; read by M_4_D_2
  project_uuid: UUID, required — the Zep project
  user_id: string, required — the user
  position: integer, required — order set
  name: string, required — unique per user, at most 100 characters
  text: string, required — at most 100 characters
zep_ce_user_summaries.json — written by M_4_D_2 to zep_ce.user_summaries; read by M_4_D_2
  project_uuid: UUID, required — the Zep project
  user_id: string, required — the user
  content: string, required — the summary
  through_message_id: integer, required — the last message folded in
  updated_at: timestamp, required — when replaced
cognee_chunk_node.json — to the graph_node table in schema tiers.relational.database_name of the memory database, written by M_5_B_1 through M_5_A_1
  id: string, required — uuid5 of the piece id
  name: string, required — the piece id
  type: string, required — DocumentChunk
  text: string, required — the piece's text
  belongs_to_set: list of string, required — the producer's group
  event_id: string, required — the captured event
  piece_id: string, required — the supplied piece
  position: integer, required — the piece's place in its event
  text_fingerprint: string, required — sha256 of the text
  actor_agent_id: string, required — the producer
  event_time: string, required — the event's instant
  event_type: string, optional — the event type or content type
  schema_version, source_revision, source_artifact_id, work_id, payload_sha256, exactness: strings, optional — the event's provenance
mem0_memory_record.json — to Mem0's memory collection in the memory database, written by M_6_A_2 through M_6_A_1
  id: string, required — the memory's id (a new uuid on ADD, kept on UPDATE)
  vector: FLOAT[width], required — the configured embedder model's received numbers for data
  payload.data: string, required — the memory text
  payload.hash: string, required — md5 of the text
  payload.user_id: string, required — the identity scope (the producer)
  payload.event_id: string, required — the captured event it came from
  payload.actor_agent_id: string, required — the producer
  payload.embedding_identity: string, required — provider, model, revision and width as JSON
  payload.created_at, payload.updated_at: string, required — ISO 8601 times
  payload.source_revision, payload.work_id, payload.source_artifact_id, payload.exactness: string, optional — provenance
mem0_history_row.json — to Mem0's history table in the memory database, written by M_6_A_1
  id: string, required — the history row's id
  memory_id: string, required — the memory changed
  old_memory: string, optional — the text before
  new_memory: string, optional — the text after
  event: string, required — ADD, UPDATE or DELETE
  created_at, updated_at: timestamp with time zone, required — when
  is_deleted: boolean, required — true on DELETE
  actor_id, role: string, optional — who said it
tier_rankings.json — to M_8_A_2 tier retrieval, from M_5_B_1 and M_6_A_2
  rankings: object, required — model label to a list of items ordered by cosine distance, each naming event_id, item_id and distance
  left_out: object, required — model label to the reason it gave no ranking
  not_current: list, optional — facts held back as ended (always empty for these two tiers)
processor_result.json — to the owner's receipt for the event and tier, from M_5_B_1 and M_6_A_2
  status: string, required — processed or processed_no_new_fact
  processor_id: string, required — cognee-duckdb-chunks or mem0-duckdb
  processor_version: string, required — the installed cognee or mem0_duckdb version
  projection_version: string, required — cognee-piece-chunk-1 or mem0-history-vector-1
model_step_output.json — to the caller via the socket model operation, from M_5_B_2, M_5_B_3, M_6_B_1, M_6_B_2 and M_6_B_3
  step fields: object, required — the step's own output as plain JSON
  part: string, required — cognee or mem0
  step: string, required — the step run
  opinion: string, required — main or second
  requested_model: string, required — the model the settings named for that opinion
  achieved_model: string, optional — the model the server reported; null before any answer
  placement: string, required — graphics card or processor only, where the server holds the model
  seconds: float, required — time the step took
embedding_embed_request.json — to agent_embedding POST /embed, header requester-agent-id
  capability_id: string, required — the reader's capability id
  passages: list of object, required — id and text (the model's declared question or passage words in front)
embedding_feature_pairs_request.json — to agent_embedding POST /feature/{rerank capability}
  declared_ids: list of string, required — the candidate ids, unique
  pairs: list of object, required — id, text_a (the question), text_b (the candidate)
embedding_feature_words_request.json — to agent_embedding POST /feature/{words capability}
  declared_ids: list of string, required — "question" then the candidate ids, unique
  passages: list of object, required — the question as id "question", then each candidate's id and text
corpus_question_request.json — to agent_corpus POST /question_numbers
  requester_agent_id: string, required — memory's requester id
  question: string, required — the text to measure
retrieve_answer.json — to the caller via the local Unix socket (plain JSON, no length prefix)
  status: string, required — "success"
  tiers: object, required — episodic, temporal, relational and personalization, each a tier_answer
retrieve_batch_answer.json — to the caller via the local Unix socket
  status: string, required — "success"
  answers: list of object, required — query and tiers, in the order asked
tier_answer.json — one memory kind's answer inside retrieve and retrieve_batch
  status: string, required — ok, partial, unavailable or failed
  hits: list of object, required — for a ranked tier, events with event_id, score and items; for a plain tier, the tier's hits
  withheld: integer, optional — hits withheld as removed, expired or invisible
  reason: string, optional — why the tier is unavailable or failed
  combiner: object, optional — id, rule, models, settings_read; null for a plain tier
  combiner_note: string, optional — for a plain tier
  models: object, optional — used, ranked_not_combined, left_out (label to reason)
  settings_used: object, optional — every search setting in force
  decay: object, optional — applied and time_constant_seconds, or the reason
  words: object, optional — applied, capability, ranked, candidates, order, or the reason
  merged_order: list of string, optional — item ids after fusion and decay
  rerank: object, optional — applied, capability, candidates, weight, cross_scorer_order, ordered, or the reason
  not_current: list of object, optional — item_id, event_id, fact, valid_at, invalid_at
  rankings: object, optional — model label to its own hits (item_id, event_id, distance, text, memory, content)
  ground_truth_case: string, optional — the case recorded, or null
  ground_truth_error: string, optional — why recording failed
context_answer.json — to the caller via the local Unix socket
  status: string, required — "success"
  context: string, required — Graphiti's rendered block followed by the WAITING section
  waiting: list of string, required — each thing that could not be answered and why
  episodes: integer, required — episodes placed in the block
models_report.json — to any authenticated caller via the socket operation models
  models: list of object, required — model_id, revision, width, kind, supplier_collection, supplier_recipe, registered_at, numbers_table, fresh, reasons, rows, stale_rows, missing_pieces, derived_fresh, derived_reasons, derived_rows, derived_missing
  numbers_owed: list of object, required — request_id, model_id, revision, event_id, reason, requested_at (text removed)
  model_id, revision: string, required — primary key together
  width: integer, required — numbers per text
  kind: string, required — embedding or classifier
  supplier_collection, supplier_recipe: string, required — where the supplier keeps this model's numbers
  registered_at: string, required — ISO 8601
  numbers_table: string, required, unique — memory_numbers_ plus 20 hexadecimal characters
  model_id, revision, checked_at, status: string, required — status current, mismatch or unverified
  supplier_model_id, supplier_revision: string, optional — what the supplier reported
  supplier_width: integer, optional — the width it reported
  reason: string, optional — the mismatch or refusal
  event_id: string, required — the event the piece belongs to
  piece_id: string, required, primary key — the piece
  model_id, revision: string, required — the label
  width: integer, required — the registered width
  text_fingerprint: string, required — SHA-256 of the piece text
  received_at: string, required — ISO 8601
  stale: boolean, required — marked stale by a label mismatch
  stale_reason: string, optional — why
  numbers: array of width floats, required — the supplier's numbers
  piece_id: string, required, primary key — "<event>#0" from the door, a corpus unit id, or "derived:<event>:<fingerprint>"
  event_id: string, required — foreign key to memory_events
  position: integer, required — the piece's place in the event
  text, text_fingerprint: string, required — the measured text and its SHA-256
  source: string, required — event or derived
  received_at: string, required — ISO 8601
  piece_id: string, required, primary key — the retired piece
  retired_at, reason: string, required — when and why (re-cut, or the engine no longer holds the text)
  replaced_by: string, optional — the replacing piece ids
  request_id: string, required, primary key — uuid
  model_id, revision: string, required — the model owing numbers
  event_id, text, text_fingerprint: string, optional — what is owed; text cleared when closed
  reason, requested_at: string, required — the supplier's refusal and when
  status: string, required — pending, answered, superseded or withdrawn
  closed_at: string, optional — when it closed
  combiner_id: string, required, primary key — the combiner
  rule: string, required — reciprocal_rank_fusion or external_artifact
  models, settings_read: JSON text, required — sorted lists
  proposed_by, proposed_at: string, required — who and when
  decision_id, combiner_id: string, required — the decision and the combiner it is about
  replaces: string, optional — the combiner current before
  decision: string, required — seeded, adopted or kept_current
  comparison_score: string, optional — the score name
  candidate_score, current_score: number, optional — the two scores
  held_back_cases, smallest_held_back_set: integer, optional — the counts
  settings_used: JSON text, optional — the settings in force
  reason, decided_at: string, required — why and when
  name: string, required — the search setting
  value: JSON text, required — its value
  source: string, required — "memory_runtime_settings table, [ranking]"
  recorded_at: string, required — ISO 8601
ground_truth_case.json — written by M_9_A_1 and M_9_B_1 to memory_gt_cases and memory_gt_case_events in the memory database; read back by M_9_B_1, M_9_A_3 and M_9_B_2
  case_id: string, required — "case-" followed by a SHA-256 hex digest
  kind: string, required — retrieved_item, model_choice or classification
  memory_kind: string, required — episodic, temporal, relational or personalization
  input_kind: string, required — question or event
  input: string, required — the question text, or an event id when input_kind is event
  created_at: string, required — ISO 8601 UTC time
  created_by: string, required — the principal, os-uid-<uid>
  split: string, required — training or held_back, fixed at creation
  held_back_share: float, required — the share in force at creation
  selected_by_owner: boolean, required — true when chosen through review_select
  events: list of string, required — the event ids the case points to (on read)
  source_removed: boolean, required — derived on read from the removal records
ground_truth_output.json — written by M_9_A_1 to memory_gt_outputs in the memory database; read by M_9_A_3, M_9_B_1 and M_9_B_2
  case_id: string, required — the case
  output_key: string, required — model_id@revision for a reader, or combined, words or reranked
  role: string, required — model, combined, words or reranked
  model_id: string, optional — the registered model, for role model only
  revision: string, optional — the registered revision, for role model only
  width: integer, optional — read from the model registry, for role model only
  output: JSON list, required — ranked items, each item_id, event_id, item_kind, score, and cross_score for reranked
  combiner_id: string, required — the combiner current at the time
  recorded_at: string, required — ISO 8601 UTC time
ground_truth_run.json — written by M_9_A_1 to memory_gt_runs in the memory database; read through ground_truth_table
  case_id: string, required — the case, one run per case
  query: string, required — the question
  tier: string, required — the memory kind searched
  ran_at: string, required — ISO 8601 UTC time
  settings: JSON object, required — the full search settings report in force
  report: JSON object, required — models, words, decay, rerank with cross_scores, not_current without fact text, withheld
known_answer_request.json — from any agent via the local Unix socket to M_9_A_2
  op: string, required — record_known
  query: string, required — the test question
  answer: string, required — the right answer in words
  events: list of string, required — the events whose statements give the answer
  basis: string, required — construction, or the reason for a correction
  said_by: string, required — who says the answer is right
known_answer.json — written by M_9_A_2 to memory_gt_known in the memory database; current rows shown by memory_gt_current_known
  known_id: string, required — a random identity
  query: string, required — the test question
  answer: string, required — the right answer in words
  right_events: JSON list of string, required — sorted unique event ids
  basis: string, required — construction or the correction's reason
  said_by: string, required — who says it is right
  recorded_by: string, required — the caller, os-uid-<uid>
  recorded_at: string, required — ISO 8601 UTC time
  supersedes: string, optional — the known_id this row replaces
known_answer_reply.json — from M_9_A_2 to the caller via the local Unix socket
  status: string, required — success
  known_id: string, required — the row appended
ground_truth_table_request.json — from any agent via the local Unix socket to M_9_A_3
  op: string, required — ground_truth_table
  table: string, required — one of memory_gt_known, memory_gt_current_known, memory_gt_cases, memory_gt_case_events, memory_gt_outputs, memory_gt_runs, memory_gt_judgments, memory_gt_ranking_items, memory_gt_ranking_scores
  limit: integer, required — the most rows to return, at least 1
ground_truth_table_reply.json — from M_9_A_3 to the caller via the local Unix socket
  status: string, required — success
  table: string, required — the table read
  columns: list of string, required — the column names
  rows: list of list, required — at most limit rows the caller may see
  withheld: integer, required — rows left out for visibility
ranking_score_row.json — a row of the memory_gt_ranking_scores view, computed by the database from M_9_A_1's outputs and M_9_A_2's current known answers
  case_id: string, required — the case
  query: string, required — the case's question
  tier: string, required — the case's memory kind
  output_key: string, required — the output scored
  role: string, required — model, combined, words or reranked
  known_id: string, required — the known answer scored against
  truth_by: string, required — who recorded that known answer
  first_right_rank: integer, optional — the first rank whose event is right, null when none is
  right_at_1: boolean, required — whether the first item is right
  reciprocal_rank: float, required — one over first_right_rank, 0 when none is
review_request.json — from any agent via the local Unix socket to M_9_B_1
  op: string, required — review_list, review_show, review_page, review_judge or review_select
  case_id: string, optional — required for review_show, review_page and review_judge
  verdict: string, optional — for review_judge: right, wrong, pick, typed or none_correct
  chosen_items: list of string, optional — for review_judge, defaults to empty
  typed_answer: string, optional — for review_judge with verdict typed
  note: string, optional — for review_judge
  question: string, optional — required for review_select
  events: list of string, optional — required for review_select
  memory_kind: string, optional — required for review_select: episodic, temporal, relational or personalization
review_list_reply.json — from M_9_B_1 to the caller via the local Unix socket
  status: string, required — success
  cases: list, required — each case_id, kind, memory_kind, input_kind, created_at, disagreement, selected_by_owner, judged, verdict
review_view.json — from M_9_B_1 to the caller via the local Unix socket, inside a reply with status success under view
  case: object, required — the case as in ground_truth_case.json
  original: object, required — event id to that event's original text, for the case's events
  answers: list, required — each key, role, combiner_id, items (each with its event's original text) and scores
  judgments: list, required — every judgment of the case, oldest first
  disagreement: boolean, required — whether the readers' first items differ
review_page_reply.json — from M_9_B_1 to the caller via the local Unix socket
  status: string, required — success
  html: string, required — one complete HTML page, every value escaped
judgment.json — written by M_9_B_1 to memory_gt_judgments in the memory database; read by M_9_A_3, M_9_B_2 and the held-back comparison
  judgment_id: string, required — a random identity
  case_id: string, required — the case judged
  judged_by: string, required — the caller, os-uid-<uid>
  judged_at: string, required — ISO 8601 UTC time
  verdict: string, required — right, wrong, pick, typed or none_correct
  chosen_items: JSON list of string, required — the items chosen, possibly empty
  typed_answer: string, optional — the typed answer
  note: string, optional — the judge's note
  supersedes: string, optional — the judgment this row replaces
export_manifest.json — written by M_9_B_2 to memory_gt_exports in the memory database and returned, with its content, to the Python caller
  export_id: string, required — a random identity
  purpose: string, required — training or held_back_inputs
  case_ids: list of string, required — sorted case ids
  fingerprint: string, required — SHA-256 of the canonical JSON content
  created_at: string, required — ISO 8601 UTC time
  created_by: string, required — who asked
  content: list, required — returned to the caller only, never stored: per case, the case, outputs and judgments (training) or inputs and model outputs (held_back_inputs)
export_verification.json — from M_9_B_2 to the Python caller
  matches: boolean, required — whether the content is exactly as fingerprinted
  removed: list of string, required — case ids whose source has since been removed
  fingerprint_now: string, required — the fingerprint of the content rebuilt now

Redis Channels:
Published: none directly; registration, heartbeat and deregistration go through shared_agent_registry over shared_redis_bus, whose keys are the registry's own.

HTTP Endpoints:
GET  /health — returns {"status": "ok" | "starting", "detail": string} with the socket, database, model parts, agent_id and registration note

## MODULE DEFINITIONS

### M_0 — Infrastructure
description: Starts, runs and stops the memory service from the settings stored in its own DuckDB file: the entry script and command router, the settings loader, writer and page, the builder that wires the tier engines to the owner service, the health-door port claim and registry presence, the asynchronous DuckDB layer every part uses, and the core schema created at every start.
dependencies: []
entry_conditions:
  - Dockerfile.base is VERIFIED
  - the micromamba environment sovereign_memory holds every package pinned in environment/requirements.lock
  - test fixtures are prepared
exit_conditions:
  - all submodules VERIFIED
  - all artifacts schema-valid
  - all tests passed
  - end-to-end module test passed
module_test: On a throwaway database, store a complete settings set with set-setting, start the service through run_sovereign_memory.sh with a free health address, confirm GET /health answers 200 with status ok and a registration note, send SIGTERM and confirm exit code 0 with the socket file removed and the database file unlocked; then start it on a database with no settings table and confirm exit code 2 with nothing started.

#### M_0_A — Start and Run
purpose: Turn a start request into a running, maintained service, a bounded tier rebuild, a settings command, or a plain refusal with an exit code.
inputs:
  - argv: list of string — the command-line words
  - SOVEREIGN_PYTHON: string — the interpreter the environment names
  - SOVEREIGN_MEMORY_DATABASE: string — the memory database file when no argument names it
outputs:
  - exit_code: integer — 0, 1 or 2
  - running_service: process — the owner service serving its socket until a stop signal
dependencies:
  - M_0_B_1
  - M_0_B_2
  - M_0_B_3
  - M_0_C_1
  - M_0_C_2
tests:
  - A database with valid settings and a free health address -> the service serves until SIGTERM, then exits 0
  - No database path and no SOVEREIGN_MEMORY_DATABASE -> exit code 2 with the reason printed
  - SOVEREIGN_PYTHON unset -> the entry script exits 1 before any Python runs
  - SIGTERM while a maintenance round runs -> the round is waited on for at most the drain bound, then cancelled, and the service stops cleanly

##### M_0_A_1 — Entry Script and Interpreter Check
job: Start the memory runtime from the one entry file the shared service supervisor recognises, refusing a missing or non-executable interpreter.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: bash
inputs:
- argv: list of string — every argument, passed through unchanged to the command router
- SOVEREIGN_PYTHON: string — path of the interpreter; the user service sets it to the micromamba environment sovereign_memory's python
- AGENT_ROOT: string — optional; the project root, defaulting to the folder the script sits in
outputs:
- exit_code: integer — 1 when the interpreter is refused; otherwise the process becomes the Python runtime and its exit code is the router's
artifacts: []
data_contracts: []
seam_contracts:
- The process that holds the health address runs AGENT_ROOT/sovereign_memory_main.py; the port claim of M_0_C_1 matches that exact path when a later start supersedes an earlier copy.
dependencies:
- M_0_A_2
invariants:
- UMST:INV-003
- agent_memory:INV-011
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Run in strict shell mode: stop on any failed command, any unset variable and any failed stage of a pipe.
  2. Set AGENT_ROOT to the environment's value, or else to the folder this script sits in (the project root), derived from the script's own location and never a written-in home path.
  3. If SOVEREIGN_PYTHON is unset, empty, or not an executable file, print "sovereign_memory did not start: SOVEREIGN_PYTHON must name this project's interpreter (its micromamba environment's python); it is '<value or not set>'." to standard error and exit 1. Nothing else runs.
  4. Set PYTHONDONTWRITEBYTECODE=1 so the interpreter writes no compiled-bytecode files into the tree.
  5. Replace this process (exec, not a child call) with SOVEREIGN_PYTHON running AGENT_ROOT/sovereign_memory_main.py, passing every argument unchanged; the reset flags --reset and --deep-reset are passed through, never interpreted here.
  6. sovereign_memory_main.py holds no logic: it calls the command router (M_0_A_2) and exits with its status.
  7. The same router is also reachable as python -m sovereign_memory and as the installed command sovereign-memory. Those two ways skip steps 2 to 5: no interpreter check, and AGENT_ROOT is unset unless the caller sets it, so a port claim with a health address given is refused (M_0_C_1).
  8. The interpreter must be proven by measurement to be this project's own environment (Law 11: its own package-metadata folder present, no virtual-environment marker anywhere in its prefix, the interpreter resolving inside its own prefix). This is not built: the script checks only that the file exists and is executable.
  9. The script sets none of SOVEREIGN_MEMORY_DATABASE, SOVEREIGN_MEMORY_HTTP_HOST, SOVEREIGN_MEMORY_HTTP_PORT, REDIS_HOST, REDIS_PORT or SOVEREIGN_ROOT; the user service agent-sovereign-memory.service (or the caller) gives them.
tools:
- bash
- micromamba
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_0_A_1
- exit_code: integer, CORE — 1 on a refused interpreter, otherwise the router's code
- interpreter_path: string, CORE — the SOVEREIGN_PYTHON value checked
- agent_root: string, DEBUG — the project root used
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- SOVEREIGN_PYTHON unset, empty or not executable -> print the reason naming the value, exit 1, nothing started
- SOVEREIGN_PYTHON names an executable interpreter that is not this project's environment -> not detected today (the Law 11 environment-kind proof is not built); the runtime then fails on its first missing package or runs on the wrong packages
- .copier-answers.yml names the environment sovereign_tools while the real environment is sovereign_memory -> a record mismatch nothing detects; that file is changed only by a copier update
- Started through python -m sovereign_memory or sovereign-memory -> no interpreter check runs, and with a health address given the port claim refuses because AGENT_ROOT and SOVEREIGN_ROOT are needed (exit 1)
- run_sovereign_memory.sh lacks its executable bit on disk -> the user service cannot run it (seen 2026-10-24; git records the file executable)
tests:
- type: unit
  description: An unset interpreter is refused before any Python runs
  input: run_sovereign_memory.sh with SOVEREIGN_PYTHON unset
  expected: exit code 1 and a message ending "it is 'not set'."
  tolerance: exact
- type: unit
  description: Arguments reach the router unchanged
  input: SOVEREIGN_PYTHON set to the environment's python, arguments show-settings and a throwaway database path
  expected: the settings page of that database is printed and the exit code is 0
  tolerance: exact
- type: invariant
  description: UMST:INV-003 a refusal is never silent
  input: SOVEREIGN_PYTHON set to a file without the executable bit
  expected: exit code 1 with that path named on standard error
  tolerance: exact
logging_tier: CORE

##### M_0_A_2 — Command Router
job: Route the command line to serving, a bounded tier rebuild, or a settings command, refusing an unusable database path or invalid settings before anything starts.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- argv: list of string — the command-line words, or the process's own when none are given
- SOVEREIGN_MEMORY_DATABASE: string — the database file when no argument names it
- SOVEREIGN_MEMORY_CONFIG: string — read only to refuse it when set
- environment: mapping of string to string — the process environment, read once for the presence
outputs:
- exit_code: integer — 0 on success, 1 on a failed start or rebuild, 2 on a refused path, argument or setting
- printed_lines: list of string — settings changes, the settings page, the adopted combiner, or the rebuild result
artifacts:
- rebuild_result.json
data_contracts:
- rebuild_result.json
seam_contracts:
- set-setting and show-settings call change_settings and show_page of M_0_B_2.
- adopt-combiner calls adopt_by_ruling of M_8_C_2 on a direct read-write handle of the stopped database.
- A bounded rebuild starts the owner without processing owed work (M_1_A_1) and calls rebuild_projection (M_2_A_3).
- Serving hands the built service, its maintenance interval and the presence to the lifecycle of M_0_A_3.
dependencies:
- M_0_B_1
- M_0_B_2
- M_0_B_3
- M_0_C_1
- M_0_A_3
invariants:
- UMST:INV-003
- agent_memory:INV-001
- agent_memory:INV-006
- agent_memory:INV-008
- agent_memory:INV-011
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Take the arguments given, or the process's own command line.
  2. If the first word is set-setting: read the database path, any number of name=value assignments, --describe name=words (repeatable), --unset name (repeatable) and --by who (default: the login name), in any order. A name assigned twice is refused. Call change_settings (M_0_B_2), print each returned line and return 0; on a refusal print "Refused: <reason>" and return 2.
  3. If the first word is show-settings: read the database path and an optional --history; print the page from show_page (M_0_B_2) and return 0; on a refusal print "Refused: <reason>" and return 2.
  4. If the first word is adopt-combiner: read the database path, the combiner id, --ruling words (required), --reads setting (required, repeatable) and --by who (default: the login name). Open the database read-write directly; if it cannot be opened (the running service holds it) print "Refused: cannot open <path> (stop the service first): <error>" and return 2. In one transaction call adopt_by_ruling (M_8_C_2) with the current time; on a ValueError or database error roll back, print "Refused: <reason>" and return 2. Otherwise commit, print "Combiner <id> is current over: <models>" and return 0. No earlier decision is edited.
  5. If SOVEREIGN_MEMORY_CONFIG is set to anything non-empty, print that it is no longer read, that SOVEREIGN_MEMORY_DATABASE names the database and how to set settings, and return 2.
  6. Read the serving options: an optional database path, --rebuild-tier (relational or personalization only), --rebuild-id, --rebuild-limit (an integer), --reset and --deep-reset. A malformed option is a usage error with exit code 2.
  7. If --reset or --deep-reset is given, print that this service keeps no generated state or logs of its own, that no reset mode ever clears the memory database (the operator's content) and that nothing was removed; then continue to a normal start.
  8. If --rebuild-tier is given without --rebuild-id or without a --rebuild-limit above 0, or --rebuild-id or --rebuild-limit is given without --rebuild-tier, stop with a usage error and exit code 2.
  9. Take the database path from the argument, or else from SOVEREIGN_MEMORY_DATABASE. If neither gives one, print "Give the memory database file or set SOVEREIGN_MEMORY_DATABASE" and return 2.
  10. If the path ends in .toml, print that settings now live in the memory database with the set-setting command line, and return 2.
  11. Load and validate the stored settings (M_0_B_1), then build the service unstarted (M_0_B_3), restricted to the rebuild tier when one is given. On a ConfigurationError print "Configuration error: <reason>" and return 2. Up to here nothing has opened the database for writing.
  12. Turn on logging at INFO level to standard error.
  13. Rebuild route: start the owner without processing owed work, run rebuild_projection(tier, id, limit), and always stop the owner. A ValueError or RuntimeError prints "Rebuild failed: <reason>" and returns 1. Otherwise print the result as one JSON line and return 0 when its status is completed, 1 for any other status.
  14. Serve route: define the health report function (M_11_A_2) and build the presence from one snapshot of the environment (M_0_C_1). A half-given or malformed health address prints "Configuration error: <reason>" and returns 2.
  15. Run the lifecycle (M_0_A_3). An EcosystemError (the port held by another program, or SOVEREIGN_ROOT or AGENT_ROOT missing for a claim) prints "Did not start: <reason>" and returns 1. A clean stop returns 0.
tools:
- argparse
- asyncio
- duckdb
- logging
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_0_A_2
- command: string, CORE — serve, rebuild, set-setting, show-settings or adopt-combiner
- database_path: string, CORE — the memory database used
- exit_code: integer, CORE — the code returned
- refusal_reason: string, CORE — the printed reason when the code is not 0
- reset_requested: boolean, DEBUG — whether --reset or --deep-reset was given
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- No database path -> print the reason, return 2
- SOVEREIGN_MEMORY_CONFIG set -> print the new way, return 2
- A .toml path given -> print the set-setting command, return 2
- A setting missing, unknown or invalid, or an engine that cannot be built -> print "Configuration error" naming it, return 2, nothing started
- Rebuild options incomplete or given without a tier -> usage error, exit code 2
- Rebuild tier not configured -> "Configuration error: tiers.<tier> is not configured", return 2
- Rebuild raises ValueError or RuntimeError -> "Rebuild failed", return 1; a rebuild that ends in any status but completed -> result printed, return 1
- adopt-combiner while the service runs -> refused because the database cannot be opened, return 2
- Port held by another program, or SOVEREIGN_ROOT or AGENT_ROOT missing for a claim -> "Did not start", return 1, after the service that had already started is stopped cleanly
- Any other start failure (the socket path refused, a database error, or a setting refused by a language-model part's own check when it is built at start, such as the Letta or Zep settings) -> not caught here: the traceback is printed and the process exits with status 1. This contradicts the rule that every invalid setting returns 2 before anything starts, because those part settings are first checked inside the service's start (known gap)
- --reset or --deep-reset -> clears nothing, says so, and starts normally
tests:
- type: unit
  description: No database path is refused
  input: no argument and no SOVEREIGN_MEMORY_DATABASE
  expected: exit code 2 and "Give the memory database file or set SOVEREIGN_MEMORY_DATABASE" printed
  tolerance: exact
- type: unit
  description: The retired settings-file variable is refused
  input: SOVEREIGN_MEMORY_CONFIG set to any file path
  expected: exit code 2 and a message naming SOVEREIGN_MEMORY_DATABASE and the set-setting command
  tolerance: exact
- type: unit
  description: Rebuild options without a tier are refused
  input: a database path with --rebuild-id r1 and no --rebuild-tier
  expected: a usage error and exit code 2, the database untouched
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 no reset mode removes an original
  input: --reset on a database holding captured events
  expected: the reset message is printed, the service starts, and every memory_payloads row is still present afterwards
  tolerance: exact
- type: invariant
  description: agent_memory:INV-001 adopt-combiner never writes beside a running service
  input: adopt-combiner against the database a running service holds
  expected: exit code 2 with "stop the service first"; no decision row is added
  tolerance: exact
logging_tier: CORE

##### M_0_A_3 — Service Lifecycle and Maintenance Loop
job: Run the started service with its periodic maintenance until a stop signal, then stop every part within the drain bound.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: true
language: python
inputs:
- service: OwnerService — the built, unstarted owner service
- maintenance_interval_seconds: float — seconds between maintenance rounds, from the stored settings
- presence: Presence — the health door and registration, or none
- signals: SIGINT or SIGTERM — the request to stop
outputs:
- serving_process: process — the owner's socket served until a stop signal
- maintenance_log: log lines — each failed round with its traceback
artifacts: []
data_contracts: []
seam_contracts:
- Calls start and stop of the owner service (M_1_A_1); start creates the schema (M_0_D_2) and spawns processing of owed work (M_2_A_2).
- Each maintenance round calls enforce_expiry (M_1_C_2), then sync_numbers (M_7_A_3), then process_owed (M_2_A_2).
- Starts and stops the presence: the port claim and door (M_0_C_1), the registration and heartbeat (M_0_C_2).
dependencies:
- M_0_B_3
- M_0_C_1
- M_0_C_2
invariants:
- UMST:INV-003
- agent_memory:INV-001
- agent_memory:INV-003
- agent_memory:INV-011
tunable_parameters:
  - param_name: service.maintenance_interval_seconds
    default_value: "30"
    param_type: float
    description: Seconds between maintenance rounds (expiry, number sync, owed work); a stored setting with no code default, positive and finite.
  - param_name: service.drain_timeout_seconds
    default_value: "10"
    param_type: float
    description: The most seconds stop waits for the maintenance task, then for open clients, then for background processing, before cancelling each; a stored setting with no code default, positive and finite.
intelligence_pattern: time_based_justified
logic: |
  1. Install SIGINT and SIGTERM handlers that set one stop flag.
  2. Start the owner service (M_1_A_1): it opens the database for its whole life, creates and migrates the schema (M_0_D_2), builds the language-model parts, binds the socket and spawns processing of every owed receipt in the background. A failure here propagates to the router (exit 1 with a traceback) and nothing below runs.
  3. Start the maintenance task (steps 9 to 12).
  4. Start the presence: claim the health port and open the door (M_0_C_1), then begin registration (M_0_C_2). The health door therefore opens only after the socket serves.
  5. Wait for the stop flag.
  6. On the stop flag, on a failed presence start, or on cancellation: set the flag; stop the presence (heartbeat cancelled, deregistered, door closed).
  7. Wait for the maintenance task for at most drain_timeout_seconds. If it is still running, cancel it and wait for it. Raise any failure of the maintenance task, and any cancellation this step did not request itself; the cancellation this step requested is not an error.
  8. In every case stop the owner service (M_1_A_1: close the socket server, drain clients and background processing each within the drain bound, close the database connection, remove only its own socket file), then remove the signal handlers.
  9. Maintenance loop: wait up to maintenance_interval_seconds for the stop flag; when it is set, return.
  10. Otherwise run one round, in this order: enforce expiry, sync numbers with the supplier, process owed receipts.
  11. If any step of the round raises, log "Scheduled owner maintenance failed" with the traceback; the rest of that round is skipped and the next round still runs.
  12. Rounds never overlap: the next wait starts when a round ends, so a round longer than the interval delays the next one. The first round runs one interval after start, so numbers are first synced then, while owed receipts are already being processed from the start.
tools:
- asyncio
- signal
- logging
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_0_A_3
- round_started_at: string, DEBUG — ISO 8601 time a maintenance round began
- round_failed: boolean, CORE — whether the round raised
- round_error: string, CORE — the exception type and message of a failed round
- stop_signal: string, CORE — SIGINT or SIGTERM
- maintenance_cancelled: boolean, CORE — whether the drain bound ended the maintenance task
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- A maintenance round raises -> logged with its traceback, the rest of that round skipped, the next round runs at the next interval
- Maintenance still running when the drain bound ends at stop -> cancelled; unfinished receipts stay owed and are retried at the next start
- The owner service fails to start -> the presence never starts, the exception reaches the router, exit 1
- The presence fails to start (port held by another program) -> maintenance and the already-started service are stopped cleanly, the router exits 1 with "Did not start"
- A language-model call still running in its thread when the drain bound ends -> its coroutine stops, the thread is left to the model server's request timeout, and the receipt stays "processing" for the next start to retry
- Every round failing -> visible only in the log; the health door still answers ok, because health reports whether the socket server exists and nothing about maintenance (known gap)
tests:
- type: unit
  description: A failed round does not stop maintenance
  input: a service whose enforce_expiry raises once, interval 1 second
  expected: one logged "Scheduled owner maintenance failed" and a second round that runs all three steps
  tolerance: exact
- type: unit
  description: SIGTERM stops everything within the bounds
  input: a running service, then SIGTERM
  expected: the presence is stopped first, the service stops, the socket file is gone and the process returns within about three drain bounds
  tolerance: numerical
- type: invariant
  description: agent_memory:INV-001 the database file is released at stop
  input: SIGTERM to the running service, then a read-only open of its database by another process
  expected: the open succeeds
  tolerance: exact
- type: invariant
  description: UMST:INV-003 a maintenance failure is logged, never swallowed
  input: sync_numbers raising a RuntimeError during a round
  expected: the log holds the traceback naming RuntimeError
  tolerance: exact
logging_tier: CORE

#### M_0_B — Settings
purpose: Keep every setting as a described row of the memory database, validate it whole before the service uses it, and build the service from it.
inputs:
  - memory_runtime_settings: table — the stored settings rows
  - assignments: list of name=value — the operator's changes
outputs:
  - runtime_config: RuntimeConfig — every setting, validated
  - owner_service: OwnerService — built and unstarted
  - settings_page: text — the readable settings page
dependencies:
  - M_0_D_2
tests:
  - A complete settings set -> a RuntimeConfig with every tier configured
  - An unknown setting name -> refused naming it, nothing written
  - A new setting without a description -> refused naming it, nothing written
  - Both supplier and embedding_door stored -> refused at load and at change

##### M_0_B_1 — Settings Loader and Validator
job: Turn the stored settings rows of the memory database into one fully validated runtime configuration, or refuse naming the setting.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- database_path: string — the memory database file
outputs:
- runtime_config: RuntimeConfig — socket and database paths, the service bounds, pool labels, tier settings, search settings, model rows, the number source, the language model and the part settings; a tier's fields are empty when it is not configured
artifacts: []
data_contracts:
- memory_runtime_settings
seam_contracts:
- parse is the one validator; the settings writer (M_0_B_2) runs it on the complete result of every change.
- The ranking table's shape is checked here; its value types and ranges are checked by the search settings of M_8_C_1 when the service is built.
- The Zep, Letta and Graphiti part settings are checked here for their keys only; their values are checked by the parts themselves when the service starts (M_2_B_1 and the part nodes).
dependencies: []
invariants:
- UMST:INV-003
- agent_memory:INV-001
- agent_memory:INV-004
- agent_memory:INV-005
- agent_memory:INV-011
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Resolve the database path. If it is not a file, refuse: "Memory database <path> does not exist; create its settings with: <set-setting command line>".
  2. Open the file read-only. If that fails (another process holds it, such as the running service), refuse naming the error and asking whether the memory service is already running on it.
  3. If the table memory_runtime_settings is absent, refuse with the set-setting command line. Read every (path, value) row ordered by path; a database error is refused naming it. The file is always closed before returning.
  4. If there are no rows, refuse: the table is empty.
  5. Rebuild the nested settings from the dotted paths. Each value is JSON text; text that is not JSON is refused naming the path. A path below a path that holds a value, or a path stored twice or also holding sub-settings, is refused.
  6. If service.database_path is stored, refuse: the database is the file given on the command line, never a setting. Otherwise set it to that file.
  7. Validate the whole, resolving relative paths against the database's folder. Top level: service and tiers are required; visibility, ranking, models, supplier, embedding_door, language_model, zep, cognee, letta and graphiti are optional; any other name is refused as unknown. The same rule (required present, nothing unknown) applies to every table below.
  8. service: socket_path (non-empty text), max_request_bytes (a positive integer), drain_timeout_seconds and maintenance_interval_seconds (positive finite numbers). A true or false value is never accepted as a number.
  9. visibility.pool_scope_labels: optional, a list of non-empty strings; absent means no pool labels.
  10. tiers: only episodic, temporal, relational and personalization, and at least one. episodic needs organization_id (non-empty text). temporal must be an empty table. relational needs database_name (non-empty text) and may name embedding_model {model_id, revision}.
  11. language_model: provider (must be ollama), model, base_url, api_key (non-empty text), request_timeout_seconds (positive finite), options (a table), and optionally second_opinion_model (non-empty text).
  12. personalization: only memory. A stored memory.llm is refused (the model is set in language_model); language_model is required. Mem0's llm section is made from language_model: provider openai at base_url plus /v1, with model, api_key, the request timeout, and temperature, max_tokens, top_p and reasoning_effort copied from options when present. memory needs embedder, vector_store and enable_update_delete_branches, and may hold custom_fact_extraction_prompt, custom_update_memory_prompt and custom_instructions. embedder needs provider, config (with model) and revision. vector_store's provider must be duckdb and its config.embedding_model_dims a positive integer. Mem0's own configuration check then runs. enable_update_delete_branches must be true (the tier refuses Mem0's add-only pipeline). embedder.provider must be received_numbers (memory never makes numbers, so the retired direct-call embedder and every model-calling embedder are refused). embedder config received_numbers.revision must equal the embedder's revision, and embedding_dims must be an integer equal to the vector store's dimensions.
  13. ranking: required when relational or personalization is configured. It must hold comparison_score, held_back_share, smallest_held_back_set and merging_constant, and may hold only decay_time_constant_seconds, decay_lower_bound, decay_upper_bound, words_capability, model_weights, words_weight, rerank_capability, rerank_candidates and rerank_weight.
  14. models: a list of rows, each exactly model_id, revision, kind, supplier_collection and supplier_recipe (non-empty text) and width (a positive integer). No model is named in code; every model is one of these rows.
  15. supplier and embedding_door: base_url and requester_agent_id (non-empty text) and timeout_seconds (positive finite). embedding_door may hold prefixes: a table of model id to {question and/or passage: text}. Both stored together is refused: numbers come from one source.
  16. zep: message_window, summary_max_output_tokens, model_context_tokens, prompt_headroom_tokens and characters_per_token required, summarizer_prompt optional. cognee: data_directory (non-empty text). letta: compaction, sleeptime and dream. graphiti: contradiction_candidates. A temporal tier without graphiti settings is refused.
  17. If the socket path and the database path name the same file, refuse. The socket path is made absolute but not resolved, so a symbolic link at that path stays visible to the socket check of M_1_A_1.
  18. Return the RuntimeConfig. It reads no environment variable and no text file, never writes the table, calls no model and touches no network.
tools:
- duckdb
- pydantic
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_0_B_1
- database_path: string, CORE — the file read
- settings_read: integer, CORE — number of setting rows read
- refused_setting: string, CORE — the setting named by a refusal
- tiers_configured: list of string, CORE — the tiers the settings configure
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Database file missing -> ConfigurationError with the set-setting command line
- Database held by another process -> ConfigurationError asking whether the service is already running on it
- Settings table missing or empty -> ConfigurationError with the set-setting command line
- A value that is not JSON, a path under a value, or a path stored twice -> ConfigurationError naming the path
- A missing, unknown, mistyped or out-of-range setting -> ConfigurationError naming it; the service never starts on half-valid settings
- service.database_path stored -> ConfigurationError: it is not a setting
- A personalization embedder other than received_numbers -> ConfigurationError: memory never makes numbers
- supplier and embedding_door both stored -> ConfigurationError: one source, not two
- A Zep, Letta or Graphiti value that has the right key but a wrong value -> accepted here and refused only when the part is built at start (see M_0_A_2 for how that start failure ends)
tests:
- type: unit
  description: A missing settings table is refused
  input: an empty DuckDB file
  expected: ConfigurationError naming memory_runtime_settings and the set-setting command line
  tolerance: exact
- type: unit
  description: An unknown setting is refused
  input: settings rows holding every required service setting plus service.socket_mode
  expected: ConfigurationError "[service] has unknown keys: socket_mode"
  tolerance: exact
- type: unit
  description: Two number sources are refused
  input: settings rows holding both supplier and embedding_door tables
  expected: ConfigurationError saying numbers come from one source, not two
  tolerance: exact
- type: invariant
  description: agent_memory:INV-004 no model-calling embedder is accepted
  input: tiers.personalization.memory.embedder.provider set to ollama
  expected: ConfigurationError saying the provider must be received_numbers
  tolerance: exact
- type: invariant
  description: agent_memory:INV-011 no setting has a code default
  input: settings rows with service.drain_timeout_seconds removed
  expected: ConfigurationError naming drain_timeout_seconds as missing
  tolerance: exact
logging_tier: CORE

##### M_0_B_2 — Settings Writer and Page
job: Serve the operator's settings commands over the stored settings table, writing each checked change with its history or printing the readable page.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- database_path: string — the memory database file
- assignments: mapping of dotted name to value — new values; text that is not JSON is taken as plain text
- descriptions: mapping of dotted name to text — plain-words descriptions; one given for a parent covers its new children
- unsets: list of string — names to remove, each with everything below it
- changed_by: string — who is changing them; the login name when none is given
- with_history: boolean — whether the page lists every recorded change
outputs:
- change_lines: list of string — one line per change, or "nothing changed: every value and description was already as given"
- settings_page: text — every setting's name, value, description, when last changed and by whom
artifacts:
- memory_runtime_settings
- memory_runtime_settings_history
data_contracts:
- memory_runtime_settings
- memory_runtime_settings_history
seam_contracts:
- Every change is checked by config.parse of M_0_B_1 and by SearchSettings.from_mapping of M_8_C_1 on the complete settings that would result.
dependencies:
- M_0_B_1
invariants:
- UMST:INV-003
- UMST:INV-004
- agent_memory:INV-001
- agent_memory:INV-011
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. parse_assignment splits "name=value": a missing "=", an empty name or a name with an empty dotted part is refused with an example; the value is read as JSON, or taken as plain text when it is not JSON.
  2. parse_description splits "name=words": both parts must be non-empty.
  3. change_settings: if no assignment, unset or description is given, refuse "nothing to change".
  4. Open the database read-write; if that fails (the running service holds it), refuse saying the service may be running and must be stopped first.
  5. Begin one transaction. Create memory_runtime_settings and memory_runtime_settings_history when absent. If either table's columns are not exactly the expected ones in order, refuse: the table was made by an older version and its settings must be re-entered on a fresh database.
  6. Read the current values and descriptions. Apply unsets: a name with no setting at or below it is refused; otherwise it and everything below it are removed.
  7. Apply assignments: everything at or below the name is replaced by the new value, flattened into dotted leaves (an empty table or a list is one leaf). A key that is empty or holds a dot is refused. A value that cannot be stored as JSON (including not-a-number and infinity) is refused naming the path.
  8. A description naming no remaining setting is refused. Every resulting setting needs a description: the one given for it, else its stored one, else the one given for its nearest parent; a new setting with none is refused with the --describe line to use.
  9. Validate the complete result with the service's own validator (M_0_B_1), with the database path inserted; when ranking is present, also check its value types and ranges with the search settings check (M_8_C_1), refusing as "ranking: <reason>".
  10. Write, at one timestamp and in name order: a removed setting is deleted ("removed X (was v)"); a new or changed value or description replaces the row ("added X = v", "changed X: old -> new", or "described X" when only the description changed); one history row is added per changed value (what = value) and per changed description (what = description), each with old text, new text, time and who.
  11. Commit. A database error at any point is refused as "Cannot store settings"; closing the file abandons an unfinished transaction, so a refusal keeps no change.
  12. show_page: refuse a missing file, a database the service holds, a missing settings table or older-shaped columns. Print "Settings of the memory database <path>", the time the page was made, then for each setting in name order its "name = value", "What it does:", "Last changed: <time> by <who>", then the count; with history, every change newest first, or "none recorded". It only reads.
  13. Neither function reads a text file as a source of settings or starts the service; settings are refused while the service runs because the service holds the file.
tools:
- duckdb
- json
- getpass
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_0_B_2
- changed_by: string, CORE — who made the change
- changes_written: integer, CORE — number of value and description changes written
- refused_reason: string, CORE — why a change was refused
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Database held by the running service -> refused, nothing changed
- A new setting without a description -> refused with the --describe line to use, nothing changed
- Unset or describe of an unknown name -> refused, nothing changed
- The complete result fails validation -> refused naming the setting, nothing changed
- Older-shaped settings tables -> refused; settings must be re-entered on a fresh database
- A database error during the write -> refused, the transaction abandoned, nothing changed
- A part setting with a valid key but a value only the part checks (Zep, Letta, Graphiti, Cognee) -> stored, then refused only when the service next starts (known gap)
tests:
- type: unit
  description: A new setting needs a description
  input: set-setting with service.max_request_bytes=1048576 on an empty database and no --describe
  expected: refusal naming service.max_request_bytes and the --describe line; the table holds no row
  tolerance: exact
- type: unit
  description: A change writes its history
  input: an existing setting ranking.merging_constant changed from 60 to 50 by "shawn"
  expected: the line "changed ranking.merging_constant: 60 -> 50" and one history row with what value, old 60, new 50 and changed_by shawn
  tolerance: exact
- type: invariant
  description: agent_memory:INV-011 nothing is written unless the whole result validates
  input: a change that sets ranking.held_back_share to 1.5
  expected: refusal "ranking: ..." and both tables unchanged
  tolerance: exact
logging_tier: CORE

##### M_0_B_3 — Engine and Service Builder
job: Build the owner service, unstarted, wired to one engine per configured tier, the visibility policy, the number source and the language-model part factory.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- runtime_config: RuntimeConfig — the validated settings
- only_tier: string — optional; build just this tier, for a rebuild
outputs:
- owner_service: OwnerService — not started; holds the registry, the retrievers, the policy, the bounds, the search settings, the seed model rows, the number source and the part factory
artifacts: []
data_contracts: []
seam_contracts:
- Engines built here: LettaRecallTier (M_3_B_1), GraphitiEpisodeTier (M_4_A_2), CogneeChunkTier (M_5_B_1) and Mem0Tier (M_6_A_2), each registered as processor (M_2_A_1) and as retriever (M_8_A_2) under its tier name.
- Visibility policy: ScopeLabelPolicy or RestrictedPolicy (M_8_A_1).
- Number source: CorpusHttpSupplier (M_7_B_2) or EmbeddingDoorSupplier (M_7_B_1).
- Part factory: ModelParts with GraphitiModel, CogneeModel, LettaModel, ZepModel and Mem0Model (M_2_B_1 and the part nodes of M_3_B, M_4_B, M_4_D, M_5_B and M_6_B); called once by the owner's start with its database access.
- OwnerService is the class whose start, stop and socket M_1_A_1 owns.
dependencies:
- M_0_B_1
- M_0_D_2
invariants:
- agent_memory:INV-004
- agent_memory:INV-005
- agent_memory:INV-011
- UMST:INV-003
tunable_parameters:
  - param_name: tiers.episodic.organization_id
    default_value: "org_local_proof"
    param_type: string
    description: The Letta organization the episodic tier stores under; its presence configures the episodic tier. A stored setting with no code default.
  - param_name: tiers.relational.database_name
    default_value: "cognee_chunks"
    param_type: string
    description: The schema name of the Cognee node table in the memory database; its presence configures the relational tier. A stored setting with no code default.
intelligence_pattern: none
logic: |
  1. If only_tier is given and is not one of the four tiers, raise ConfigurationError "Unknown tier".
  2. For each configured tier (all, or only_tier), import its engine package only then, so a deployment without that tier never initializes that engine's library, logging or telemetry: episodic builds LettaRecallTier(organization_id); temporal builds GraphitiEpisodeTier; relational builds CogneeChunkTier(database_name, embedding_model when stored); personalization builds Mem0Tier(the validated Mem0 configuration, with the owner's terminal-failure type).
  3. If building an engine raises ValueError, ImportError or OSError, raise ConfigurationError "tiers.<tier> cannot be built: <reason>".
  4. If only_tier was given and nothing was built, raise ConfigurationError "tiers.<tier> is not configured".
  5. Register every engine in one ProcessorRegistry, and map each tier name to its engine as retriever. A tier left unconfigured gets neither, so its receipts stay pending and its retrieval answers unavailable; nothing is simulated.
  6. Visibility: ScopeLabelPolicy(pool_scope_labels) when labels are stored, otherwise RestrictedPolicy.
  7. Number source: when supplier is stored, CorpusHttpSupplier(base_url, requester_agent_id, timeout_seconds, original_text); else when embedding_door is stored, EmbeddingDoorSupplier(base_url, requester_agent_id, timeout_seconds, original_text, prefixes); else none, and the owner then answers every request for numbers "numbers not yet supplied". original_text reads one event's original text through the owner's own lifetime connection once the service runs.
  8. Part factory: none when language_model is not stored (the model operations then answer that no language model is configured). Otherwise a function that, given the service's database access at start, builds ModelParts from the one language_model setting with: GraphitiModel when the temporal tier and graphiti settings exist; CogneeModel when cognee settings exist; LettaModel when letta settings and the episodic tier exist; ZepModel when zep settings exist; Mem0Model when the personalization tier was built, its llm section made from language_model with the requested model name.
  9. Build OwnerService(socket path, database path, registry, retrievers, policy, max_request_bytes, drain_timeout_seconds, ranking, models, supplier, part factory). No authenticator is passed, so every caller is identified by operating-system peer credentials (M_1_A_2); no trained combiner artifact is passed.
  10. If the constructor raises ValueError (ranking absent while a tier ranks by numbers, a ranking value out of range, or socket and database naming one file), raise ConfigurationError "ranking: <reason>"; the prefix "ranking:" is used for every one of these causes.
  11. It opens no database and calls no model; the part constructors (and their own setting checks) run later, inside the owner's start.
tools:
- importlib
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_0_B_3
- tiers_built: list of string, CORE — the tier engines built
- policy: string, CORE — scope_label or restricted
- number_source: string, CORE — corpus, embedding_door or none
- model_parts_configured: boolean, CORE — whether a part factory was made
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- An engine cannot be built from its settings -> ConfigurationError naming the tier; the router returns 2
- Rebuild asked for an unconfigured tier -> ConfigurationError "tiers.<tier> is not configured"
- Ranking settings absent while a tier ranks by numbers, or out of range -> ConfigurationError "ranking: ..."
- No number source stored -> the service builds; every ranked tier reports its models left out with "numbers not yet supplied"
- No language model stored -> the service builds; model and model_parts answer "no language model is configured on this service"
tests:
- type: unit
  description: An unconfigured tier gets no engine
  input: settings configuring only episodic and temporal
  expected: the registry and retrievers hold exactly episodic and temporal
  tolerance: exact
- type: unit
  description: A rebuild of an unconfigured tier is refused
  input: only_tier personalization with no personalization settings
  expected: ConfigurationError "tiers.personalization is not configured"
  tolerance: exact
- type: invariant
  description: agent_memory:INV-005 every model comes from data
  input: settings with two model rows and a language_model naming a model
  expected: the built service's seed models are exactly the two rows and every part asks for the stored model name
  tolerance: exact
logging_tier: CORE

#### M_0_C — Presence
purpose: Give the running service its place in the ecosystem: a claimed address for the health door and a registration with a heartbeat.
inputs:
  - SOVEREIGN_MEMORY_HTTP_HOST and SOVEREIGN_MEMORY_HTTP_PORT: string — the health door's address, both or neither
  - SOVEREIGN_ROOT, AGENT_ROOT, REDIS_HOST, REDIS_PORT: string — where the shared libraries and the registry's Redis are
outputs:
  - health_listener: server — bound on the claimed address
  - registration_note: string — what the last registration attempt did
dependencies: []
tests:
  - Address free -> claimed and the door bound
  - Address held by another program -> EcosystemError naming the holder, nothing stopped
  - Redis address not given -> the note says "not registered" and the service keeps serving

##### M_0_C_1 — Port Claim
job: Claim the health door's address through the shared service supervisor before the door is bound on it.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- environment: mapping of string to string — one snapshot of the process environment
- SOVEREIGN_MEMORY_HTTP_HOST: string — the host, given together with the port or not at all
- SOVEREIGN_MEMORY_HTTP_PORT: string — a whole number from 1 to 65535
- SOVEREIGN_ROOT: string — the Sovereign tree; optional when machine.json declares it
- AGENT_ROOT: string — this project's root
outputs:
- address: Address — host and port, or none when neither is given
- health_listener: server — the door bound on the address
artifacts: []
data_contracts:
- machine.json
seam_contracts:
- Calls take_port of shared_service_supervisor with the entry file AGENT_ROOT/sovereign_memory_main.py written by M_0_A_1.
- The bound server answers every connection with the health door of M_11_A_2.
dependencies: []
invariants:
- UMST:INV-003
- agent_memory:INV-001
- agent_memory:INV-011
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Read the address once, when the presence is built: if neither host nor port is given, the address is none; if only one is given, raise EcosystemError saying both must be given together; if the port is not a whole number from 1 to 65535, raise EcosystemError naming it. No default address is ever supplied. The live address is 127.0.0.1:9110.
  2. Find the Sovereign tree: SOVEREIGN_ROOT when given; else the sovereign_root.value of machine.json two folders above AGENT_ROOT (the protocol root); else none. It never guesses a path.
  3. On start, with no address, listen on nothing and go on to registration (M_0_C_2); no door answers, so no health report can be asked for.
  4. With an address, both the tree and AGENT_ROOT are required; if either is missing raise EcosystemError naming them.
  5. Import shared_service_supervisor from <tree>/shared, adding that folder to the import path once; a missing folder or module raises EcosystemError. The module is never copied.
  6. In a worker thread, call take_port(host, port, AGENT_ROOT/sovereign_memory_main.py): when the address is free nothing is stopped; when it is held by a previous copy of this same entry file, that process is sent SIGTERM and the address is waited for; when it is held by any other program, or by a process this account cannot identify or stop, it raises AddressHeldByStranger, which becomes EcosystemError naming the holder, and nothing is stopped. The waits inside take_port (an 8 second grace and a 10 second free wait) are the shared library's own defaults, not memory settings.
  7. Bind the health door on the address (M_11_A_2 answers each connection).
  8. At stop, after the heartbeat and deregistration (M_0_C_2), close the door and wait for it to close.
  9. The claim runs after the owner has started (M_0_A_3 step 4), so a refused claim stops a service that was already serving its socket.
  10. From reading the code, not measured: superseding a previous copy is reached only when that copy holds the port without holding the database, because a second start is refused earlier, at settings load (M_0_B_1), while the first copy holds the database file.
tools:
- asyncio
- importlib
- json
shared_imports:
- shared_service_supervisor — take_port, AddressHeldByStranger
metadata_fields:
- node_id: string, CORE — M_0_C_1
- host: string, CORE — the claimed host
- port: integer, CORE — the claimed port
- superseded_processes: integer, CORE — how many previous copies take_port stopped
- refusal_reason: string, CORE — the holder named when the claim is refused
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Only one of host and port given, or a malformed port -> EcosystemError when the presence is built; the router returns 2
- Address given without SOVEREIGN_ROOT (or machine.json) or without AGENT_ROOT -> EcosystemError; the router returns 1 after stopping the started service
- Shared folder or supervisor module missing -> EcosystemError naming it; the router returns 1
- Port held by another program -> EcosystemError naming the holder, nothing stopped; the router returns 1
- No central port registry exists, so 9110 was taken by the absence of a conflict; the address is declared nowhere but the environment of the user service (known gap)
- The address is given by the environment, not a stored setting -> deliberate: the Sovereign convention gives a service's address in its environment
tests:
- type: unit
  description: A half-given address is refused
  input: SOVEREIGN_MEMORY_HTTP_HOST set and SOVEREIGN_MEMORY_HTTP_PORT unset
  expected: EcosystemError saying both must be given together
  tolerance: exact
- type: unit
  description: A free port is claimed and bound
  input: a free local port with SOVEREIGN_ROOT and AGENT_ROOT set
  expected: take_port returns 0 and a connection to the port is answered by the health door
  tolerance: exact
- type: invariant
  description: UMST:INV-003 another program's port is never taken silently
  input: a port held by an unrelated listening process
  expected: EcosystemError naming that process, which is still running afterwards
  tolerance: exact
logging_tier: CORE

##### M_0_C_2 — Registration and Heartbeat
job: Keep this service registered in the shared agent registry with a heartbeat at the registry's own interval until stop.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- environment: mapping of string to string — SOVEREIGN_ROOT (or machine.json through AGENT_ROOT), REDIS_HOST, REDIS_PORT
outputs:
- registration_note: string — "registered in the shared agent registry", "registered; heartbeat sent", "not registered: ..." or "registration failed: ..."
artifacts:
- agent_registration.json
data_contracts:
- agent_registration.json
seam_contracts:
- The registration note is read by the health door (M_11_A_2) on every request.
- register, heartbeat and deregister are the shared agent registry's own calls over the shared Redis bus.
dependencies:
- M_0_C_1
invariants:
- UMST:INV-003
- agent_memory:INV-011
tunable_parameters: []
intelligence_pattern: time_based_justified
logic: |
  1. Read the heartbeat interval from the shared agent registry module's private name _HEARTBEAT_INTERVAL_S (60 seconds today). If the tree is not given, the module cannot be imported, or it names no interval, set the note "not registered: <reason>", log a warning and end; registration is then never tried again in this run.
  2. Loop until cancelled: in a worker thread, register or beat (steps 3 to 5), keep the returned note, then sleep one interval.
  3. If the tree, REDIS_HOST or REDIS_PORT is missing, or the port is not digits, return "not registered: SOVEREIGN_ROOT, REDIS_HOST and REDIS_PORT are not all given"; the next interval tries again.
  4. First time: import shared_redis_bus, shared_agent_registry and shared_schema_validator from the tree; connect a RedisBus to the given Redis; register the record agent_id "agent_sovereign_memory", name "sovereign_memory", version read from the installed package's metadata, a one-line description, capabilities memory.capture, memory.retrieve, memory.context, memory.forget and memory.model_steps, and the endpoint GET /health with its description. Return "registered in the shared agent registry".
  5. Later times: send the heartbeat for agent_sovereign_memory and return "registered; heartbeat sent".
  6. If an attempt raises EcosystemError, OSError, ValueError, the bus's Redis-unavailable error or the schema validator's error, set "registration failed: <type>: <message>", forget the registry object so the next interval registers afresh, log a warning, and keep serving.
  7. At stop: cancel the heartbeat task and wait for it; if a registry object is held, deregister agent_sovereign_memory in a worker thread, logging a warning on failure.
  8. The registered identity is agent_sovereign_memory; by Shawn's ruling of 2026-10-28 the agent's identity is agent_memory (the repository keeps the name sovereign_memory for now), so the registry identity must become agent_memory. This is not changed in the code yet.
  9. The record this code sends names no host, no port and no socket path: it gives only the health door's path and method. The socket address must be registered so callers can find the memory; it is not registered today.
  10. The capabilities advertised are coarse: ground truth, review, load and router records are reachable on the socket but not named in the registration.
tools:
- asyncio
- importlib
shared_imports:
- shared_agent_registry — AgentRegistry
- shared_redis_bus — RedisBus, RedisUnavailableError
- shared_schema_validator — SchemaValidationError
metadata_fields:
- node_id: string, CORE — M_0_C_2
- registration_note: string, CORE — the latest note
- heartbeat_interval_seconds: float, CORE — the interval read from the registry module
- attempt_failed: boolean, CORE — whether the latest attempt raised
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Registry module names no interval, or the tree is not given -> "not registered: ..." noted and logged; registration is not retried in this run
- Redis address not given -> "not registered: ..." noted; tried again every interval; serving continues
- Redis down or the record refused -> "registration failed: ..." noted and logged; registered afresh at the next interval; serving continues
- An error of any other kind in an attempt -> ends the heartbeat task; it is not logged and is collected silently at stop (gap against UMST:INV-003, from reading the code, not measured)
- The heartbeat interval is read from the registry's private name _HEARTBEAT_INTERVAL_S rather than a declared value (known gap)
- The registered identity is agent_sovereign_memory, not agent_memory (known gap, by the 2026-10-28 ruling)
- The socket address is not registered (known gap)
- Deregistration fails at stop -> warning logged; stop continues
tests:
- type: unit
  description: Missing Redis address is noted, not fatal
  input: SOVEREIGN_ROOT given, REDIS_HOST unset
  expected: the note "not registered: SOVEREIGN_ROOT, REDIS_HOST and REDIS_PORT are not all given" and the service still serving
  tolerance: exact
- type: unit
  description: Second attempt sends a heartbeat
  input: a reachable registry over a throwaway Redis, two intervals elapsed
  expected: notes "registered in the shared agent registry" then "registered; heartbeat sent"
  tolerance: exact
- type: invariant
  description: UMST:INV-003 a registry outage is noted and logged
  input: Redis stopped after the first registration
  expected: the note begins "registration failed:" with the error type, a warning is logged, and the socket still answers
  tolerance: exact
logging_tier: CORE

#### M_0_D — Database Foundation
purpose: Give every part of memory safe asynchronous access to the one DuckDB file, and create its tables at every start.
inputs:
  - db_path: string — the memory database file
outputs:
  - lifetime_connection: AsyncConnection — the owner's connection for the service's life
  - core_tables: tables — events, receipts, removals, payloads, rebuilds, rebuild receipts and router records
dependencies: []
tests:
  - A SQLite file given as the database -> refused, the file unchanged
  - A fresh file -> every core table created in one transaction
  - A file with the old rebuilds shape -> migrated with every record kept

##### M_0_D_1 — Asynchronous DuckDB Dialect
job: Give every part of memory asynchronous access to the one DuckDB file through one serialised worker per connection.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- database: string — a plain local file path or ":memory:"
- config: mapping — DuckDB options, merged with any options carried by a SQLAlchemy address
- native_handle: DuckDB connection — for a borrowed engine, a handle its owner keeps
outputs:
- connection: AsyncConnection — a worker-owned connection with buffered cursors
- engine: SQLAlchemy engine — the duckdb+aio dialect, or a borrowed engine on a native handle
artifacts: []
data_contracts: []
seam_contracts:
- Used by the owner service (lifetime connection and one connection per capture, forget, router record, part write and expiry), and by the Letta store, the Graphiti driver and the Cognee adapters, which reach it through the dialect or a borrowed engine.
- The dialect is registered for SQLAlchemy as duckdb.aio; the Alembic implementation is registered for the dialect name duckdb.
dependencies: []
invariants:
- UMST:INV-001
- UMST:INV-003
- agent_memory:INV-001
- agent_memory:INV-003
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. connect(database, options): merge the DuckDB options with any options carried by the address, create an AsyncConnection with a one-thread worker and a gate, and open the database on that same worker. If opening fails, close the connection and raise.
  2. Opening (connect_native): a path that starts with a scheme such as "name:" is refused (a plain local path is required). Read the first 16 bytes: a SQLite header is refused with "SQLite storage is unsupported; explicit migration to DuckDB is required. The original file has not been modified." A missing file follows DuckDB's normal creation. Any other file-system error is raised as an input-output error. After opening, if the current database is not native DuckDB storage, close the handle and refuse.
  3. run(job): under the gate, refuse when closed; run the job on the worker and return its result. Every query, begin, commit and rollback goes through run, so one connection never runs two jobs at once.
  4. If the caller is cancelled while a job runs: interrupt the native job, wait until it finishes, close the handle on the worker (which discards any transaction), shut the worker down and re-raise the cancellation. That connection is never used again.
  5. begin, commit and rollback track one transaction flag on the worker: begin starts a transaction only when none is open; commit and rollback finish it only when one is open; rollback on a closed connection does nothing.
  6. Cursors are logical and buffered: execute runs the statement and keeps every row and the description before returning; fetchone, fetchmany and fetchall read the kept rows; executemany runs a list of parameter sets. Closing a cursor never closes the handle. A cursor used after its connection closes is refused.
  7. close is safe to repeat; it waits for any running job, closes the handle on the worker and shuts the worker down.
  8. The SQLAlchemy dialect (name duckdb, driver aio) keeps DuckDB's SQL compilation, uses question-mark parameters, turns the statement cache off, gives an in-memory database no pool and a file a queued pool, refuses server-side cursors (streaming is not supported), pings with a plain select, and reads indexes and foreign keys from DuckDB's own catalogue for the right database and schema.
  9. borrowed_engine(native) makes a SQLAlchemy engine whose one connection is a handle owned elsewhere: its begin, rollback and close do nothing and its commit is refused, so statements run inside the owner's transaction and only the owner finishes it; it opens no other connection, never resets the handle and never changes its current schema.
tools:
- duckdb
- sqlalchemy
- duckdb_engine
- alembic
- asyncio
- concurrent.futures
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_0_D_1
- database: string, DEBUG — the path opened
- job_cancelled: boolean, CORE — whether a cancellation closed the connection
- refused_reason: string, CORE — why an open was refused
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- A path with a scheme prefix -> NotSupportedError, nothing opened
- A SQLite file -> NotSupportedError, the file unchanged
- Storage that is not native DuckDB -> NotSupportedError, the handle closed
- A file-system error reading the path -> an input-output error raised
- Another process holds the file -> DuckDB's lock error raised to the caller
- A job cancelled mid-run -> the job interrupted and drained, the connection closed for good, the cancellation re-raised
- Use after close -> InterfaceError
- A server-side cursor asked for -> NotSupportedError
- A commit through a borrowed engine -> TransactionException; only the handle's owner commits
tests:
- type: unit
  description: A SQLite file is refused untouched
  input: a file starting with the SQLite header
  expected: NotSupportedError and the file's bytes unchanged
  tolerance: exact
- type: unit
  description: Jobs on one connection never interleave
  input: two concurrent run calls on one connection, each recording its start and end
  expected: the second starts only after the first ends
  tolerance: exact
- type: invariant
  description: agent_memory:INV-003 a borrowed engine cannot commit the owner's transaction
  input: a SQLAlchemy session on a borrowed engine calling commit inside the owner's open transaction
  expected: TransactionException, and the owner's later rollback leaves no row written
  tolerance: exact
- type: invariant
  description: UMST:INV-001 a cancelled write loses nothing committed
  input: a write cancelled mid-job on a connection holding an open transaction
  expected: the uncommitted rows are absent, every earlier committed row is present, and the connection refuses further use
  tolerance: exact
logging_tier: CORE

##### M_0_D_2 — Core and Search Schema Setup
job: Create, at every start and before the socket opens, every table the memory database needs, migrating an older shape in place.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- lifetime_connection: AsyncConnection — the owner's connection, just opened
- search_settings: SearchSettings — the validated ranking settings, or none
- seed_models: list of model rows — from the models setting
outputs:
- core_tables: tables — memory_events, memory_receipts, memory_removals, memory_payloads, memory_rebuilds, memory_rebuild_receipts, memory_router_records
- search_tables: tables — the numbers, combiner, ground-truth and search-settings tables
artifacts:
- memory_events
- memory_receipts
- memory_removals
- memory_payloads
- memory_rebuilds
- memory_rebuild_receipts
- memory_router_records
data_contracts:
- memory_events
- memory_receipts
- memory_removals
- memory_payloads
- memory_rebuilds
- memory_rebuild_receipts
- memory_router_records
seam_contracts:
- The search tables are made by the initializer each owning node supplies: the model registry and checks (M_7_A_1), pieces and numbers (M_7_A_2), owed requests and retirements (M_7_A_3), the combiners and decisions (M_8_C_2), memory_settings (M_8_C_1), and the memory_gt tables and views (M_9_A_1, M_9_A_2, M_9_A_3).
- Seed model rows are registered through the numbers store (M_7_A_1); the starting combiner is seeded by ensure_starting_combiner (M_8_C_2).
- Each tier engine's own storage (Letta, Graphiti, Cognee, Mem0) is created by its initialize_storage hook through the dispatcher (M_2_A_1), each in its own transaction.
dependencies:
- M_0_D_1
invariants:
- UMST:INV-001
- agent_memory:INV-001
- agent_memory:INV-005
- agent_memory:INV-006
- agent_memory:INV-009
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Core schema, in one transaction on the lifetime connection: create, when absent, memory_events (event_id key, schema_version, actor_agent_id, event_type, created_at, payload_ref, payload_sha256, exactness, outcome, raw_event JSON), memory_receipts (event_id and tier key, processor_id, processor_version, projection_version, status, attempt_id, attempted_at, completed_at, error_ref; tied to its event), memory_removals (target_event_id key, removal_event_id, operation, requested_by, requested_at; both ids tied to events), memory_payloads (event_id key, payload_sha256, payload bytes; tied to its event), memory_rebuilds (request_id key, tier, model_labels as JSON text defaulting to an empty list, status, requested_at), memory_rebuild_receipts (request_id, event_id and tier key, receipt JSON) and memory_router_records (principal and record_key key, kind, writer, content, episode, episode_source, created_at).
  2. Inside the same transaction, migrate an old rebuilds table: if memory_rebuilds still has the column embedding_identity, copy every row into a table of the new shape with that value as the one entry of model_labels, drop the old table and rename the new one. A table already in the new shape is left alone. No earlier record is lost.
  3. Commit. Any failure rolls the transaction back and raises; the owner's start then closes the connection and removes nothing but its own socket.
  4. Search storage: make the numbers store and the numbers services (with the number source). In one transaction create, when absent, the numbers schema, the combiner schema, the ground-truth schema and the search-settings table; when search settings are declared, append to memory_settings every value that differs from its latest record, with the source "memory_runtime_settings table, [ranking]" and the start time.
  5. Register each seed model row, each in its own transaction: a new row is added with its own numbers table; an identical row already stored is left as it is; a row whose width, kind or supplier differs from the stored one is refused and the start fails.
  6. Seed the starting combiner once: when no combiner is current and at least one embedding model is registered, record rrf-start (reciprocal rank fusion over every registered embedding model, reading merging_constant) with the decision "seeded". A model registered later joins no combiner until a candidate naming it wins or the owner adopts one by ruling.
  7. After the dispatcher is built, each tier engine that has its own storage hook creates its storage in its own short transaction (committed on success, rolled back and raised on failure).
  8. Tables are made with CREATE ... IF NOT EXISTS; the only migration is step 2. No index is created beyond the primary keys and the unique numbers-table name. memory_rebuild_receipts is written by rebuilds and read by nothing today. Nothing here deletes a row.
tools:
- duckdb
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_0_D_2
- tables_created: list of string, DEBUG — tables that were absent and are now created
- rebuilds_migrated: boolean, CORE — whether the old rebuilds shape was migrated
- settings_recorded: list of string, CORE — search setting names appended to memory_settings
- starting_combiner_seeded: boolean, CORE — whether rrf-start was seeded at this start
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Any core table statement fails -> the transaction rolls back, the error is raised, the start fails and the database is released
- A seed model row conflicts with a stored row of the same id and revision -> refused, the start fails
- An engine's storage hook fails -> its transaction rolls back, the error is raised, the start fails
- The file was written by an older shape other than the rebuilds column -> not migrated; only the rebuilds migration exists
tests:
- type: unit
  description: A fresh file gets every core table
  input: an empty DuckDB file and the owner's start
  expected: the seven core tables exist with their keys
  tolerance: exact
- type: unit
  description: The starting combiner is seeded once
  input: two starts on one file with one embedding model row
  expected: exactly one rrf-start combiner and one "seeded" decision
  tolerance: exact
- type: invariant
  description: UMST:INV-001 the old rebuilds shape migrates without loss
  input: a file whose memory_rebuilds has embedding_identity and three rows
  expected: three rows in the new shape, each model_labels holding its old identity as the one entry
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 setup never removes an original
  input: a start on a file holding events and payloads
  expected: every memory_payloads row present before the start is present after it, byte for byte
  tolerance: exact
logging_tier: CORE

### M_1 — Event Capture and Lifecycle
description: Takes memory events from local agents over the owner's Unix socket, stores each one durably with its verified original bytes and one pending receipt per tier, keeps the shared router's bookkeeping notes, withholds forgotten and expired events from every answer, and reads stored events and their receipts back to callers allowed to see them.
dependencies:
  - M_0
entry_conditions:
  - M_0 is VERIFIED
  - test fixtures are prepared (a throwaway memory database holding valid settings, made under the test's own temporary folder)
exit_conditions:
  - all submodules VERIFIED
  - all artifacts schema-valid
  - all tests passed
  - end-to-end module test passed
module_test: Start the service on a throwaway database, capture a version 2 event with its payload bytes through the socket, stop and restart the service, load the event back with byte-identical text and four receipts, forget it as its producer, and confirm that status names the removal event, that retrieval withholds the event, and that its original payload row is still stored.

#### M_1_A — Transport
purpose: Open the owner's socket safely, read one length-framed request per connection, name the caller from the operating system's peer credentials, and close down without touching any file the service did not create.
inputs:
  - socket_path: path — service.socket_path, resolved against the database's folder
  - framed_request: bytes — a 4-byte big-endian length, then that many bytes of UTF-8 JSON
outputs:
  - listening_socket: Unix stream socket — one request per connection
  - principal_and_request: object pair — the authenticated caller and the parsed request, or one error reply
dependencies:
  - M_0_D_1
  - M_0_D_2
tests:
  - A stale socket file left by a crashed run -> removed and replaced at start
  - A regular file at the socket path -> start refused and the file left untouched
  - A request longer than service.max_request_bytes -> one "Payload Too Large" error reply, connection closed
  - A request body naming another actor -> the event is stored under "os-uid-<uid>" of the connecting process

##### M_1_A_1 — Socket Open and Close
job: Own the service's Unix socket file for the life of the process, from a refusal-checked bind at start to removal of only that same file at stop.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- socket_path: path — the socket file named by service.socket_path
- db_path: path — the memory database file the service owns
- process_pending: boolean — whether start also begins the owed-work run; false only for the trusted rebuild of M_2_A_3
outputs:
- listening_socket: Unix stream socket — accepting one connection per request once start returns
- socket_identity: pair of integers — device and inode of the socket this service bound, remembered so stop removes only that file
artifacts: []
data_contracts: []
seam_contracts:
- owed_work_trigger — after the bind succeeds, start hands the owed-work run (M_2_A_2 process_pending) to a background task when process_pending is true
dependencies:
- M_0_D_1
- M_0_D_2
invariants:
- UMST:INV-001
- agent_memory:INV-001
tunable_parameters:
  - param_name: service.socket_path
    default_value: "sovereign_memory.sock"
    param_type: string
    description: Where the owner's Unix socket is bound, resolved against the database's folder; a stored setting with no code default, refused when it names the database file itself.
  - param_name: service.drain_timeout_seconds
    default_value: "10"
    param_type: float
    description: How long stop, and a failed start, wait for open client tasks and background processing before cancelling them; a stored setting with no code default.
intelligence_pattern: none
logic: |
  1. At construction, refuse when the socket path and the database path name the same file (the same resolved path, or the same existing file).
  2. Before acquiring anything, inspect the socket path. Nothing there: free. A symlink, regular file, directory or any other non-socket: raise FileExistsError and delete nothing. A socket owned by another user: raise PermissionError. A socket something answers on: raise FileExistsError naming it. A socket nobody answers on (connection refused or gone): remember its device and inode as stale. Any other connect error: raise FileExistsError naming the error.
  3. Open the lifetime connection to the database file; this one connection holds the file for the whole life of the process, so no other process can write it (agent_memory:INV-001).
  4. Create the core and search tables (M_0_D_2), build the dispatcher, the retrieval service and the review screen, build the language-model router when one is configured (M_2_B_1), then let each registered engine create its own storage in a short transaction of its own.
  5. Create the socket's folder when it is missing. If a stale socket was found, inspect the path again; if it is no longer the very same file, raise FileExistsError; otherwise unlink it.
  6. Bind a new socket directly (never through asyncio's path helper, which could unlink a socket that appeared after the check), record its device and inode, and start serving connections through M_1_A_2.
  7. If process_pending is true, start the owed-work run in the background; a failed start never reaches this step, so no engine work begins on a half-started service.
  8. If any step from 3 onward fails: close the listener and the server, remove the socket only if it is the file this service bound, wait for background processing up to service.drain_timeout_seconds and cancel what remains, close the lifetime connection, then raise the original error.
  9. At stop: close the server; wait up to service.drain_timeout_seconds for open client tasks, then cancel and await the rest; wait the same bound for background processing, then cancel stragglers (a model call's own thread runs on to the provider's request time limit, and its receipt stays "processing" for the next start to retry); close the lifetime connection, which rolls back any uncommitted job; unlink the socket path only if it is still a socket with the remembered device and inode.
tools:
- asyncio
- socket
- os
- stat
- duckdb
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_1_A_1
- socket_path: string, CORE — the path bound
- stale_socket_replaced: boolean, CORE — whether a stale socket was found and replaced at start
- cancelled_tasks: integer, DEBUG — client and processing tasks cancelled at the drain bound during stop
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Socket path is a regular file, symlink or directory -> FileExistsError before anything is acquired; nothing is deleted
- Socket path is another user's socket -> PermissionError; nothing is deleted
- Another service answers on the socket path -> FileExistsError naming the path
- The database file is held by another process -> the lifetime connection fails, the socket is never bound, the error is raised and the start exits
- The stale socket changed between the two inspections -> FileExistsError; the new file is left alone
- Any later start step fails -> listener and server closed, only this service's own socket removed, connection released, error raised again
- Work still running at the drain bound -> cancelled; its receipt stays "processing" and is retried at the next start
- The code sets no permission bits of its own on the socket file, so which local users may connect is decided by the folder and the process's file-creation mask (stated by reading the code, not measured)
- The socket's address is not registered with the agent registry; only the health door's address is (known gap, recorded in the settings and runtime facts)
tests:
- type: unit
  description: A stale socket from a crashed run is replaced
  input: a socket file at service.socket_path that nothing listens on, owned by the service's user
  expected: start succeeds, the old file is unlinked, a new socket is bound and its device and inode are remembered
  tolerance: exact
- type: unit
  description: A non-socket at the socket path is refused
  input: a regular file at service.socket_path
  expected: FileExistsError before the database is opened; the file is unchanged
  tolerance: exact
- type: unit
  description: Stop removes only its own socket
  input: after start, the socket file is replaced by another socket of the same user, then stop is called
  expected: the replacement file is left in place and the lifetime connection is closed
  tolerance: exact
- type: invariant
  description: agent_memory:INV-001 one process owns the database file
  input: a second service process started on the same database file while the first is serving
  expected: the second start fails on the database lock, binds no socket, and the first service's socket and file are untouched
  tolerance: exact
logging_tier: CORE

##### M_1_A_2 — Request Reader and Caller Identity
job: Turn one socket connection into one parsed request carrying the caller's operating-system identity, or into one error reply.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- connection_stream: asyncio stream pair — the reader and writer of one accepted connection
- framed_request: bytes — a 4-byte big-endian length, then that many bytes of UTF-8 JSON
outputs:
- principal: AuthenticatedPrincipal — agent_id "os-uid-<uid>" from the peer credentials, scope label "restricted"
- request: JSON object — the parsed request, with op defaulting to "capture"
- error_reply: JSON object — status "error", message and details, written without a length prefix
artifacts:
- socket_request.json
- error_reply.json
data_contracts:
- socket_request.json
- error_reply.json
seam_contracts:
- ConnectionAuthenticator — an optional registered authenticator whose authenticate_stream names the principal instead of the peer credentials
- operation_handoff — the parsed op, request and principal are handed to the operation dispatcher (M_11_A_1); its reply is written here as plain JSON
dependencies:
- M_1_A_1
invariants:
- UMST:INV-001
- agent_memory:INV-010
tunable_parameters:
  - param_name: service.max_request_bytes
    default_value: "1048576"
    param_type: int
    description: The largest framed request the socket accepts, in bytes; a longer one gets a "Payload Too Large" reply. A stored setting with no code default.
intelligence_pattern: none
logic: |
  1. Read exactly 4 bytes. If the client closes first, log a warning, send nothing and close.
  2. Read the length as a big-endian integer. If it is larger than service.max_request_bytes, reply "Payload Too Large" naming the bound and close without reading the rest.
  3. Read exactly that many bytes. If the client closes first, log a warning, send nothing and close.
  4. Decode the bytes as UTF-8 JSON. If the JSON is malformed, reply "Invalid JSON payload" with the parser's message.
  5. Take op (missing means "capture"), event and payload_content_hex. If payload_content_hex is present but not valid hexadecimal, reply "Malformed Hex Payload".
  6. If op is not one of the 19 operations, reply "Unsupported operation" naming it. If op is capture and event is missing or empty, reply "Missing 'event' in payload".
  7. Name the caller. If an authenticator is registered, ask it; its RuntimeError, ValueError or ConnectionError becomes "Authentication Error". Otherwise read the peer credentials from the transport, or through SO_PEERCRED on the socket; when neither exists, reply "Configuration Error". The principal is "os-uid-<uid>" with scope "restricted"; nothing in the request body can name or change it (agent_memory:INV-010).
  8. Hand op, request and principal to the operation (M_11_A_1). A PermissionError becomes "Visibility Refused", a ValueError "Validation Error", and a RuntimeError, TimeoutError, ConnectionError or database error is logged with its traceback and becomes "Internal Error" naming the error type.
  9. Write the success reply as plain JSON with no length prefix, then close the connection in every case, success or failure.
tools:
- asyncio
- json
- socket
- struct
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_1_A_2
- operation: string, CORE — the op named, or "capture" when missing
- principal: string, CORE — the os-uid principal the request ran as
- error_message: string, CORE — the error reply's message, when one was sent
- request_bytes: integer, DEBUG — the declared length of the framed request
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Client closes before the whole request -> warning logged, no reply, no work left behind
- Declared length above service.max_request_bytes -> "Payload Too Large" reply, rest of the bytes unread
- Malformed JSON, bad hexadecimal or an unknown op -> one error reply naming the cause
- No authenticator and no peer credentials -> "Configuration Error"
- A request body that is not valid UTF-8, is JSON but not an object, or carries a payload_content_hex that is not text -> the exception escapes, the connection closes with no reply and the service's own code logs nothing (known gap, by reading the code)
- An exception outside the caught set (for example KeyError or TypeError inside an operation) -> the connection closes with no reply and the task is only tracked, not logged (known gap)
- A client that sends a length and then stalls -> no read deadline exists, so its task waits until the client closes or stop cancels it at the drain bound (known gap, by reading the code)
- The reply carries no length prefix although the request must carry one (asymmetric framing; clients read until the connection closes)
tests:
- type: unit
  description: An oversized request is refused before it is read
  input: a length header of service.max_request_bytes plus one
  expected: one error reply with message "Payload Too Large", connection closed, no operation run
  tolerance: exact
- type: unit
  description: A missing op means capture
  input: a framed request {"event": <valid legacy event>} with no op
  expected: the event is captured and the reply has status "success"
  tolerance: exact
- type: invariant
  description: agent_memory:INV-010 identity comes only from the operating system
  input: a capture whose event names actor_agent_id "someone-else", sent by a process running as uid 1000
  expected: the stored event's actor_agent_id is "os-uid-1000"
  tolerance: exact
logging_tier: CORE

#### M_1_B — Capture
purpose: Check each incoming event against the memory_event contract and store it, its verified original bytes and its four pending receipts in one transaction before the capture is acknowledged; keep the shared router's write-once notes beside it.
inputs:
  - memory_event: JSON object — a version 1 or version 2 envelope
  - payload_content: bytes — the original payload, decoded from payload_content_hex
  - principal: AuthenticatedPrincipal — from M_1_A_2
outputs:
  - stored_event: JSON object — the event as stored, with its durable receipts, and created true or false
  - router_record: JSON object — one bookkeeping note, as stored
dependencies:
  - M_1_A_2
  - M_0_D_2
tests:
  - A version 2 event whose bytes match its payload_sha256 -> stored with four pending receipts, created true
  - The identical event sent again -> created false, nothing new stored
  - The same event_id with different content -> refused with a conflict, the first event unchanged
  - A second record_claim of one key -> created false, the first record returned unchanged

##### M_1_B_1 — Event Contract Validator
job: Decide whether one event matches the version 1 or version 2 memory_event contract before anything is stored.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- event_data: JSON object — the envelope as received
- payload_content: bytes or null — the payload bytes, when the caller supplied them
outputs:
- verdict: none or ValueError — nothing on acceptance; a ValueError naming the first failure otherwise
artifacts:
- memory_event_schema.json
data_contracts:
- memory_event_schema.json
- memory_event.json
seam_contracts: []
dependencies: []
invariants:
- UMST:INV-001
- agent_memory:INV-002
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. At construction, load memory_event_schema.json from the schemas folder beside the code (the path is derived from the file's own location), check that it is a valid Draft-07 schema, and build the validator.
  2. Check the event against the schema. It must match exactly one of two shapes and carry no extra field. Version 1 (schema_version "1.0.0"): event_id, tier (episodic, temporal, relational or personalization), content, content_type (fact, observation, correction, inference or preference), ttl_seconds (an integer of 0 or more, or null), entity_refs, agent_id, source_artifact_id, confidence (0 to 1), source (observation, inference, human_correction or llm_output) and timestamp. Version 2 (schema_version 2.x.y): event_id, actor_agent_id, event_type, created_at, source_artifact_id, work_id, parent_event_id, causation_event_id, source_revision, recipe_version, model_occupant_id, program_version, payload_ref, payload_sha256 (64 lowercase hexadecimal characters), outcome, exactness (observed, derived or inferred) and tier_receipts holding all four tiers, each receipt with event_id, processor_id, processor_version, projection_version, status (pending, processing, processed, processed_no_new_fact, retryable_failure, terminal_failure or unknown), attempt_id, attempted_at, completed_at and error_ref. On any failure raise ValueError "Event failed schema validation" with the first error's message.
  3. For version 2, every receipt's event_id must equal the envelope's event_id; otherwise raise ValueError naming the tier. When bytes are supplied, their SHA-256 must equal payload_sha256; otherwise raise ValueError "Payload SHA256 mismatch" with both values.
  4. For version 1 with a non-zero ttl_seconds, the timestamp must parse as ISO 8601; otherwise raise ValueError saying an event with a ttl needs an ISO 8601 timestamp.
  5. Return nothing. The validator never stores, repairs or rewrites an event.
tools:
- jsonschema
- hashlib
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_1_B_1
- schema_version: string, CORE — the version the event declared
- refusal_reason: string, CORE — the first failure's message, when refused
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Schema file missing or not a valid Draft-07 schema -> the validator cannot be built and the service does not start
- Event matches neither shape, or carries an extra field -> ValueError with the first schema error only
- Version 2 receipt bound to another event_id -> ValueError naming the tier
- Version 2 bytes whose hash differs -> ValueError "Payload SHA256 mismatch"
- Client-supplied receipt states such as "processed" are not refused here; the capture step stores every receipt as pending instead (M_1_B_2), which is how the schema's "refuse fabricated legacy completions" is met
- The schema text says the semantic validator enforces idempotency; idempotency is enforced in M_1_B_2, not here
- A version 1 ttl_seconds of 0 is accepted and never expires (only a positive ttl counts in M_1_C_2), while the schema text says only null means permanent
- A version 2 created_at is not checked as ISO 8601 here
tests:
- type: unit
  description: Every receipt must name its own envelope
  input: a version 2 envelope whose temporal receipt carries a different event_id
  expected: ValueError naming the temporal tier
  tolerance: exact
- type: unit
  description: An extra field is refused
  input: a version 1 event with an added field "priority"
  expected: ValueError "Event failed schema validation"
  tolerance: exact
- type: invariant
  description: agent_memory:INV-002 bytes are verified before capture can be acknowledged
  input: a version 2 envelope with payload bytes whose SHA-256 differs from payload_sha256
  expected: ValueError "Payload SHA256 mismatch" and nothing reaches the database
  tolerance: exact
logging_tier: CORE

##### M_1_B_2 — Durable Capture
job: Commit one accepted event, with its verified original bytes plus one pending receipt per tier, in a single transaction before acknowledging the capture.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- event_data: JSON object — a version 1 or version 2 envelope from the capture request
- payload_content: bytes or null — the payload bytes decoded from payload_content_hex
- principal: AuthenticatedPrincipal — the caller named by M_1_A_2
outputs:
- capture_reply: JSON object — status "success", the stored event with its durable tier_receipts, and created true for the call that wrote it or false for an identical repeat
- stored_rows: database rows — one memory_events row, one memory_payloads row and four memory_receipts rows, committed together
artifacts:
- capture_reply.json
data_contracts:
- memory_event.json
- capture_reply.json
seam_contracts:
- owed_work_trigger — after the commit, processing of this event on every registered tier starts in the background (M_2_A_2 process_event); the reply never waits for it
dependencies:
- M_1_B_1
- M_1_A_2
- M_0_D_2
invariants:
- UMST:INV-001
- agent_memory:INV-002
- agent_memory:INV-003
- agent_memory:INV-006
- agent_memory:INV-010
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Overwrite the actor field with the authenticated principal: agent_id for schema_version "1.0.0", actor_agent_id otherwise. The request body never decides who produced the event.
  2. Open a connection of its own for this capture, so a capture never waits behind engine work on the lifetime connection, and close it afterwards.
  3. Copy the envelope so the caller's own object is not altered. Refuse a removal argument from anything but the owner's own removal capture (M_1_C_1, M_1_C_2).
  4. Validate the event (M_1_B_1).
  5. Refuse an event_type (or version 1 content_type) that begins "system." unless the owner's own code is capturing it.
  6. For version 2: payload_ref must be "inline" (any other value is refused naming it), the bytes must be present, and their SHA-256 must equal payload_sha256.
  7. Normalise for storage: the actor; the event type; created_at (the version 1 timestamp); payload_ref "inline" for version 1; payload_sha256 from the envelope for version 2 and sixty-four zeros for version 1 (the real hash of a version 1 payload is kept in memory_payloads); exactness "observed" and outcome "unknown" when absent. The stored payload is the version 2 bytes, or the version 1 content text encoded as UTF-8.
  8. Build four receipts, all "pending" with no attempt. A version 2 envelope keeps its own processor_id, processor_version and projection_version; anything absent, and every version 1 receipt, takes processor "unknown" and versions "0.0.0". A receipt state the caller sent is never stored (agent_memory:INV-003).
  9. In one transaction, look for the event_id. If it exists: the actor, the stored hash and the immutable envelope (the envelope with each receipt reduced to its event_id, processor_id, processor_version and projection_version) must all match, otherwise roll back and raise ValueError "Conflict" naming what differs. For an identical repeat, store the payload bytes if the earlier capture stored none, record the removal row if this is the owner's removal capture, read the durable receipts as they stand, commit, and return them with created false.
  10. Otherwise insert the event row, the payload row (with the SHA-256 of the stored bytes), the four receipt rows and, for an owner removal capture, the removal row; commit; return the event with its four pending receipts and created true. The event is acknowledged only after this commit (agent_memory:INV-002).
  11. If two identical captures race and this one loses the insert on a constraint or transaction conflict, run the same capture once more; it takes the repeat path of step 9.
  12. Start processing of the event in the background (M_2_A_2) and reply with the stored event and created. Acknowledging capture is distinct from completing it: the reply's receipts are the pending ones just committed.
tools:
- duckdb
- hashlib
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_1_B_2
- event_id: string, CORE — the captured event
- schema_version: string, CORE — 1.0.0 or 2.x.y
- created: boolean, CORE — whether this call wrote the event
- payload_bytes: integer, DEBUG — length of the stored original
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Version 2 bytes missing, or their hash differs -> Validation Error reply; nothing stored
- payload_ref other than "inline" -> Validation Error naming it; references to bytes stored elsewhere are not built
- event_type beginning "system." from a client -> Validation Error; reserved for the owner's own removals
- Same event_id with a different actor, hash or immutable envelope -> Validation Error "Conflict"; the first event is unchanged
- A database error during the insert -> the whole transaction rolls back, nothing of the event remains, Internal Error reply
- A capture whose event is not a JSON object -> the attribute error escapes before validation, no reply is sent (the gap stated in M_1_A_2)
- A version 1 event gets four receipts but is owed only to its own tier, so its other three receipts stay "pending" for ever and status never reports it complete (stated in M_1_D_1)
- No statement in the owner's code deletes a memory_events or memory_payloads row, so originals are never discarded (agent_memory:INV-006); erasing bytes under a retention policy is not built
tests:
- type: unit
  description: A committed capture survives a restart
  input: a version 2 event with payload bytes captured, then the service stopped and started again
  expected: load returns the same envelope, byte-identical payload text and four receipts
  tolerance: exact
- type: unit
  description: An identical repeat stores nothing new
  input: the same version 2 event and bytes captured twice
  expected: the first reply has created true, the second created false, and one row each in memory_events and memory_payloads
  tolerance: exact
- type: unit
  description: Reusing an id with different content is refused
  input: a second event with an existing event_id and a different payload
  expected: Validation Error naming the conflict; the stored event and bytes are unchanged
  tolerance: exact
- type: invariant
  description: agent_memory:INV-002 nothing is acknowledged before event, bytes and four pending receipts commit together
  input: a capture during which the fourth receipt insert fails
  expected: no reply of success, and no event, payload or receipt row for that event_id in the database
  tolerance: exact
- type: invariant
  description: agent_memory:INV-003 a capture never stores a claimed completion
  input: a version 2 envelope whose four receipts all say "processed"
  expected: all four stored receipts are "pending" with no attempt_id
  tolerance: exact
logging_tier: CORE

##### M_1_B_3 — Router Bookkeeping Records
job: Keep the shared router's write-once bookkeeping notes per caller, apart from memories.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- key: string — the record's key, unique per principal
- kind: string — what the note is (for example a claim to process an event, its result or a wait)
- writer: string — who wrote it
- content: string — the note's text
- episode: string or null — optional episode name
- episode_source: string or null — optional episode source
- principal: AuthenticatedPrincipal — the caller named by M_1_A_2
outputs:
- router_record_reply: JSON object — created (record_claim) or found (record_get), and the record as stored
artifacts:
- router_record.json
data_contracts:
- router_record.json
seam_contracts: []
dependencies:
- M_1_A_2
- M_0_D_2
invariants:
- UMST:INV-001
- agent_memory:INV-010
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. record_claim: key, kind, writer and content must each be non-empty text, and episode and episode_source must be text or absent; otherwise raise ValueError "record_claim needs key, kind, writer and content as text".
  2. On a connection of its own, in one transaction, insert the row (principal, key, kind, writer, content, episode, episode_source, created_at from the service clock) with "on conflict do nothing"; created is true only when the insert returned a row; commit.
  3. Read back the record as stored for (principal, key) and reply with created and the record. A later call with the same key gets created false and the first record unchanged: the first writer wins and nothing is ever overwritten or deleted.
  4. record_get: key must be non-empty text, otherwise ValueError. Read (principal, key) on the lifetime connection; reply found false with record null when absent, otherwise found true with record_key, kind, writer, content, episode, episode_source and created_at.
  5. A record is the router's own note, not a memory: no tier ever processes it and it has no receipts.
tools:
- duckdb
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_1_B_3
- record_key: string, CORE — the key claimed or read
- created: boolean, CORE — whether this call wrote the record
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- A missing or non-text field -> Validation Error; nothing written
- A database error during the insert -> rolled back, Internal Error reply
- Records never expire and forget does not apply to them; they are bounded only by service.max_request_bytes per request
tests:
- type: unit
  description: The first writer of a key wins
  input: two record_claim calls with the same key and different content from one principal
  expected: the first reply has created true, the second created false with the first content
  tolerance: exact
- type: unit
  description: An absent record is reported, not invented
  input: record_get for a key never claimed
  expected: found false and record null
  tolerance: exact
- type: invariant
  description: agent_memory:INV-010 a caller sees only its own records
  input: a record claimed by os-uid-1000 under key "k", then record_get "k" from os-uid-1001
  expected: found false for os-uid-1001
  tolerance: exact
logging_tier: CORE

#### M_1_C — Removal and Expiry
purpose: Make forgetting and expiry durable owner-authored events that withhold their target from every answer the moment they commit, hand the removal to every tier through the ordinary receipts, and say which events are still live.
inputs:
  - forget_request: JSON object — the event_id the producer wants forgotten
  - stored version 1 events with a positive ttl_seconds
outputs:
  - removal_event: version 2 envelope — system.forget or system.expire, with its four pending receipts
  - live_map: mapping — event_id to producer for events still live
dependencies:
  - M_1_B_2
  - M_1_D_1
tests:
  - The producer forgets its event -> removal recorded, event withheld at once, complete true only after every tier processed the removal
  - Another principal asks to forget it -> Visibility Refused, nothing recorded
  - A version 1 event whose ttl has elapsed -> left out of the live map before and after its expiry event is captured
  - Forget asked twice -> the same removal event id both times, nothing new stored

##### M_1_C_1 — Forget
job: Turn a producer's request to forget one of its events into a durable owner-authored removal that withholds the event at once.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- event_id: string — the event to forget
- principal: AuthenticatedPrincipal — the caller named by M_1_A_2
outputs:
- forget_reply: JSON object — the removal event's id, removed_by, its receipts as they stand, and complete
- removal_event: version 2 envelope — event_type system.forget, actor owner-system, with a JSON payload naming the target
artifacts:
- receipt_status.json
- removal_payload.json
data_contracts:
- receipt_status.json
- removal_payload.json
seam_contracts:
- owed_work_trigger — after the removal commits, processing of the removal event on every registered tier starts in the background (M_2_A_2 process_event); each tier removes its projection in its own apply
dependencies:
- M_1_B_2
- M_1_D_1
invariants:
- UMST:INV-001
- agent_memory:INV-006
- agent_memory:INV-010
- agent_memory:INV-003
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. event_id must be non-empty text, otherwise raise ValueError "forget needs the event_id to forget".
  2. On a connection of its own, load the target with its verified bytes (M_1_D_1); an unknown event raises ValueError "Unknown event".
  3. Only the producer may forget: if the target's actor is not the principal, raise PermissionError "Only the producer of an event may forget it".
  4. Capture the removal (ingest_system_removal with operation "forget", requested_by the principal, requested_at now, details requested_by and reason "forgotten by its producer"):
     a. The operation must be "expire" or "forget".
     b. The removal event id is "system.forget:<target>". If the target is already removed by this same id, return it unchanged (a repeated request changes nothing). If another removal (an expiry) already removed it, raise ValueError naming that removal.
     c. Load the target; refuse an owner-authored target (event type beginning "system."), since owner events are provenance.
     d. Build the payload: details plus operation, target_event_id and target_actor_agent_id, as sorted-key JSON in UTF-8.
     e. Build a complete version 2 envelope: actor owner-system, event_type system.forget, created_at the request time, source_artifact_id copied from the target, causation_event_id the target, source_revision "owner-forget-of-<target>", payload_ref inline with the payload's SHA-256, outcome "forgotten", exactness "derived", and four pending receipts with processor "unknown" and versions "0.0.0".
     f. Capture it through M_1_B_2 as the owner's own code, with the target's memory_removals row written in the same transaction, so the target is withheld from every search and listing (M_1_C_3) the moment the removal is durable.
  5. Reply with the removal event's receipts as they stand (pending at this moment), removed_by and complete; then start processing the removal event on every registered tier in the background.
  6. complete becomes true, as read through status (M_1_D_1), only when all four receipts of the removal event are processed or processed_no_new_fact.
  7. Nothing is deleted: the target's event row and its original bytes stay stored, and ground truth keeps its pointer to the event (agent_memory:INV-006).
tools:
- duckdb
- hashlib
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_1_C_1
- target_event_id: string, CORE — the event forgotten
- removal_event_id: string, CORE — system.forget:<target>
- requested_by: string, CORE — the principal who asked
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Unknown target -> Validation Error "Unknown event"; nothing recorded
- Caller is not the producer -> Visibility Refused; nothing recorded (only the producer may forget until a permission owner supplies broader grants, which is not built)
- Target already expired -> Validation Error naming the expiry event
- Target is an owner-authored event -> Validation Error; provenance cannot be removed
- A tier with no registered processor -> that receipt of the removal event stays pending and complete never becomes true
- Whether every index and cache inside an engine is cleared is decided by that tier's apply (M_3_B_1, M_4_A_2, M_5_B_1, M_6_A_2), not here
- The load operation still returns a forgotten event's original text, and the model operation can still be handed a forgotten event's id (gaps against agent_memory:INV-006, stated in M_1_D_1 and M_2_B_1)
tests:
- type: unit
  description: A producer forgets its event
  input: forget of an event captured by the same principal, all four tiers registered
  expected: reply with event_id "system.forget:<target>" and complete false; after processing, status of that id has complete true
  tolerance: exact
- type: unit
  description: Only the producer may forget
  input: forget of another principal's event
  expected: Visibility Refused; no memory_removals row
  tolerance: exact
- type: unit
  description: Forget is idempotent
  input: the same forget sent twice
  expected: the same removal event id both times and one memory_removals row
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 forgetting withholds the event but never deletes the original
  input: a forgotten event, then a retrieval whose question matches it and a read of memory_payloads
  expected: the event is absent from every tier's answer and its memory_payloads row is still present with the same hash
  tolerance: exact
logging_tier: CORE

##### M_1_C_2 — Expiry Enforcement
job: Turn every legacy event whose time to live has elapsed into a durable owner-authored expiry removal.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- stored_events: database rows — version 1 events carrying a positive ttl_seconds
- now: timestamp — the current UTC instant
outputs:
- expiry_results: mapping — expiry event id to tier to the receipt status each tier ended with
artifacts:
- removal_payload.json
data_contracts:
- removal_payload.json
seam_contracts:
- owed_work_trigger — each expiry event is processed at once on every registered tier (M_2_A_2 process_event)
dependencies:
- M_1_C_1
invariants:
- UMST:INV-001
- agent_memory:INV-006
- agent_memory:INV-011
tunable_parameters: []
intelligence_pattern: time_based_justified
logic: |
  1. List expired events: version 1 events with a non-null ttl_seconds above zero that are not removed by a forget (an event already removed by an expiry is listed again, so a half-finished expiry is resumed), in capture order. For each, expires_at is the event's own timestamp (read as UTC when it carries no zone) plus ttl_seconds; keep those whose expires_at is at or before now.
  2. On a connection of its own, for each expired event capture the owner's expiry through the same removal capture as M_1_C_1, with operation "expire", requested_by owner-system, requested_at the expires_at instant, details ttl_seconds and reason "ttl_seconds elapsed"; the event id is "system.expire:<target>", source_revision "owner-expiry-of-<target>" and outcome "expired". Asking again returns the same id and stores nothing new.
  3. Process that expiry event on every registered tier now (M_2_A_2) and collect each tier's status.
  4. Return expiry event id to tier to status.
  5. Callers: the maintenance loop (M_0_A_3) runs this every service.maintenance_interval_seconds, before syncing numbers and retrying owed work; every trusted rebuild (M_2_A_3) runs it first. A failed round is logged by that loop and the next round runs. Expiry is time-based by its nature: the ttl is the producer's own declared lifetime, not a tuning constant.
  6. Withholding does not wait for this step: M_1_C_3 recomputes the expired list on every call, so an expired event leaves every answer the moment its time passes.
tools:
- duckdb
- datetime
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_1_C_2
- expired_count: integer, CORE — events expired in this round
- expiry_event_ids: list of string, CORE — the expiry events captured or resumed
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- An event already forgotten -> not listed; its forget stands
- A tier fails the expiry -> that receipt is recorded as failed and the expiry event stays owed; the next round resumes it
- Version 2 events carry no expiry field, so they never expire; a retention policy beyond the legacy ttl is not built
- ttl_seconds of 0 never expires
- The expiry instant comes from the producer's own timestamp, so a backdated timestamp expires at once
- Each expiry event is processed inline and awaited, so a slow tier's prepare lengthens the maintenance round
tests:
- type: unit
  description: An elapsed ttl is expired through the tiers
  input: a version 1 event with ttl_seconds 1 and a timestamp two seconds old, all four tiers registered
  expected: "system.expire:<id>" captured with outcome "expired", and every tier's receipt for it processed
  tolerance: exact
- type: unit
  description: Expiry is repeatable
  input: two expiry rounds in a row over the same elapsed event
  expected: one expiry event and one memory_removals row
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 expiry withholds but never deletes the original
  input: an expired event, then a read of memory_events and memory_payloads
  expected: both rows still present; the event appears in no answer
  tolerance: exact
logging_tier: CORE

##### M_1_C_3 — Live Producers
job: Say which of a list of event ids are still live, with each one's producer, so no removed or expired event reaches an answer.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- event_ids: list of string — events named by hits, cases or a request
outputs:
- live_map: mapping — event_id to producer, for every listed event that is known, not removed and not expired
- removal_id: string or null — from removal_of, the removal event that removed one event
artifacts: []
data_contracts: []
seam_contracts:
- live_check — read by retrieval's withholding filter (M_8_A_2), the review screen (M_9_B_1), known answers (M_9_A_2), ground-truth table access (M_9_A_3) and the dispatcher's removal check (M_2_A_2)
dependencies:
- M_1_C_2
invariants:
- UMST:INV-001
- agent_memory:INV-006
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. An empty list returns an empty map.
  2. In one read, take the listed events (duplicates collapsed) that have no memory_removals row, with each one's producer.
  3. Recompute the expired list (M_1_C_2 step 1) and drop every expired id, even when its expiry event has not been captured yet.
  4. Return the map; unknown, removed and expired ids are simply absent.
  5. removal_of returns the removal event id recorded for one event, or null. Both read only.
tools:
- duckdb
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_1_C_3
- asked: integer, DEBUG — ids asked about
- live: integer, DEBUG — ids returned live
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- An id that was never captured -> absent from the map, never an error
- The expired list is recomputed in full on every call, a scan of every legacy event with a ttl per search; its cost grows with that number (stated by reading the code, not measured)
tests:
- type: unit
  description: Removed and unknown ids are left out
  input: ids of one live event, one forgotten event and one never captured
  expected: a map holding only the live event with its producer
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 an expired event is withheld before its expiry is captured
  input: a version 1 event whose ttl elapsed one second ago, no expiry round run yet
  expected: absent from the live map
  tolerance: exact
logging_tier: CORE

#### M_1_D — Read Back
purpose: Return a stored event with its verified original bytes and its receipts, and report whether every tier finished it, to a caller allowed to see it.
inputs:
  - event_id: string — the event asked about
  - principal: AuthenticatedPrincipal — from M_1_A_2
outputs:
  - loaded_event: JSON object — found, the envelope with its receipts, and the original text
  - receipt_status: JSON object — receipts, removed_by and complete
dependencies:
  - M_1_A_2
  - M_0_D_2
tests:
  - load of an unknown event -> found false
  - status of an event another principal produced, under own-only visibility -> Visibility Refused
  - A stored payload whose bytes no longer match its hash -> refused, never repaired

##### M_1_D_1 — Event Loader and Receipt Status
job: Read one stored event back with its verified original bytes plus its receipts, for a caller allowed to see it.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- event_id: string — the event asked about
- principal: AuthenticatedPrincipal — the caller named by M_1_A_2
outputs:
- loaded_event: JSON object — found false, or found true with the envelope (receipts as they stand) and payload_text
- receipt_status: JSON object — event_id, removed_by, receipts and complete
- verified_event: envelope and bytes — for internal callers (dispatcher, forget, rebuild, model steps)
artifacts:
- loaded_event.json
- receipt_status.json
data_contracts:
- loaded_event.json
- receipt_status.json
seam_contracts:
- VisibilityPolicy — the policy object built from visibility.pool_scope_labels resolves the principal's visibility; it is defined in the processor seam (M_2_A_1) and its policies in M_8_A_1
dependencies:
- M_1_A_2
- M_0_D_2
invariants:
- UMST:INV-001
- agent_memory:INV-002
- agent_memory:INV-006
- agent_memory:INV-010
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. load_event: one read joining memory_events with memory_payloads for the id; no row raises ValueError "Unknown event".
  2. Verify the stored event without repairing it: decode the envelope. With no stored bytes, a version 2 event raises ValueError saying a retry of the capture must supply them, and a version 1 event returns no bytes. With bytes, their SHA-256 must equal the stored payload hash and, for version 2, that hash must equal the envelope's payload_sha256; otherwise raise ValueError "fails its hash check".
  3. receipts returns tier to receipt (processor_id, processor_version, projection_version, status, attempt_id, attempted_at, completed_at, error_ref) for one event, empty for an unknown one.
  4. Socket "load": event_id must be non-empty text. Load and verify the event; an unknown event answers found false. Resolve the principal's visibility and check the event's producer; refused raises PermissionError. Decode the bytes as UTF-8 (not text raises ValueError). Reply found true, the envelope with its current receipts, and payload_text.
  5. Socket "status": event_id must be non-empty text; an unknown event is a Validation Error. For an owner-authored event (type beginning "system."), visibility is judged on the event it removed (its causation_event_id). Refused raises PermissionError. Reply event_id, removed_by, receipts and complete, which is true only when receipts exist and every one is processed or processed_no_new_fact.
tools:
- duckdb
- hashlib
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_1_D_1
- event_id: string, CORE — the event read
- found: boolean, CORE — whether the event exists
- complete: boolean, CORE — whether every tier finished it
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Stored bytes fail their hash check -> ValueError; nothing is repaired or rewritten
- Version 2 event stored without bytes -> ValueError telling the producer to retry the capture
- Event not visible to the caller -> Visibility Refused
- Payload is not UTF-8 text -> Validation Error from load
- load does not check removal: a forgotten or expired event's original text is still returned to a caller allowed to see its producer (gap against agent_memory:INV-006, by reading the code)
- load verifies the hash before it checks visibility, so a hash failure answers before a visibility refusal (by reading the code)
- status never reports a version 1 event complete, because three of its four receipts are never owed (M_1_B_2)
- status answers an unknown event with a Validation Error while load answers found false
tests:
- type: unit
  description: An event loads with its original text
  input: load of a captured version 2 event by its producer
  expected: found true, the envelope with four receipts, payload_text equal to the captured bytes decoded as UTF-8
  tolerance: exact
- type: unit
  description: A tampered payload is refused
  input: a memory_payloads row whose bytes were changed outside the service, then load
  expected: Validation Error "fails its hash check"
  tolerance: exact
- type: invariant
  description: agent_memory:INV-010 the same visibility guards load and status
  input: load and status of an os-uid-1000 event asked by os-uid-1001 under own-only visibility
  expected: Visibility Refused for both
  tolerance: exact
logging_tier: CORE

### M_2 — Engine Dispatch
description: Sends every owed receipt to its tier's registered engine, prepares outside any write transaction, commits each projection with its own truthful receipt on one connection, resumes attempts a crash interrupted, rebuilds a tier's projection from the stored originals on a trusted request, and runs every language-model step on the model the one language_model setting names.
dependencies:
  - M_0
  - M_1
entry_conditions:
  - M_0 and M_1 are VERIFIED
  - test fixtures are prepared (a throwaway memory database and engines registered on it)
exit_conditions:
  - all submodules VERIFIED
  - all artifacts schema-valid
  - all tests passed
  - end-to-end module test passed
module_test: With all four tiers registered and the embedding door stopped, capture a version 2 chat; the episodic and temporal receipts become processed and the relational receipt retryable_failure naming "numbers not yet supplied"; start the door; after the next maintenance round the relational receipt is processed with exactly one chunk per piece; then run one model step with opinion "second" and confirm the reply names the second-opinion model as both requested and achieved.

#### M_2_A — Processing
purpose: Turn each pending receipt into an honest outcome by running the tier's engine, one projection and its receipt per transaction, and rebuild a tier's projection from the originals when the operator asks.
inputs:
  - owed receipts: database rows — pending, retryable_failure or processing
  - registered engines: one MemoryProcessor per configured tier
outputs:
  - receipt states: processed, processed_no_new_fact, retryable_failure, terminal_failure or unknown, each with processor identity and error_ref
  - rebuild_result: JSON object — request_id, tier, model_labels and status
dependencies:
  - M_1_B_2
  - M_1_D_1
  - M_1_C_3
tests:
  - An engine reports processed -> its projection and receipt commit together
  - An engine reports a failure after writing -> its writes are rolled back and the failure is recorded
  - A receipt left "processing" by a crash -> claimed again at the next start, no duplicate projection
  - A relational rebuild within its limit -> every receipt replayed, status completed, originals unchanged

##### M_2_A_1 — Processor Registry
job: Hold at most one registered engine per memory tier under the processor contract every engine must keep.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- processor: MemoryProcessor — an engine object naming its tier_name
outputs:
- processor_for_tier: MemoryProcessor or null — the engine registered for a tier
- registered_tiers: list of string — tiers with an engine, in the fixed order episodic, temporal, relational, personalization
- owner_reader: function — runs one read on the owner's handle between transactions
artifacts: []
data_contracts: []
seam_contracts:
- MemoryProcessor — tier_name; prepare(envelope, verified bytes), asynchronous, run outside any write transaction and the only place model work may happen; apply(native handle, prepared work), run inside the owner's transaction, must write its projection on that handle, must not commit, close or open another connection, must check source revision, permissions and idempotency itself, and returns ProcessorResult(status, processor_id, processor_version, projection_version, error_ref); owner-authored system.forget and system.expire envelopes arrive through the same seam and the engine removes its projection in apply
- optional hooks — attach_reader(reader), attach_numbers(services), initialize_storage(handle), and for rebuild reset_projection(handle, max_items) with model_labels()
- TerminalProcessingError — raised by an engine when retrying the same event can never succeed
- MemoryRetriever, TierRankings, Visibility, RetrievalScope, VisibilityPolicy, AuthenticatedPrincipal, ConnectionAuthenticator — the read-side and identity types defined in the same seam file and used by M_1_A_2, M_1_D_1 and M_8
dependencies:
- M_0_D_1
invariants:
- UMST:INV-001
- agent_memory:INV-001
- agent_memory:INV-003
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. register: the engine's tier_name must be one of episodic, temporal, relational or personalization, otherwise raise ValueError "Unknown tier"; a tier that already has an engine raises ValueError.
  2. get returns the tier's engine, or null when none is registered.
  3. tiers returns the registered tiers in the fixed order.
  4. make_reader builds the reader handed to engines: it runs one function with the owner's native handle as a single job on the owner's worker, between transactions, so the function sees committed state only and must not write, commit or close.
  5. The registry stores and returns engines only; it never calls one. The service builder (M_0_B_3) registers one engine per configured tier, each serving as both processor and retriever.
tools:
- duckdb
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_2_A_1
- registered_tiers: list of string, CORE — tiers with an engine
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- An engine naming an unknown tier -> ValueError at build; the service does not start
- Two engines for one tier -> ValueError at build
- A tier missing from the settings has no engine, so its receipts stay pending and are never invented as done
tests:
- type: unit
  description: One engine per tier
  input: two engines both naming "episodic"
  expected: the second registration raises ValueError
  tolerance: exact
- type: invariant
  description: agent_memory:INV-003 a tier without an engine never reports processed
  input: a capture with only the episodic engine registered, then status
  expected: episodic processed, the three other receipts still pending, complete false
  tolerance: exact
logging_tier: CORE

##### M_2_A_2 — Owed Work Processor
job: Drive every owed receipt through its tier's engine so that a receipt says processed only when that tier's projection committed with it.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- owed_pairs: list of (event_id, tier) — receipts in pending, retryable_failure or processing, for registered tiers
- verified_event: envelope and bytes — from M_1_D_1
outputs:
- outcomes: mapping — (event_id, tier) to the receipt status each attempt ended with
- receipt_rows: database rows — status, attempt_id, attempted_at, completed_at, processor identity and error_ref per tier
artifacts:
- receipt_status.json
data_contracts:
- receipt_status.json
seam_contracts:
- owed_work_trigger — served here: capture and forget start process_event for one event, start runs process_pending once, the maintenance loop (M_0_A_3) runs process_owed every service.maintenance_interval_seconds, and expiry and rebuild call it directly
- MemoryProcessor — prepare and apply as defined in M_2_A_1
dependencies:
- M_2_A_1
- M_1_B_2
- M_1_D_1
- M_1_C_3
invariants:
- UMST:INV-001
- agent_memory:INV-002
- agent_memory:INV-003
- agent_memory:INV-006
- agent_memory:INV-012
tunable_parameters: []
intelligence_pattern: event_driven
logic: |
  1. List owed work: receipts in pending, retryable_failure or processing (an attempt a crash left unfinished) for the registered tiers; a version 2 event is owed to every tier and a version 1 event only to its own tier; order by capture, then tier. Tiers without an engine are not listed and stay pending.
  2. For each pair, take an in-process lock for (event, tier), so two attempts on one receipt never overlap.
  3. Claim in one short transaction: if the receipt is no longer owed, return its current status (finished work is never redone, so replaying a completed attempt makes no duplicate projection); otherwise set it to processing with a new attempt id and attempted_at now, clear completed_at and error_ref, and commit.
  4. If the event has a removal, record terminal_failure naming the removal event.
  5. Load the event with its verified bytes (M_1_D_1) and run the engine's prepare outside any transaction; any model call happens here. NumbersNotYetSupplied becomes retryable_failure and is logged in one line naming the wait; TerminalProcessingError becomes terminal_failure; any other error becomes retryable_failure and is logged with its traceback.
  6. On the owner's worker, in one transaction on the lifetime connection: check again for a removal (removed while preparing: terminal_failure, nothing written); run apply on the same handle; if the transaction id changed, the engine committed on its own, which is recorded as terminal_failure because its writes cannot be undone; a reported status outside processed, processed_no_new_fact, retryable_failure, terminal_failure and unknown is rolled back and recorded as retryable_failure; a reported status other than the two success states rolls the projection back and begins a fresh transaction, with error_ref defaulting to "processor reported <status>; its projection was rolled back".
  7. Write the receipt (status, processor id, processor version, projection version, completed_at, error_ref) only where the attempt id matches and the status is still processing; read it back; if it is not this status with this attempt id, roll back with "Receipt attempt is no longer current; result discarded"; otherwise commit and return the status. The projection and its receipt therefore commit or roll back together, per tier; there is no transaction across tiers, and a tier left unfinished stays owed.
  8. A failure is written in its own transaction, again only where the attempt id still matches and the receipt is still processing, with error_ref "<error type>: <message>".
  9. After a whole pass, finish any rebuild whose tier has nothing owed (M_2_A_3).
  10. Crash recovery: a receipt left processing is claimed again at the next start or maintenance round with a new attempt id; each engine's apply must be idempotent (the Letta, Graphiti and Cognee tiers derive their record ids from the event). Replay is keyed by event and tier; the processor version is recorded on the receipt, and a newer engine version does not reopen a finished receipt (only a rebuild does).
tools:
- asyncio
- duckdb
- uuid
- logging
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_2_A_2
- event_id: string, CORE — the event attempted
- tier: string, CORE — the tier attempted
- attempt_id: string, CORE — the attempt's identity
- status: string, CORE — the receipt state recorded
- error_ref: string, CORE — the failure named, when one occurred
- prepare_seconds: float, DEBUG — time spent in the engine's prepare
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Numbers not yet supplied -> retryable_failure naming the wait; retried every maintenance round until the numbers arrive
- Engine raises TerminalProcessingError, or the event was removed -> terminal_failure; never attempted again
- Engine commits the owner's transaction itself -> terminal_failure; what it committed cannot be undone
- Engine reports a failure state -> its projection rolled back, the state recorded
- Attempt superseded while applying -> result discarded, projection rolled back
- No attempt limit, back-off or schedule of its own exists: a retryable failure is retried every maintenance round for ever (the plan's bounded retry is not built, and no number for it is declared)
- A receipt recorded as "unknown" is never owed again and never complete, so nothing retries it
- A failure write that finds the attempt superseded writes nothing but still returns the status it meant to write (by reading the code)
- The per-receipt locks are kept in memory for the life of the process (by reading the code)
- Pairs run one at a time, so a long prepare (a Mem0 extraction of minutes) delays every later pair and the maintenance round
- Model work runs in prepare, outside any write transaction, but the model that answered a prepare-time call is not recorded on the receipt (agent_memory:INV-012 met for the transaction rule, not for the record)
tests:
- type: unit
  description: Waiting on numbers is an honest retryable failure
  input: a version 2 event captured with the relational tier registered and no supplier connected
  expected: relational receipt retryable_failure with error_ref beginning "NumbersNotYetSupplied" and naming "numbers not yet supplied"
  tolerance: exact
- type: unit
  description: An interrupted attempt is resumed without a duplicate
  input: a receipt left "processing" with a projection that was never committed, then a restart
  expected: the receipt is claimed with a new attempt id, ends processed, and exactly one projection row exists
  tolerance: exact
- type: unit
  description: An engine that commits on its own is caught
  input: an engine whose apply calls commit on the handle
  expected: receipt terminal_failure naming that the engine ended the owner's transaction
  tolerance: exact
- type: invariant
  description: agent_memory:INV-003 a projection and its receipt commit together or not at all
  input: an engine whose apply writes one row and then reports retryable_failure
  expected: the row is absent and the receipt is retryable_failure with error_ref saying the projection was rolled back
  tolerance: exact
- type: invariant
  description: agent_memory:INV-012 model work runs outside the write transaction
  input: an engine whose prepare waits on a slow model call while another capture arrives
  expected: the second capture commits and replies before the first prepare returns
  tolerance: exact
logging_tier: CORE

##### M_2_A_3 — Projection Rebuild
job: Rebuild one tier's projection from the stored originals through the ordinary receipts, as one bounded trusted request.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- tier: string — relational or personalization, from --rebuild-tier
- request_id: string — from --rebuild-id; the same id resumes rather than re-clears
- max_items: integer — from --rebuild-limit, positive
outputs:
- rebuild_result: JSON object — request_id, tier, model_labels and status (processing, completed, failed or superseded)
- rebuild_rows: database rows — one memory_rebuilds row and the archived receipts in memory_rebuild_receipts
artifacts:
- rebuild_result.json
data_contracts:
- rebuild_result.json
seam_contracts:
- reset_projection — the engine empties its own source-backed projection on the owner's handle inside the rebuild transaction, never committing and never calling a model; model_labels names the models whose numbers its projection holds
dependencies:
- M_2_A_2
- M_1_C_2
invariants:
- UMST:INV-001
- agent_memory:INV-003
- agent_memory:INV-004
- agent_memory:INV-006
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Only the trusted command line runs it (M_0_A_2: --rebuild-tier relational or personalization, --rebuild-id, a positive --rebuild-limit), never a socket client. The service is started with process_pending false so no ordinary processing begins first, and the running service must be stopped beforehand because the settings loader refuses a locked database.
  2. Capture every elapsed expiry first (M_1_C_2).
  3. The tier's engine must offer reset_projection and model_labels, otherwise raise ValueError "has no supported embedding projection rebuild" (the episodic and temporal engines offer neither). The request id must be non-empty text and max_items a positive integer.
  4. In one owner transaction: if the request id already exists for the same tier and labels, commit and return its status, so a retry resumes without clearing completed work; with other labels raise ValueError. Read up to max_items plus one source events the tier is owed (version 1 only for its own tier); more than max_items raises ValueError and nothing changes. Verify every source's stored original (M_1_D_1); a failed hash refuses the rebuild. Insert the memory_rebuilds row (processing, labels, now), copy the tier's current receipts into memory_rebuild_receipts, and call the engine's reset_projection on the same handle; if the transaction id changed, raise RuntimeError because committed changes cannot be undone. Set the tier's receipts back to pending, or to terminal_failure "Source was removed; excluded from rebuild" for removed sources; mark older processing or failed rebuilds of the tier superseded; commit.
  5. When the state is processing, replay up to max_items owed pairs of the tier through M_2_A_2, then finish rebuilds.
  6. Finishing: a processing rebuild whose engine's labels changed becomes failed; one whose tier still has an owed live receipt stays processing; otherwise it becomes completed when every live receipt succeeded, failed when any did not.
  7. Return the result; the command prints it and exits 0 for completed and 1 otherwise, or prints "Rebuild failed" with the reason and exits 1 on a ValueError or RuntimeError. While a rebuild of a tier is processing, retrieval answers that tier as failed (M_8_A_2).
  8. Originals and removals never change; numbers come only from the supplier through the ordinary prepare (agent_memory:INV-004).
  9. Per-model rebuild (ledger row 44, requirement N3: throw one model's numbers away and rebuild them from the originals without touching any other model's numbers) is specified here and not built: no command or operation exists for it. What exists is this per-tier rebuild and the per-model stale marking and recompute requests of M_7_A_2 and M_7_A_3.
tools:
- duckdb
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_2_A_3
- request_id: string, CORE — the rebuild request
- tier: string, CORE — the tier rebuilt
- model_labels: list of string, CORE — the labels the engine reported
- source_events: integer, CORE — events replayed
- status: string, CORE — the rebuild's final state
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Tier episodic or temporal -> ValueError "has no supported embedding projection rebuild" (the command line offers only relational and personalization)
- More source events than max_items -> ValueError; nothing changed (the limit is all or nothing, not a batch size)
- Request id reused with other model labels -> ValueError
- A source's stored bytes fail their hash -> the whole rebuild refused, nothing changed
- Engine ends the rebuild transaction -> RuntimeError; what it committed cannot be undone
- Engine labels change before the replay finishes -> rebuild marked failed
- The relational engine reports no model labels and the personalization engine one label, the model whose numbers Mem0's own table holds
- memory_rebuild_receipts is written but nothing reads it
- Per-model rebuild (ledger row 44) is not built: asking for it has no command; the per-tier rebuild is the only rebuild
tests:
- type: unit
  description: A relational rebuild completes from the originals
  input: three captured events in the relational tier, rebuild with limit 3 and a new request id, supplier connected
  expected: status completed, one chunk per piece afterwards, every memory_payloads row unchanged
  tolerance: exact
- type: unit
  description: An over-limit rebuild changes nothing
  input: four relational source events, rebuild with limit 3
  expected: ValueError; projection, receipts and memory_rebuilds unchanged
  tolerance: exact
- type: unit
  description: A tier without a rebuild hook is refused
  input: rebuild of the episodic tier
  expected: ValueError "has no supported embedding projection rebuild"
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 a removed source stays removed through a rebuild
  input: a relational rebuild over a tier holding one forgotten event
  expected: that event's receipt terminal_failure "Source was removed; excluded from rebuild", no chunk for it, its original still stored
  tolerance: exact
logging_tier: CORE

#### M_2_B — Model Steps
purpose: Run every language-model step of the memory parts on the one model the language_model setting names, or on its second opinion when asked, and report which model and which device really answered.
inputs:
  - model_request: JSON object — part, step, args and opinion
  - language_model: settings — provider, model, second_opinion_model, base_url, api_key, request_timeout_seconds, options
outputs:
  - model_step_reply: JSON object — the step's output with requested_model, achieved_model, placement and seconds
  - model_parts_report: JSON object — requested models, main model placement and each part's steps and latest achieved model
dependencies:
  - M_0_B_1
  - M_1_D_1
tests:
  - A step run with opinion "second" -> requested and achieved both name the second-opinion model
  - opinion "second" with no second_opinion_model set -> refused naming the setting, no fallback to the main model
  - A step naming another principal's event -> Visibility Refused before any model call

##### M_2_B_1 — Model Step Router
job: Run one named language-model step of one memory part on the model the language_model setting names, reporting the model that really answered.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- part: string — graphiti, cognee, letta, zep or mem0, as configured
- step: string — one of the part's steps
- args: JSON object — the step's arguments
- opinion: string — "main" (the default) or "second"
- principal: AuthenticatedPrincipal — the caller named by M_1_A_2
outputs:
- model_step_reply: JSON object — the step's real output plus part, step, opinion, requested_model, achieved_model, placement and seconds
- model_parts_report: JSON object — requested main and second, main_model_placement, and per part its steps and achieved_model
artifacts:
- model_step_reply.json
- model_parts_report.json
data_contracts:
- model_step_reply.json
- model_parts_report.json
seam_contracts:
- ModelPart — name, steps(), achieved_model() and run_step(step, args, model), implemented by the parts of M_3_B, M_4_B, M_4_D, M_5_B and M_6_B
- DatabaseAccess — a part reads on the owner's handle and writes in one committed transaction on a connection of its own, never holding either across a model call
dependencies:
- M_0_B_1
- M_1_D_1
- M_1_A_2
invariants:
- UMST:INV-001
- agent_memory:INV-005
- agent_memory:INV-010
- agent_memory:INV-012
tunable_parameters:
  - param_name: language_model.provider
    default_value: "ollama"
    param_type: string
    description: The model server kind; the settings loader refuses anything but ollama. A stored setting with no code default.
  - param_name: language_model.model
    default_value: "ornith_35b_a3b_cpu"
    param_type: string
    description: The main model every part asks for, and the model the personalization tier's capture-time extraction uses; changing it changes every part together. A stored setting with no code default.
  - param_name: language_model.second_opinion_model
    default_value: "qwen38_4b_distill_cpu"
    param_type: string
    description: The model a step runs on when opinion "second" is asked; optional, and when absent a second-opinion request is refused naming this setting. A stored setting with no code default.
  - param_name: language_model.base_url
    default_value: "http://127.0.0.1:11434"
    param_type: string
    description: The model server's address; parts call its OpenAI-style door at this address plus "/v1" and read placement from "/api/ps". A stored setting with no code default.
  - param_name: language_model.request_timeout_seconds
    default_value: "3000"
    param_type: float
    description: The time limit of one request to the model server, also used for the placement probe. A stored setting with no code default.
  - param_name: language_model.options.reasoning_effort
    default_value: "none"
    param_type: string
    description: Filled into every chat request that names none; a stored setting with no code default.
  - param_name: language_model.options.temperature
    default_value: "0"
    param_type: float
    description: Handed to each part, which applies it in its own model configuration; the router itself does not set it. A stored setting with no code default.
intelligence_pattern: none
logic: |
  1. At start, the builder (M_0_B_3) makes the router from the single validated language_model setting: provider must be ollama, and model, base_url, api_key, request_timeout_seconds and options are required; second_opinion_model is optional. One part is made per configured engine: graphiti (temporal tier with graphiti settings), cognee (cognee settings), letta (letta settings with the episodic organization), zep (zep settings) and mem0 (the personalization tier). Two parts of one name are refused. No model name is written in code (agent_memory:INV-005).
  2. Each part talks to the server through an OpenAI-style client at base_url plus "/v1", with the request time limit from the setting and automatic retries switched off. A response hook keeps the "model" field of every JSON answer as the part's achieved model (the mem0 part wraps its client's create and keeps the reply's model). reasoning_effort and top_p from options are filled in where the caller gave none, and max_tokens, when set, is a ceiling a caller can lower but not raise.
  3. Socket "model": with no router configured, raise ValueError "no language model is configured on this service". part and step must be text and args an object; opinion must be "main" or "second"; otherwise raise ValueError.
  4. Before any model call, check visibility: every id in args.event_ids must be a stored event whose producer the principal may see (unknown raises ValueError, refused raises PermissionError); for the graphiti and zep parts, group_id and graph_group_id must be one of the principal's groups ("actor-" plus the SHA-256 of each visible actor, or any group under pool visibility); agent_id and foreground_agent_id, and user_id for the mem0 part, must be a visible actor.
  5. Resolve the model: an unknown part or step raises ValueError naming the known ones; "main" takes language_model.model; "second" takes language_model.second_opinion_model, and when that is absent raises ValueError naming the setting, never falling back to the main model.
  6. Run the step and time it. The step reads on the owner's handle and writes in one committed transaction on a connection of its own, so no write transaction is open while the model answers (agent_memory:INV-012); a failed step stores nothing.
  7. Reply with the step's own output plus part, step, opinion, requested_model, achieved_model (the model the server said answered this part's latest call), placement (from the server's loaded-model list for the requested name: processor only or graphics card with sizes, or loaded false with the reason when it cannot be read) and seconds rounded to two places.
  8. A step error other than ValueError or PermissionError becomes RuntimeError naming its type and message, answered as Internal Error. The router never retries.
  9. Socket "model_parts": with no router, raise ValueError; otherwise reply the requested main and second models, where the server holds the main model, and per part its steps and latest achieved model. It calls no model.
tools:
- openai
- httpx
- urllib
- asyncio
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_2_B_1
- part: string, CORE — the part run
- step: string, CORE — the step run
- opinion: string, CORE — main or second
- requested_model: string, CORE — the model the settings named
- achieved_model: string, CORE — the model the server reported answering
- placement: string, CORE — processor only, graphics card, or not loaded with the reason
- seconds: float, CORE — time the step took
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- No language_model setting -> model and model_parts answer Validation Error "no language model is configured on this service"
- Second opinion asked with no second_opinion_model -> Validation Error naming the setting; no fallback
- Unknown part, step or opinion -> Validation Error naming the known ones
- An event, group or actor the principal may not see -> Visibility Refused before any model call
- Model server unreachable, timed out or answering malformed output -> Internal Error naming the cause; nothing stored, nothing retried
- Placement cannot be read -> placement says loaded false with the reason; the step's answer still returns
- The achieved model is returned in the reply and kept in memory per part; it is not written to the database, so it is lost at restart, and capture-time model work inside a tier's prepare records none (agent_memory:INV-012 met for the transaction rule, not durably recorded)
- The recorder keeps the latest answer per part, not per request, so two concurrent steps of one part can report each other's model, and a step that makes no model call (for example zep relevant_facts) reports an earlier call's model (by reading the code, not measured)
- placement is read for the requested name after the call, not for the achieved one
- The router reports placement but does not stop a model from using the graphics card; the server's own model settings decide (the configured models are processor-only copies)
- The visibility checks do not cover cognee's text steps or zep's user_id, project_uuid and session_id, and do not refuse a forgotten or expired event id (gaps against agent_memory:INV-010 and agent_memory:INV-006)
tests:
- type: unit
  description: The second opinion is refused when not set
  input: op model with opinion "second" on a service whose settings hold no second_opinion_model
  expected: Validation Error naming language_model.second_opinion_model; no request reaches the model server
  tolerance: exact
- type: unit
  description: An invisible event is refused before the model runs
  input: op model, part graphiti, step ingest, event_ids naming another principal's event under own-only visibility
  expected: Visibility Refused and no model call
  tolerance: exact
- type: invariant
  description: agent_memory:INV-005 changing the one setting changes every part
  input: language_model.model set to a second installed model, the service restarted, one step run on each configured part
  expected: every reply's requested_model and achieved_model name the new model (the 2026-10-23 handoff records this switch for all five parts)
  tolerance: exact
- type: invariant
  description: agent_memory:INV-012 the achieved model is the one the server reported
  input: a step whose server reply names a model different from the requested name
  expected: achieved_model equals the server's reported name and requested_model the setting's
  tolerance: exact
logging_tier: CORE

### M_3 — Episodic Memory (Letta)
description: Keeps each agent's episodic memory in Letta's own structures inside the one memory database, under the reserved schema letta: pinned memory blocks and the tools that edit them, every captured message as recall, tagged archival passages, a git-backed copy of the blocks, and the language-model steps that compact a conversation, consolidate memory in the background and dream. The code is a port of Letta V1 (archive branch, commit 56ba9c25, Apache-2.0) with dreaming translated from Letta Code. Wired into the service today: the episodic tier (every captured event becomes recall messages, ranked by every registered model's numbers when they exist, else by substring) and the letta model part (seed_agent, compact, setup_sleeptime, consolidate, dream). Built but not wired: archival and source passages, conversation_search as a tool, and the git memory repository. Waiting on numbers: archival search by meaning, because a passage holds one embedding column and cannot keep every model's numbers side by side.
dependencies:
  - M_0
  - M_1
  - M_2
  - M_7
entry_conditions:
  - M_0, M_1, M_2 and M_7 are VERIFIED
  - the memory database holds tiers.episodic.organization_id and the letta settings sections compaction, sleeptime and dream
  - test fixtures are prepared on a throwaway copy of the memory database
exit_conditions:
  - all submodules VERIFIED
  - all artifacts schema-valid
  - all tests passed
  - end-to-end module test passed
module_test: On a throwaway database, capture a plain note and a version 2 message event and confirm both episodic receipts read processed by letta-duckdb-recall; retrieve a substring of the note with no numbers and confirm the hit is labelled "case-insensitive substring match, newest first; not semantic relevance"; with numbers supplied for a registered model, retrieve a question that shares no words with the note and confirm the note comes back ranked by that model's cosine distance; forget the note, restart the service and confirm it no longer comes back; then through the model operation run seed_agent, compact in mode all, and dream with force, and confirm a summary message is stored, no message is deleted, and the dream reports which blocks it changed.

#### M_3_A — Letta Store on DuckDB
purpose: Hold Letta's blocks, recall messages, archival and source passages in the reserved letta schema of the memory database, on the owner's borrowed handle or on a file the store opens itself, and hold a git-backed copy of an agent's blocks in a local folder.
inputs:
  - organization_id: string — the one Letta organization every row is written and read under
  - a native DuckDB handle — borrowed from the owner, or opened by the store on a DuckDB file (never SQLite)
  - blocks, messages, passages and editing-tool calls from the tier and the model steps
outputs:
  - rows in the letta schema tables
  - search answers (conversation_search, archival_memory_search)
  - commits in an agent's git memory repository
dependencies:
  - M_0_D_1
tests:
  - A block value longer than its limit -> refused, nothing written
  - Eight concurrent appends to one block -> all eight kept, a stale writer refused
  - A conversation_search with an end date of yesterday -> nothing returned
  - An archival search with tags "all" of two tags -> only passages holding both
  - A block deleted from the git repository -> its earlier text still readable at the earlier commit

##### M_3_A_1 — Memory Blocks and Editing Tools
job: Keep each agent's pinned memory blocks in the letta schema, changing them only through Letta's editing tools under a version check.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- organization_id: string — the Letta organization the block belongs to
- block: Letta Block — label, value, limit, read_only, description, metadata, hidden
- agent_id: string — the agent a block is attached to or detached from
- expected_version: integer — the block version the writer read
- editing_tool_call: tool name with its arguments — core_memory_append, core_memory_replace, memory_replace, memory_insert, memory_rethink or memory_apply_patch
- seed_agent_request: JSON object — organization_id, agent_id, blocks (label, value, limit) and messages (role, text), through the model operation, part letta
outputs:
- stored_block: Letta Block — the block as stored, with its new version
- tool_result: string — the edited block's new text, or the report of a multi-block patch
- seed_agent_answer: JSON object — conversation_id and the stored message_ids
artifacts:
- letta_block.json
- letta_blocks_agents.json
data_contracts:
- letta_block.json
- letta_blocks_agents.json
seam_contracts:
- LettaMemoryStore on a borrowed handle never begins, commits, rolls back or closes it and runs every operation inside the caller's transaction; a store made by open() owns its file and commits each operation itself
- run_memory_tool(organization_id, agent_id, tool, arguments) runs the tool on an in-memory copy of the agent's blocks and writes every change it made in one transaction, or nothing
dependencies:
- M_0_D_1
invariants:
- agent_memory:INV-001
- agent_memory:INV-005
- agent_memory:INV-011
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Schema. ensure_schema compares every existing table of schema letta with the mapped tables. If any differs in a column or a type, raise LettaSchemaError listing the differences and create nothing. If Letta-shaped tables exist in another schema of the same file, refuse with LettaSchemaError naming each with its row count, unless the caller asked to preserve them, in which case leave them untouched and return them. Otherwise create the missing tables (block, blocks_agents, conversations, messages, archival_passages, passage_tags, source_passages).
  2. Every read and write is limited to the caller's organization_id and to rows not marked deleted; a block of another organization raises LettaNotFound.
  3. create_block: a block with no label raises LettaIntegrityError. The block model refuses a value longer than its limit, on construction and on every later assignment. A block given no limit takes Letta's own constant of 100000 characters. The block is stored at version 1.
  4. update_block: only value, label, limit, description, metadata, read_only and hidden may change; any other field raises ValueError. The merged block is validated before anything is written. A label change moves every attachment to the new label in the same transaction, and raises LettaIntegrityError when an agent holding the block already holds another block with that label. The write is a compare-and-set on expected_version that raises the version by one; if another change came first, raise StaleBlockVersion "Block <id> is at version <n>, not <m>" and write nothing.
  5. attach_block refuses with LettaIntegrityError when the agent already holds this block or another block with the same label; detach_block raises LettaNotFound when the block was not attached; delete_block removes the block and every attachment in one transaction. The store enforces these rules itself because DuckDB refuses cascading foreign keys.
  6. run_memory_tool: read the agent's attached blocks, build Letta's core Memory from them and call the tool on it. The tools refuse a read-only block, text that is not found or found more than once, a line-number prefix or Letta's line-number banner in the text, and an insert line outside the block. After the tool returns, in one transaction: a block it removed is detached, a block whose value or label changed is written by compare-and-set on the version read, and a block it added is created and attached. If the tool raises or a block changed meanwhile, nothing is written. The tool's result is returned.
  7. memory_apply_patch: a patch without block headers is a simplified unified diff on one block; every hunk's context and removed lines must match exactly one place, else ValueError, and a patch with no hunk is refused. A patch with "*** Add Block:", "*** Delete Block:", "*** Update Block:" or "*** Move to:" headers is split into operations run in their written order, so a Move after an Update renames after the update (the original ran the rename first and the update then failed on the old label; the port fixes this). Adding or renaming to an existing label is refused.
  8. Service path: the model operation's part letta, step seed_agent (no model call), checks that organization_id is the one the service holds (else PermissionError), that blocks is a list and messages a non-empty list (else ValueError), then in one committed write creates each block, attaches it to agent_id, creates one conversation and stores the messages in it. No socket operation edits a block directly: stored blocks change on the service path only through the sleep-time and dream tools (M_3_B_3).
tools:
- duckdb
- sqlalchemy
- pydantic
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_3_A_1
- organization_id: string, CORE — the Letta organization acted on
- block_id: string, CORE — the block created, changed or attached
- block_version: integer, CORE — the version after the change
- tool_name: string, DEBUG — the editing tool run
- refused_reason: string, CORE — the refusal text when nothing was written
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- A value longer than the block's limit -> refused by the block model, nothing written
- An edit of a read-only block -> ValueError naming the block, nothing written
- A writer holding a stale version -> StaleBlockVersion "at version 12, not 11", nothing written
- A label another attached block already holds -> LettaIntegrityError, nothing written
- A block id of another organization -> LettaNotFound
- A letta table of another shape already present -> LettaSchemaError listing the differences, nothing created
- A step naming an organization other than the service's -> PermissionError "that organization is not the one this service holds"
- A block made without a limit -> takes Letta's constant of 100000 characters, written in code rather than read from a stored setting (a Law 32 gap, stated, not fixed)
- Five of the six editing tools (core_memory_append, core_memory_replace, memory_replace, memory_insert, memory_rethink) carry the inherited one-line docstring "Method docstring." -> the documentation rule is not met in that file (read from the code)
- A caller asking the service to edit a block directly -> no socket operation exists for it; only seed_agent, consolidate and dream reach blocks
tests:
- type: unit
  description: Concurrent appends and a stale writer (ledger row 6, proven 2026-10-20)
  input: eight threads each run core_memory_append on one block through run_memory_tool; then a writer calls update_block with a version read before them
  expected: all eight appends kept; the late writer refused with StaleBlockVersion and the block unchanged
  tolerance: exact
- type: unit
  description: One multi-block patch in one transaction
  input: memory_apply_patch adding block goals, updating human and moving it to person
  expected: blocks goals and person stored, human gone, all in one commit; a patch whose hunk matches nowhere stores nothing
  tolerance: exact
- type: unit
  description: Shared block (ledger row 5)
  input: one block attached to two agents, edited through the first agent's tools
  expected: the second agent reads the edited value; detaching one agent leaves the block
  tolerance: exact
- type: invariant
  description: agent_memory:INV-001 the store never finishes the owner's transaction
  input: run_memory_tool on a store built on a borrowed handle inside a transaction the caller then rolls back
  expected: no block change remains and the handle is still open
  tolerance: exact
logging_tier: CORE

##### M_3_A_2 — Recall and Conversation Search
job: Hold every message of every agent as Letta recall, searchable the way Letta's conversation_search tool searches it.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- messages: list of Letta Message — id, agent_id, role, text or content parts, otid, sender_id, conversation_id
- message_filter: object — conversation_id, after_sequence_id, roles, query_text, otid, created_after, created_before, limit, newest_first
- conversation_search_request: object — agent_id, query, roles, limit, start_date, end_date, conversation_id, timezone_name
outputs:
- stored_messages: list of Letta Message — as stored, each with its sequence_id
- conversation_search_answer: JSON object — message line and results, each with timestamp, time_ago, role and content
artifacts:
- letta_message.json
- letta_conversation.json
data_contracts:
- letta_message.json
- letta_conversation.json
seam_contracts:
- list_messages(organization_id, agent_id, filters) is the one reader of recall for the episodic tier (M_3_B_1) and the model steps (M_3_B_2, M_3_B_3)
dependencies:
- M_0_D_1
- M_3_A_1
invariants:
- agent_memory:INV-001
- agent_memory:INV-006
- agent_memory:INV-011
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. add_messages: every message needs an agent_id, else LettaIntegrityError. A message naming a conversation must name one of the same organization and the same agent, else LettaIntegrityError (the original foreign key). Messages are appended in the order given and returned with their sequence_id.
  2. list_messages returns one agent's current messages in stored order, narrowed by conversation, by sequence after a given one, by roles, by a case-insensitive substring of the text (special characters escaped), by exact otid, and by created at or after and at or before a moment. A limit keeps the earliest matches, or with newest_first the latest, latest first. list_message_agent_ids returns every agent with a current message.
  3. correct_message replaces a message's text and content parts in place; an unknown message raises LettaNotFound. delete_messages removes the named messages of the agent and returns how many. delete_conversation clears conversation_id on its messages and deletes the conversation, in one transaction (the original ON DELETE SET NULL). These remove projection rows only: the captured event stays in the owner's event store.
  4. conversation_search: roles default to user and assistant, and the tool role is always removed; the agent's own conversation_search calls are never returned. start_date and end_date are ISO 8601; a bare date means the start or the end of that day; naive moments take timezone_name, else UTC. A date in any other form raises ValueError naming the accepted forms. The newest matches come first, cut to limit, or to Letta's default page size of 5 when no limit is given. No match answers "No results found." with no results; otherwise "Showing <n> results:" with each result's timestamp, how long ago (s, m, h or d), role and text.
  5. The store matches text only. Search by meaning over recall happens in the episodic tier (M_3_B_1) from the owner's per-model numbers, never in this store.
  6. Service path: no socket operation calls conversation_search; recall is reached through the episodic tier's retrieve and through the Letta model steps.
tools:
- duckdb
- sqlalchemy
- zoneinfo
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_3_A_2
- organization_id: string, CORE — the Letta organization read or written
- agent_id: string, CORE — the agent whose recall was read or written
- messages_written: integer, CORE — messages appended or deleted
- results_returned: integer, DEBUG — conversation_search results returned
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- A message with no agent_id -> LettaIntegrityError, nothing written
- A message naming another agent's conversation -> LettaIntegrityError "Conversation <id> is not a conversation of agent <id>", nothing written
- A search date that is not ISO 8601 -> ValueError naming the accepted forms (YYYY-MM-DD or YYYY-MM-DDTHH:MM)
- A search under another organization -> no results
- A conversation_search with no limit -> Letta's page size of 5, written in code as Letta's constant rather than read from a stored setting (a Law 32 gap, stated)
- A caller wanting conversation_search through the service -> no socket operation exposes it; ledger row 7 proves it only by the script prove_search_tools.py on a throwaway file
tests:
- type: unit
  description: conversation_search as Letta's tool (ledger row 7, run 2026-10-20)
  input: an agent's user, assistant and tool messages mentioning "bergen", plus the agent's own conversation_search call; search "bergen"
  expected: the user and assistant messages, newest first; the tool message and the search call left out
  tolerance: exact
- type: unit
  description: Date filters and a bad date
  input: the same search with end_date yesterday, then with end_date "last week"
  expected: no results for yesterday; ValueError naming the accepted forms for the bad date
  tolerance: exact
- type: unit
  description: Two conversations of one agent, one of another
  input: messages added to two conversations of agent a, then one naming agent b's conversation for agent a
  expected: both conversations kept in order; the third refused with LettaIntegrityError
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 removing recall never touches the original
  input: delete_messages for the messages of one captured event
  expected: the messages are gone from recall and the event row and its payload bytes remain in the owner's event store
  tolerance: exact
logging_tier: CORE

##### M_3_A_3 — Archival Passages
job: Keep tagged archival passages with any embedding the supplier made, searchable by tags with text or by cosine distance.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- archival_insert: object — archive_id, content, tags, and the embedding with its embedding_config only when the supplier made it
- archival_search: object — archive_id, query, query_embedding (optional, from the supplier), tags, tag_match_mode any or all, top_k, start_datetime, end_datetime, timezone_name
- source_passage: object — source_id, file_id, file_name, text, embedding
outputs:
- stored_passage: Letta Passage — as stored, with its tags
- archival_results: list of JSON object — timestamp, content, tags, and distance for a search by meaning
- source_results: list of (Letta Passage, distance)
artifacts:
- letta_archival_passage.json
- letta_passage_tag.json
- letta_source_passage.json
data_contracts:
- letta_archival_passage.json
- letta_passage_tag.json
- letta_source_passage.json
seam_contracts:
- archival_memory_search takes the question's embedding already made by the supplier; the store never makes one
dependencies:
- M_0_D_1
- M_3_A_1
invariants:
- agent_memory:INV-004
- agent_memory:INV-011
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. archival_memory_insert: blank text raises LettaIntegrityError "An archival passage needs text". Tags are kept once each in their first order. The embedding is stored only when given; a passage without one is found by text and tags but not by meaning.
  2. correct_archival_passage replaces the text and the embedding together (None when the new text has none), and the tags when given, so a search never finds the old text's vector under the new text. delete_archival_passage removes the passage and its tag rows in one transaction; delete_archive removes every passage of an archive and their tags and returns the count.
  3. search_archival_passages ranks the archive's current passages by exact cosine distance to the query vector, among stored vectors of the query's own width, nearest first, cut to the limit. Tags "any" keeps passages holding any listed tag; "all" keeps passages holding every one.
  4. archival_memory_search: a tag_match_mode other than any or all raises ValueError; a blank query returns nothing. With a query embedding, search by meaning; when a date filter is given it first reads 20 times the wanted count, because dates are applied after the nearest are found. Without an embedding, the query is a case-insensitive substring of the text, newest first, as Letta does for an agent with no embedding setting. Dates follow the conversation_search rules. The answer is cut to top_k, or to Letta's page size of 5.
  5. Source passages: insert, search by cosine distance within listed sources, and delete by source or by one file.
  6. Not wired: no socket operation and no tier stores or searches archival or source passages; the episodic tier does not fill them.
  7. Not built: archival search by meaning on the service path. A passage holds one embedding column, so it cannot keep every registered model's numbers side by side as the owner's numbers tables do (ledger 2026-10-27). What it must do when built: store each passage's numbers per registered model through the numbers receiver and rank per model, reporting each model left out.
tools:
- duckdb
- sqlalchemy
- zoneinfo
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_3_A_3
- archive_id: string, CORE — the archive acted on
- passages_returned: integer, DEBUG — results returned
- search_kind: string, CORE — meaning or text
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- A passage with blank text -> LettaIntegrityError, nothing stored
- A tag_match_mode other than any or all -> ValueError
- A stored vector of a different width from the question's -> left out of the ranking by the width filter
- A date filter on a search by meaning -> reads 20 times the wanted count first; the 20 is written in code, not a stored setting (a Law 32 gap, stated)
- A caller wanting archival memory through the service -> no socket operation reaches it today; ledger row 8 is run only by the script prove_search_tools.py
- A search by meaning on the service path -> not built; waits on per-model numbers for passages, and today nothing on the service path makes or stores a passage embedding
tests:
- type: unit
  description: Tags any and all (ledger row 8, run 2026-10-20)
  input: three passages with tags food, places and prefs in different combinations; text search with tags any of food and places, then all of places and prefs
  expected: all three for any; only "Ada lives in Bergen" for all
  tolerance: exact
- type: unit
  description: A corrected passage never returns its old text
  input: correct_archival_passage with new text and no embedding, then a text search for a word only in the old text
  expected: no result
  tolerance: exact
- type: invariant
  description: agent_memory:INV-004 the store never makes numbers
  input: archival_memory_insert with no embedding, then archival_memory_search with no query embedding
  expected: the passage stored with a null embedding and found by text; no embedding call made anywhere
  tolerance: exact
logging_tier: DEBUG

##### M_3_A_4 — Git Memory Repository and Shared Repositories
job: Keep an agent's memory blocks as Markdown files in a local git repository whose history can be read at any commit.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- storage_folder: string — the base folder holding repositories; required, never defaulted
- agent_id: string — whose repository
- blocks: list of Letta Block — written as one Markdown file each
- ref: string — a commit SHA, a branch or HEAD to read at
- remote_and_clones: paths — a bare git remote and the agents' working clones, for a shared repository
outputs:
- commit: MemoryCommit — SHA, author, time, message and files changed
- blocks_at_ref: list of Letta Block — the blocks as they were at the ref
- history: list of MemoryCommit — newest first
artifacts:
- memory_block_file.md
data_contracts:
- memory_block_file.md
seam_contracts:
- StorageBackend (upload, download, exists, delete, list, delete_prefix, copy by path); LocalStorageBackend is the one implementation
dependencies:
- M_3_A_1
invariants:
- agent_memory:INV-006
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. GitOperations needs the git program; if it is missing, raise RuntimeError. LocalStorageBackend needs its base folder (the original defaulted to a folder in the home directory) and refuses a path that leaves the folder.
  2. create_repo_async makes an agent's repository with the given blocks as its first commit. Each block is a Markdown file with a YAML frontmatter (description, read_only, metadata) followed by the block's text.
  3. update_block_async, create_block_async and delete_block_async are one commit each, creating the repository first when it does not exist. Unchanged frontmatter is kept byte for byte; a changed one is rewritten.
  4. A commit copies the repository out of storage into a temporary folder, applies the changes, commits, and copies back only the .git files that are new or changed. Commits to one agent queue on an in-process lock per agent (the original used a Redis lock).
  5. get_blocks_async reads every block at a ref (no repository means no blocks); get_history_async returns commits newest first, optionally only those touching one path, up to a limit.
  6. Skills: skills/<name>/SKILL.md is the block skills/<name>; other Markdown files and non-Markdown files are not blocks.
  7. Backups: the repository copied out of storage is the backup; the original has no separate backup function.
  8. Shared repositories (git_backend): BareGitRemote makes a bare remote; GitWorktree clones it, writes and reads files, commit_and_push pushes pending commits and reports a remote error, pull fast-forwards the tracked branch or reports the conflict.
  9. Not wired: no service code calls this repository. Dreaming (M_3_B_3) edits blocks in the letta schema and does not write the repository. What it must do when wired: after each block change committed in the database, commit the same change to the agent's repository so history and time travel cover the service's blocks.
tools:
- git
- asyncio
- subprocess
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_3_A_4
- agent_id: string, CORE — whose repository
- commit_sha: string, CORE — the commit made or read at
- files_changed: integer, DEBUG — files in the commit
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- The git program is absent -> RuntimeError before any repository is touched
- A storage path leaving the base folder -> refused, nothing written
- A pull that cannot fast-forward -> the remote's conflict error reported, nothing merged
- Another organization's repository -> nothing returned
- git_backend.py carries the inherited one-line docstrings "Module docstring.", "Class docstring." and "Method docstring." -> the documentation rule is not met in that file (read from the code)
- A block changed by dreaming or a sleep-time agent -> not committed to any repository, because the repository is not wired (ledger row 10 stays unproven)
tests:
- type: unit
  description: Time travel and history (ledger row 10, run 2026-10-20 by prove_memory_repo.py)
  input: a repository made with blocks human and persona; update human, then delete persona
  expected: history newest first holds three commits; the blocks at the first commit include persona with its first text
  tolerance: exact
- type: unit
  description: Shared repository across two agents (ledger row 11, proven 2026-10-20)
  input: a bare remote with two clones; agent a pushes a file, agent b pulls it, extends and pushes; agent a pulls
  expected: agent a reads b's extension; the remote's log shows both commits
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 a deleted block is withheld, not destroyed
  input: delete_block_async for one block, then get_blocks_async at HEAD and at the commit before
  expected: absent at HEAD, present with its text at the earlier commit
  tolerance: exact
logging_tier: DEBUG

#### M_3_B — Letta Tier and Model Steps
purpose: Make Letta the service's episodic tier, writing recall on the owner's transaction and answering searches by every model's numbers, and run Letta's compaction, sleep-time consolidation and dreaming with the language model the settings name.
inputs:
  - captured events with their verified payload bytes, from the owed-work processor
  - search questions with the caller's visibility, from tier retrieval
  - model operation requests for part letta: seed_agent, compact, setup_sleeptime, consolidate, dream
outputs:
  - processing results for receipts (processed or processed_no_new_fact)
  - per-model rankings of recall messages, or labelled substring hits
  - compaction summaries, sleep-time and dream reports with the blocks they changed
dependencies:
  - M_2_A_1
  - M_2_B_1
  - M_3_A_1
  - M_3_A_2
tests:
  - A captured note -> episodic receipt processed by letta-duckdb-recall
  - The same event applied twice -> processed_no_new_fact the second time, one copy stored
  - A question with numbers for a fresh model -> one ranking per model, labelled with the model
  - compact in mode sliding_window on a 15-message conversation -> older part summarised, newer kept
  - dream with step_count reached -> the reflection agent edits a block and the place advances

##### M_3_B_1 — Letta Recall Tier
job: Serve as the episodic tier, holding each captured event as Letta recall messages that searches rank by every fresh model's numbers.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- event_envelope: JSON object — the captured event (version 1 or 2), or an owner-authored removal (system.expire or system.forget)
- verified_payload_bytes: bytes — the payload whose fingerprint was checked at capture, or none
- native_tx_handle: DuckDB connection — the owner's open transaction, for apply
- scope: RetrievalScope — the caller's identity and resolved visibility (own, granted or pool)
- query: string — the search question
- limit: integer — the most hits per ranking
outputs:
- processor_result: ProcessorResult — status processed or processed_no_new_fact, processor letta-duckdb-recall, the letta_memory_model version, projection letta-recall-1
- tier_rankings: TierRankings — per model label the hits nearest first, and each model left out with its reason
- substring_hits: list of JSON object — event_id, actor_agent_id, message_id, role, text, sequence_id, ranking, when no model has numbers
artifacts:
- episodic_hit.json
data_contracts:
- letta_message.json
- episodic_hit.json
seam_contracts:
- processor seam: prepare(envelope, verified bytes) runs with no database; apply(native_tx_handle, prepared) runs on the owner's open transaction and never begins, commits, rolls back or closes it
- retriever seam: prepare_query(scope, query, limit) runs outside any transaction; retrieve(native_handle, scope, prepared) only reads
- numbers seam: attach_numbers(services) gives the tier the owner's numbers services; question_numbers_for_events asks services.question_vectors("event", query); rank_events ranks the permitted live events per model from that model's numbers table
dependencies:
- M_0_D_1
- M_1_B_1
- M_1_C_1
- M_2_A_1
- M_7_B_3
- M_3_A_2
invariants:
- agent_memory:INV-001
- agent_memory:INV-003
- agent_memory:INV-004
- agent_memory:INV-006
- agent_memory:INV-007
- agent_memory:INV-010
tunable_parameters:
  - param_name: tiers.episodic.organization_id
    default_value: "org_local_proof"
    param_type: string
    description: The one Letta organization the episodic tier writes and reads every recall row under, and the only organization the letta model part accepts; a stored setting with no code default, and a missing or empty value is refused when the settings load.
intelligence_pattern: none
logic: |
  1. Construction: an empty organization_id raises ValueError. Nothing is opened until the owner's first call.
  2. prepare (no database, no model): if the event is a removal, read the target event and its actor from the verified bytes; a removal that does not match its causation event raises TerminalProcessingError. Otherwise read the producing actor (none raises TerminalProcessingError) and the text, kind and role messages from the verified bytes (bytes that are not UTF-8 raise TerminalProcessingError). The role messages, or one message of role "user" holding the text, become Letta messages: id "message-" plus a uuid5 of the event id and the message's index, otid the event id, agent and sender the actor.
  3. apply (on the owner's transaction): bind the store to the owner's handle; a different handle later raises RuntimeError. Ensure the letta schema. For a removal, delete the target event's messages (found by otid); status processed when any existed, else processed_no_new_fact. For a capture, add only the messages whose ids are not stored yet; status processed when any were new, else processed_no_new_fact. The owner commits the receipt with these rows on the same connection, so a replay never adds a second copy.
  4. prepare_query (outside any transaction): fetch the question's numbers once per model that is fresh for whole-event pieces; each stale model, or one whose question numbers the supplier cannot give, is left out with its reason. With no numbers services both lists are empty. The tier never embeds the question.
  5. retrieve with question numbers for at least one model: per model, rank the events the scope permits (own: the caller's; granted: the granted actors, none means no hits; pool: every producer), leaving out removed events, retired pieces and stale numbers, nearest first by cosine distance, one row per event. For each ranked event return its recall messages in message order, each carrying the event's distance and the label "cosine distance of <model>'s own numbers for the event"; an event with no recall message is skipped. Cut each ranking to limit and return the models left out. The tier never ranks across models: merging is M_8_B_1's.
  6. retrieve with no question numbers: own reads the caller's agent, granted the granted actors (none answers no hits), pool every agent with messages; any other mode raises ValueError. With no letta schema yet, answer no hits. Otherwise run Letta's substring search (an empty query matches everything), newest first by sequence, cut to limit, each hit labelled "case-insensitive substring match, newest first; not semantic relevance".
  7. model_labels answers none: the tier holds no numbers of its own, so a numbers rebuild covers nothing here.
tools:
- duckdb
- uuid
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_3_B_1
- event_id: string, CORE — the event processed or ranked
- receipt_status: string, CORE — processed or processed_no_new_fact
- models_ranked: list of string, CORE — model labels that ranked
- models_left_out: object, CORE — model label to reason
- ranking_kind: string, CORE — numbers or substring
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Payload bytes that are not UTF-8, an event with no producing actor, or a removal that does not match its causation event -> TerminalProcessingError; the receipt records a terminal failure and nothing is stored
- The owner passes a different handle after the first -> RuntimeError, nothing written
- An unknown visibility mode -> ValueError
- No numbers services, no registered model, or every model stale -> the substring fallback, each hit labelled as not semantic relevance; the models left out are reported with their reasons
- A question sharing no words with any message and no numbers -> no hits (before 2026-10-27 this was the only answer)
- Ranking by meaning (ledger rows 7 and 8, recall half; run 2026-10-27 with static_retrieval_mrl_en_v1) -> 'How many cats does Ada have?' returned the update chat and the first chat ranked by distance; in the evening run with seven readers, the old note 'joined the Bergen Marine Institute' stayed first for 'Where does Ada work now?', and the second stage was right first in 3 of 8 episodic cases, too few to decide anything
- An event's recall messages are capped at limit per event before the ranking is cut, so one long event can fill a ranking (read from the code, not run)
tests:
- type: unit
  description: Live capture, search, forget and restart (ledger row 7, run 2026-10-22 through the service)
  input: capture "Ada lives in Bergen and likes tea", retrieve "Bergen" with no numbers, forget the note, stop and restart the service, retrieve again
  expected: receipt processed by letta-duckdb-recall; the note found with the substring label; after forget and restart the note absent while the other notes are still found
  tolerance: exact
- type: unit
  description: Ranking by meaning
  input: numbers supplied for one fresh model; the question 'How many cats does Ada have?' over chats about cats
  expected: one ranking for that model, nearest first, every hit labelled with the model's name and carrying its event's distance
  tolerance: exact
- type: invariant
  description: agent_memory:INV-003 a replay adds nothing
  input: apply the same captured event twice on two transactions
  expected: the second result processed_no_new_fact; one copy of each message stored
  tolerance: exact
- type: invariant
  description: agent_memory:INV-010 visibility constrains the tier
  input: retrieve with own visibility for agent a over events of agents a and b
  expected: only agent a's messages, in both the numbers path and the substring path
  tolerance: exact
logging_tier: CORE

##### M_3_B_2 — Compaction
job: Compact one stored Letta conversation by summarising its older messages with the configured language model in the configured mode.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- compact_request: JSON object — organization_id, agent_id, conversation_id, mode (optional, overrides the setting for this call), force (optional, true when absent)
- model: string — the model name the model step router took from the language_model setting (main or second opinion)
outputs:
- compact_answer: JSON object — compacted, mode_used, fallback_notes, summary_text, summary_message_id, messages_before, messages_after, evicted_message_ids, tokens_before, tokens_after; or compacted false with the reason
artifacts:
- letta_conversation_context.json
- compact_answer.json
data_contracts:
- letta_message.json
- letta_conversation_context.json
- compact_answer.json
seam_contracts:
- model part protocol: name letta, steps, achieved_model (the model the server's reply named), run_step(step, args, model)
- LettaStoreAccess: a read, or one committed write, on the service's database access; never held across a model call
- Chat: one OpenAI-style request per call through the door that records the answering model; it never retries
dependencies:
- M_2_B_1
- M_3_A_1
- M_3_A_2
invariants:
- agent_memory:INV-005
- agent_memory:INV-006
- agent_memory:INV-011
- agent_memory:INV-012
tunable_parameters:
  - param_name: letta.compaction.mode
    default_value: "sliding_window"
    param_type: string
    description: Which way compaction summarises (all, sliding_window, self_compact_all, self_compact_sliding_window); a stored setting with no code default.
  - param_name: letta.compaction.prompt_acknowledgement
    default_value: "false"
    param_type: bool
    description: Whether an assistant acknowledgement is placed before the transcript; a stored setting with no code default.
  - param_name: letta.compaction.clip_chars
    default_value: "50000"
    param_type: int
    description: Longest summary kept in characters; longer is cut with a truncation note; a stored setting with no code default.
  - param_name: letta.compaction.sliding_window_percentage
    default_value: "0.3"
    param_type: float
    description: Share of the history first evicted by a sliding window (at most 1.0); a stored setting with no code default.
  - param_name: letta.compaction.eviction_step
    default_value: "0.1"
    param_type: float
    description: How much more of the history each sliding-window retry evicts; a stored setting with no code default.
  - param_name: letta.compaction.all_word_limit
    default_value: "500"
    param_type: int
    description: Word limit written into the all-mode prompts; a stored setting with no code default.
  - param_name: letta.compaction.sliding_word_limit
    default_value: "300"
    param_type: int
    description: Word limit written into the sliding-window prompts; a stored setting with no code default.
  - param_name: letta.compaction.context_window_tokens
    default_value: "8192"
    param_type: int
    description: The model's context window in tokens, used for the trigger and the eviction goal; a stored setting with no code default.
  - param_name: letta.compaction.trigger_multiplier
    default_value: "0.9"
    param_type: float
    description: Share of the context window at which compaction starts when not forced; a stored setting with no code default.
  - param_name: letta.compaction.approx_bytes_per_token
    default_value: "4"
    param_type: int
    description: Bytes counted as one token by the approximate counter, since no tokenizer is loaded; a stored setting with no code default.
  - param_name: letta.compaction.approx_safety_margin
    default_value: "1.3"
    param_type: float
    description: Factor added to approximate token counts because they run low; a stored setting with no code default.
  - param_name: letta.compaction.tool_return_truncation_chars
    default_value: "5000"
    param_type: int
    description: Longest tool reply kept in the transcript after the model first reports the request too long; a stored setting with no code default.
  - param_name: letta.compaction.fallback_budget_fraction
    default_value: "0.6"
    param_type: float
    description: Share of the context window the transcript may fill after its middle is cut; a stored setting with no code default.
  - param_name: letta.compaction.fallback_bytes_per_token
    default_value: "4"
    param_type: int
    description: Bytes per token used to turn that share into a byte budget; a stored setting with no code default.
  - param_name: letta.compaction.fallback_min_budget_bytes
    default_value: "2000"
    param_type: int
    description: Smallest transcript byte budget allowed after the cut; a stored setting with no code default.
  - param_name: letta.compaction.fallback_overhead_bytes
    default_value: "1024"
    param_type: int
    description: Bytes reserved for the prompt wrapper when the transcript is cut; a stored setting with no code default.
  - param_name: letta.compaction.truncate_head_fraction
    default_value: "0.3"
    param_type: float
    description: Share of the byte budget kept from the start of a cut transcript; a stored setting with no code default.
  - param_name: letta.compaction.truncate_tail_fraction
    default_value: "0.3"
    param_type: float
    description: Share of the byte budget kept from the end of a cut transcript; a stored setting with no code default.
  - param_name: letta.compaction.timezone
    default_value: "UTC"
    param_type: string
    description: Time zone in which the summary note's time is written; a stored setting with no code default.
intelligence_pattern: none
logic: |
  1. The letta part is built only when the letta settings and tiers.episodic.organization_id exist. Building it parses letta.compaction; a missing or unknown key, a wrong type, or a sliding_window_percentage above 1.0 raises ValueError naming it. letta.compaction.prompt may be absent, meaning the mode's own prompt (Letta's four prompts, word for word, with the two word limits from the settings).
  2. run_step checks that organization_id equals the service's organization, else PermissionError, and that agent_id and conversation_id are non-empty text, else ValueError, before any model call. A mode in the request replaces the setting for this call only.
  3. Read (one short read) the conversation's saved context, or, the first time, every message of the conversation with the agent's compiled core memory as the system text. No message raises ValueError "The conversation has no messages to compact".
  4. Count tokens approximately (bytes per token, times the safety margin, tools included). With force false and the count below context_window_tokens times trigger_multiplier, return compacted false with the count and the threshold.
  5. Summarise outside any transaction, by mode. all: summarise everything after the system message. sliding_window: start at sliding_window_percentage of the messages, move the cut back to an assistant message (or an approval request carrying tool calls), raise the share by eviction_step until what remains is below (1 minus the share) of the window; no cut, or a cut at the last message, raises ValueError, which falls back to all. self_compact_all and self_compact_sliding_window send the agent's own history with the summary request as a last user message; self_compact_all falls back to self_compact_sliding_window and then to all; self_compact_sliding_window falls back to all. A context-window error in a sliding mode is raised, never retried as a fallback. Every fallback is logged and returned in fallback_notes.
  6. One summary request: send the prompt and the transcript; if the model reports its context window exceeded, send again with tool replies cut to tool_return_truncation_chars; if that fails, cut the transcript's middle to the byte budget (context_window_tokens times fallback_budget_fraction times fallback_bytes_per_token, less the prompt and fallback_overhead_bytes, never below fallback_min_budget_bytes, keeping truncate_head_fraction and truncate_tail_fraction) and send a last time; its failure is raised. A summary longer than clip_chars is cut with "... [summary truncated to fit]".
  7. If the result is still at or over the threshold, a sliding-window result is summarised again in all mode; if the system message alone is at or over the window, raise SystemPromptTokenExceededError.
  8. Wrap the summary in Letta's system-alert note with the time in letta.compaction.timezone and the compaction counts, as a message of role summary placed after the system message.
  9. One committed write: store the summary message in the conversation and replace the saved context (system text and the ids now in context). No message is ever deleted; evicted messages stay in recall and are only out of context.
  10. Return the answer; the model step router adds the requested model, the model the server's reply named, the placement and the seconds.
tools:
- openai
- httpx
- pydantic
- zoneinfo
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_3_B_2
- conversation_id: string, CORE — the conversation compacted
- mode_used: string, CORE — the mode that produced the summary
- fallback_notes: list of string, CORE — every fallback taken and why
- tokens_before: integer, CORE — approximate tokens before
- tokens_after: integer, CORE — approximate tokens after
- achieved_model: string, CORE — the model the server's reply named
- model_seconds: float, TRAINING — time the step took
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- An organization other than the service's -> PermissionError before any model call
- A conversation with no messages -> ValueError, no model call
- A server refusal or a lost connection -> ChatError naming the HTTP status or the error; nothing stored
- The server says the request is too long in a sliding mode -> ContextWindowExceededError raised; nothing stored
- No valid sliding-window cut -> falls back to all mode, noted in fallback_notes
- The system message alone exceeds the window -> SystemPromptTokenExceededError; nothing stored
- A missing or unknown letta.compaction setting -> ValueError naming it when the letta part is built
- Runs 2026-10-23 through the service with ornith_35b_a3b_cpu, processor only (ledger row 9) -> all: 128 s, 15 messages to 2, accurate; sliding_window: 70 s, 15 to 11, summarised only the evicted part; self_compact_all: 397 s, 15 to 2, accurate; self_compact_sliding_window: 265 s, 15 to 11, accurate; qwen38_4b_distill_cpu also passed in the helper's proof
tests:
- type: unit
  description: Mode all on a 15-message conversation
  input: seed_agent with blocks and 15 messages, then compact with mode all and force true
  expected: compacted true, mode_used all, messages_after 2, one summary message stored with role summary, no message deleted
  tolerance: exact
- type: unit
  description: Below the trigger without force
  input: compact with force false on a conversation whose approximate count is below context_window_tokens times trigger_multiplier
  expected: compacted false with the count and the threshold; no model call
  tolerance: exact
- type: unit
  description: The summary is faithful
  input: the KELP-4412 fixture conversation compacted in mode all
  expected: the summary names the ticket, the plan link, the 40-metre correction, the preferences and the dates, inventing nothing
  tolerance: semantic
- type: invariant
  description: agent_memory:INV-012 no transaction held across the model call
  input: compact with a model server that answers slowly, while another write runs on the database
  expected: the other write commits before the model answers; the step reports the model the server's reply named
  tolerance: exact
logging_tier: CORE

##### M_3_B_3 — Sleep-Time Consolidation and Dreaming
job: Let a background memory agent revise an agent's memory blocks from its recent messages whenever a turn counter or the dream trigger says so.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- setup_sleeptime_request: JSON object — organization_id, group_id, foreground_agent_id, sleeptime_agent_id, sleeptime_agent_frequency (a whole number, or null for every turn)
- consolidate_request: JSON object — organization_id, group_id, message_ids (optional)
- dream_request: JSON object — organization_id, agent_id, instruction (optional), force (optional, false when absent)
- model: string — the model name the model step router took from the language_model setting
outputs:
- setup_answer: JSON object — the blocks created and the blocks shared
- consolidate_answer: JSON object — ran, reason, turns_counter, and per sleep-time agent the steps, stop_reason, tool_calls, models_reported, changed_blocks (before and after), note, messages_reviewed
- dream_answer: JSON object — ran, reason, forced, messages_reviewed, steps, stop_reason, tool_calls, models_reported, changed_blocks, advanced
artifacts:
- letta_sleeptime_group.json
- letta_dream_state.json
data_contracts:
- letta_block.json
- letta_message.json
- letta_sleeptime_group.json
- letta_dream_state.json
seam_contracts:
- model part protocol: steps setup_sleeptime, consolidate and dream of part letta
- every tool call is its own committed write through LettaStoreAccess, and every model call is made with the store released
dependencies:
- M_2_B_1
- M_3_A_1
- M_3_A_2
- M_3_B_2
invariants:
- agent_memory:INV-005
- agent_memory:INV-011
- agent_memory:INV-012
tunable_parameters:
  - param_name: letta.sleeptime.max_steps
    default_value: "6"
    param_type: int
    description: Most model calls one sleep-time agent run may make (at least 1); a stored setting with no code default.
  - param_name: letta.dream.trigger
    default_value: "step-count"
    param_type: string
    description: What starts a dream: off, step-count, or compaction-event; a stored setting with no code default.
  - param_name: letta.dream.step_count
    default_value: "3"
    param_type: int
    description: New assistant messages since the last dream that start one when the trigger is step-count (at least 1); a stored setting with no code default.
  - param_name: letta.dream.max_steps
    default_value: "6"
    param_type: int
    description: Most model calls one dream may make (at least 1); a stored setting with no code default.
intelligence_pattern: event_driven
logic: |
  1. Every step checks organization_id against the service's (else PermissionError) and its required ids (else ValueError) before any model call. The three state tables (context, sleep-time group, dream place) are created when missing.
  2. setup_sleeptime (no model): a sleeptime_agent_frequency that is not a whole number or null raises ValueError. In one committed write, record the group with its counter at zero; an existing group id raises ValueError and writes nothing. Give the sleep-time agent a memory_persona block holding Letta's sleep-time persona text, and attach every block of the foreground agent to it as well, so its edits reach both; a label it already holds is skipped, not replaced.
  3. consolidate: a message_ids that is not a list of text or null raises ValueError. In one write: with a frequency, add one turn to the counter, wrapping at the frequency, and run only when it reaches zero; with no frequency, run every turn. Choose the named messages, or every foreground message after the group's last processed one. None chosen answers ran false "no response messages were generated"; not due answers ran false "turn counter has not reached the frequency". When due, save the newest chosen message as last processed.
  4. For each sleep-time agent: with a frequency, also read the foreground messages between the last processed one and the new ones; if an id cannot be found, carry on with the new messages and return the note "prior messages unavailable: a message id was not found in the agent's recall" (the original carried on silently). Put the messages into Letta's sleep-time task text.
  5. The memory-agent loop: rebuild the system prompt before every step from Letta's sleep-time instructions and the agent's compiled memory, so edits show in the next step; offer the tools memory_replace, memory_insert, memory_rethink and memory_finish_edits. Each tool call runs as its own committed write through run_memory_tool; a refusal (unknown tool, arguments that are not a JSON object, a read-only block, text not found, a limit exceeded) writes nothing and goes back to the model as the tool's error. memory_finish_edits is the terminal rule and ends the run when it succeeds; any other tool continues it; a reply with no tool call ends the turn; max_steps ends it otherwise. Report the blocks whose text changed, before and after, and every model name the server's replies gave.
  6. dream: read the agent's messages after its last dream's place. Trigger off never fires; step-count fires when the new assistant messages reach letta.dream.step_count; compaction-event fires when a summary message (made by M_3_B_2) arrived since the last dream. Not firing and not forced answers ran false with the reason. A transcript with no text left after removing tool and empty messages answers ran false "no new messages to review".
  7. The dream runs the same loop on the agent's own blocks with Letta Code's reflection prompt and the optional instruction, bounded by letta.dream.max_steps. Its place advances to the newest message read only when it ended by itself (terminal tool or end of turn); a run stopped by max_steps leaves the place, so the next dream reads the same messages again.
  8. Not wired: dreaming writes the letta schema only; it does not commit to the git memory repository (M_3_A_4).
tools:
- openai
- httpx
- pydantic
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_3_B_3
- group_id: string, CORE — the sleep-time group, for consolidate
- agent_id: string, CORE — the agent whose blocks were reviewed
- steps: integer, CORE — model calls made
- stop_reason: string, CORE — terminal_tool, end_turn or max_steps
- changed_blocks: integer, CORE — blocks whose text changed
- models_reported: list of string, CORE — model names the server's replies gave
- model_seconds: float, TRAINING — time the step took
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- An existing group id on setup -> ValueError, nothing written
- An unknown group on consolidate -> LookupError, no model call
- A tool refusal inside the loop -> nothing written for that call; the model sees the error and the run continues
- A server refusal or lost connection -> ChatError; edits already committed by earlier tool calls stay, each having been its own write
- A run that reaches max_steps -> stop_reason max_steps; a dream's place does not advance
- A missing or unknown letta.sleeptime or letta.dream setting, or a step bound below 1 -> ValueError naming it when the letta part is built
- The consolidate step names only group_id, which the model operation does not check against the caller's visibility; any authenticated caller can run consolidation on any group of the service's organization (read from the code, not run)
- Runs 2026-10-23 with ornith_35b_a3b_cpu, processor only (ledger row 9) -> consolidation 401 s, two steps, ended by memory_finish_edits, rewrote the human block with the Bergen years and the Oslo move (one tense slip); dreaming 667 s, started by the step-count trigger (14 new steps), wrote an accurate human block; qwen38_4b_distill_cpu also passed in the helper's proof
tests:
- type: unit
  description: Consolidation ends on the terminal tool
  input: setup_sleeptime with frequency null, then consolidate after a turn telling of a move to Oslo
  expected: ran true, stop_reason terminal_tool, the human block changed to include the move, turns_counter null
  tolerance: semantic
- type: unit
  description: A frequency of 2 skips the first turn
  input: setup_sleeptime with frequency 2, then consolidate once
  expected: ran false "turn counter has not reached the frequency", turns_counter 1, no model call
  tolerance: exact
- type: unit
  description: Dream trigger by step count
  input: dream with trigger step-count, step_count 3 and two new assistant messages, then again after a third
  expected: first ran false naming 2 steps; second ran true and advanced true when it ends by itself
  tolerance: exact
- type: invariant
  description: agent_memory:INV-012 the store is released during every model call
  input: a dream against a slow model server while another write runs on the database
  expected: the other write commits before the model answers; models_reported holds the name the server gave
  tolerance: exact
logging_tier: CORE

### M_4 — Temporal Memory (Graphiti and Zep)
description: Keeps the temporal memory: every captured event becomes a Graphiti episode on the owner's transaction, the Graphiti model steps turn episodes into entities and dated facts that a contradiction ends, communities and sagas summarise the graph, and Zep's users, sessions, messages and summaries sit beside it. Graphiti 0.31.1 (commit d50741d1, Apache-2.0) runs on a DuckDB driver of the project's own (schema graphiti in the memory database, native SQL only), with the Zep CE v1.0.2 store and Zep's thread summariser (from commit 411142c^) ported into the same package. Wired today: the temporal tier (episodes, facts ranked by every model's numbers, facts no longer current held back), the graphiti model part (ingest, communities, saga_summary), the zep model part (seed_session, set_instructions, thread_summary, get_summary, user_summary, relevant_facts) and the keyword-only graph search for the context block. Built but not wired: the MCP server with 13 tools, which refuses every tool that needs a model because nothing supplies one. Waiting on numbers: Graphiti's own cosine and hybrid search, embedding-based entity matching, add_triplet, bulk ingestion, the cross-encoder reranker and Zep's summary embedding. Memory never makes numbers, so every Graphiti slot that would make one refuses with "numbers not yet supplied".
dependencies:
  - M_0
  - M_1
  - M_2
  - M_7
entry_conditions:
  - M_0, M_1, M_2 and M_7 are VERIFIED
  - the memory database holds graphiti.contradiction_candidates (required when the temporal tier is configured) and, for the zep part, the zep settings
  - test fixtures are prepared on a throwaway copy of the memory database
exit_conditions:
  - all submodules VERIFIED
  - all artifacts schema-valid
  - all tests passed
  - end-to-end module test passed
module_test: On a throwaway database with a language model configured, capture the two Ada notes and confirm their temporal receipts read processed by graphiti-duckdb-episodes; run the graphiti ingest step on both and confirm dated facts are stored, the fact "Ada Lovelace lives in Bergen." is ended by the second note under same_subject candidates, and numbers are owed for every stored fact; with those numbers supplied, retrieve 'Where does Ada work now?' and confirm the ended facts are held back in not_current; run the context operation and confirm FACTS carry their valid and invalid dates; seed a Zep session of 14 messages and confirm thread_summary folds the older messages in and keeps the newest half.

#### M_4_A — Graphiti on DuckDB
purpose: Store Graphiti's graph (episodes, entities, dated facts, mentions, communities, sagas) in schema graphiti of the memory database through native SQL, serve the temporal tier from it, and search it by words.
inputs:
  - a native DuckDB handle, borrowed from the owner or opened by the driver
  - captured events with their verified bytes; search questions with the caller's visibility
  - Graphiti node and edge models from the model steps
outputs:
  - rows in graphiti.graph_node and graphiti.graph_edge
  - processing results for receipts; per-model rankings of episodes and facts, and the facts no longer current
  - Graphiti SearchResults found by words
dependencies:
  - M_0_D_1
  - M_2_A_1
tests:
  - A Cypher query sent to the driver -> refused
  - A captured event -> one episode whose uuid is derived from the event id
  - A removal -> the episode, the facts it created and the entities only it mentioned are deleted
  - A fact ended before the question's time -> held back and listed in not_current
  - A quoted phrase in a keyword search -> refused

##### M_4_A_1 — DuckDB Graph Driver and Operations
job: Store Graphiti's graph in schema graphiti of the memory database through native SQL, on a handle the driver owns or borrows.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- connection_or_path: DuckDB connection or file path — borrowed from the owner, or opened by the driver on a DuckDB file
- graph_models: Graphiti models — EpisodicNode, EntityNode, CommunityNode, SagaNode, EntityEdge, EpisodicEdge, CommunityEdge, HasEpisodeEdge, NextEpisodeEdge
- group_ids: list of string — the namespaces to read or clear
outputs:
- stored_models: Graphiti models — read back from storage, never invented
- episodes: list of EpisodicNode — the latest eligible episodes, oldest first
- community_clusters: list of list of EntityNode — Graphiti's label-propagation clusters per group
artifacts:
- graphiti_graph_node.json
- graphiti_graph_edge.json
data_contracts:
- graphiti_graph_node.json
- graphiti_graph_edge.json
seam_contracts:
- DuckDBDriver(connection=handle) borrows the owner's handle and never begins, commits, rolls back or closes it; DuckDBDriver(db=path) owns its connection and closes only that
- graphiti_core's driver operation interfaces (entity, episode, community, saga node ops; entity, episodic, community, has-episode, next-episode edge ops; search ops; graph ops) are implemented here; a coroutine's body runs synchronously on the given handle
dependencies:
- M_0_D_1
invariants:
- agent_memory:INV-001
- agent_memory:INV-004
- agent_memory:INV-006
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Building a driver runs the schema statements: schema graphiti, table graph_node (uuid primary key, label, properties JSON), table graph_edge (uuid primary key, source_node_uuid, target_node_uuid, label, properties JSON), sequence graph_fts_gen_seq and table graph_fts_generations. A borrowed handle must be a native DuckDB connection (else refused); an owned one is opened by connect_native, which refuses a scheme (md:, s3:, sqlite:) and a SQLite file.
  2. execute_query runs native SQL with bound parameters and refuses Cypher. A session on a different database name raises ValueError.
  3. Each family's save is an upsert by uuid that refuses a uuid already owned by another family (ValueError or TypeError for a model of the wrong kind). Edge families prove both endpoints exist with an allowed label before saving. Bulk saves run in one transaction so a failure stores none.
  4. Deleting a node deletes its incident edges; deleting an edge leaves both endpoints. Group deletes act only on the group named.
  5. Reads return stored models only, with group filters and Graphiti's descending-uuid cursor and limit; a missing uuid raises Graphiti's NodeNotFoundError or EdgeNotFoundError for single reads and is simply absent from list reads. Stored embeddings are read back as stored, never made.
  6. retrieve_episodes returns the last_n episodes valid at or before the reference time, filtered by group, source and saga, selected newest first and returned oldest first (Graphiti's contract).
  7. remove_episode_and_singletons, in one transaction: delete the RELATES_TO facts the episode lists whose first provenance entry is this episode, delete the entities of the group mentioned only by this episode, then delete the episode with its edges. An unknown episode raises NodeNotFoundError.
  8. Graph ops: clear named groups, build or drop Graphiti's own lookup indexes, return label-propagation community clusters, find or create an entity's community by Graphiti's rule, and list mentioned entities and their communities.
  9. Group namespaces: the service gives every actor its own group, "actor-" plus the SHA-256 of the actor id, because Graphiti accepts only letters, digits, dashes and underscores; the actor itself is kept in each row's provenance.
  10. graphiti_core changes for DuckDB: a remove_episode hook, sagas through typed operations, the language-model answer cache moved from a SQLite file to the table graphiti.llm_cache on a passed-in owner connection (refused when missing), and errors that were swallowed now logged.
tools:
- duckdb
- graphiti_core
- asyncio
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_4_A_1
- group_id: string, CORE — the namespace acted on
- family: string, DEBUG — node or edge family written
- rows_written: integer, DEBUG — rows upserted or deleted
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Cypher sent to execute_query -> refused; nothing runs
- A uuid already owned by another family -> ValueError, nothing written
- An edge whose endpoint is missing or of a wrong label -> refused, nothing written
- property_filters in a search filter -> refused rather than ignored
- graph_fts_generations is created and never written -> an unused table in schema graphiti (read from the code)
- No vector index is used -> every similarity search is an exact cosine scan
tests:
- type: unit
  description: Removal keeps shared entities
  input: two episodes both mentioning Ada Lovelace, the first alone mentioning Bo Eriksen; remove the first
  expected: the first episode, its facts and Bo Eriksen are deleted; Ada Lovelace and the second episode remain
  tolerance: exact
- type: unit
  description: Family collision
  input: save an EntityNode with the uuid of a stored episode
  expected: ValueError; the episode unchanged
  tolerance: exact
- type: invariant
  description: agent_memory:INV-001 a borrowed driver never finishes the owner's transaction
  input: save an episode through DuckDBDriver(connection=handle) inside a transaction the owner rolls back
  expected: no episode stored; the handle still open
  tolerance: exact
logging_tier: DEBUG

##### M_4_A_2 — Graphiti Episode Tier
job: Serve as the temporal tier, holding each captured event as one Graphiti episode that searches rank, with the facts drawn from it, by every fresh model's numbers.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- event_envelope: JSON object — the captured event (version 1 or 2), or an owner-authored removal
- verified_payload_bytes: bytes — the payload checked at capture, or none
- native_tx_handle: DuckDB connection — the owner's open transaction, for apply
- scope: RetrievalScope — the caller's identity and resolved visibility
- query: string — the search question
- limit: integer — the most hits per ranking
outputs:
- processor_result: ProcessorResult — processed or processed_no_new_fact, processor graphiti-duckdb-episodes, the graphiti-duckdb version, projection graphiti-episode-1
- tier_rankings: TierRankings — per model label the episodes and facts nearest first, the models left out with reasons, and the facts no longer current
- recent_episodes: list of JSON object — the most recent episodes, when no model has numbers
artifacts:
- temporal_hit.json
- not_current_fact.json
data_contracts:
- graphiti_graph_node.json
- graphiti_graph_edge.json
- temporal_hit.json
- not_current_fact.json
seam_contracts:
- processor seam and retriever seam as in M_3_B_1
- numbers seam: question_numbers_for_events and rank_events for episodes; for facts, the derived pieces the supplier gave for each fact, joined to graphiti.graph_edge by the SHA-256 of the fact's words
dependencies:
- M_1_B_1
- M_1_C_1
- M_2_A_1
- M_7_B_3
- M_4_A_1
invariants:
- agent_memory:INV-001
- agent_memory:INV-003
- agent_memory:INV-004
- agent_memory:INV-006
- agent_memory:INV-007
- agent_memory:INV-010
- agent_memory:INV-013
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. prepare (no model, no database): a removal yields the target event; one that does not match its causation event raises TerminalProcessingError. Otherwise read the actor and the text and kind from the verified bytes; role messages become episode source message, JSON becomes json, anything else text. The event's time must be ISO 8601, else TerminalProcessingError. Build one EpisodicNode: uuid a uuid5 of the event id, name the event id, group "actor-" plus the SHA-256 of the actor, valid_at the event's time, created_at now, and provenance metadata (schema_version, event_type, source_revision, source_artifact_id, work_id, parent_event_id, causation_event_id, payload_sha256, exactness, event_id, actor_agent_id).
  2. apply (on the owner's transaction): for a removal, if the episode exists run remove_episode_and_singletons and report processed, else processed_no_new_fact; otherwise upsert the episode and report processed. The owner commits the receipt with it on the same connection.
  3. prepare_query (outside any transaction): fix the reference time to now, and fetch the question's numbers once per fresh model, each other model left out with its reason.
  4. retrieve with question numbers and a graphiti schema: per model, rank the permitted live events nearest first and return each event's episode (an event whose episode is gone is skipped) with its content, source, valid_at, source revision, distance and the label "cosine distance of <model>'s own numbers for the event".
  5. In the same ranking, facts: read every fact whose derived numbers for that model are fresh, whose event is permitted, not removed and whose piece is not retired, nearest first, one row per fact. A fact that begins after the reference time, or that Graphiti ended (invalid_at) at or before it, is not ranked and is returned in not_current with its event, words, valid_at and invalid_at. Each ranked fact carries its four timestamps (valid_at, invalid_at, expired_at, and the event's), the event it came from and the label "cosine distance of <model>'s own numbers for the fact". A fact with no numbers is not ranked.
  6. Episodes and facts of one model are merged by distance and cut to limit; not_current from every model is returned once per fact, in fact-uuid order.
  7. retrieve with no question numbers (or no graphiti schema): own reads the caller's group, granted the granted actors' groups (none answers no hits), pool every group; another mode raises ValueError; no schema answers no hits. Return Graphiti's retrieve_episodes up to limit, each labelled "most recent episodes, not query relevance". Facts are not returned on this path.
  8. model_labels answers none: the numbers live in the owner's per-model tables, so a numbers rebuild covers nothing here.
  9. Not built: a validity for episodes. An episode carries only the time it happened, so an old episode whose facts were ended still ranks as current. What it must do when built: hold back or mark an episode whose every fact has ended before the question's time, and report it in not_current.
  10. Not built: the bulk ingestion and fact_triple episode kinds (Graphiti's add_episode_bulk and add_triplet); both wait on numbers for names and facts.
tools:
- duckdb
- graphiti_core
- asyncio
- uuid
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_4_A_2
- event_id: string, CORE — the event processed or ranked
- receipt_status: string, CORE — processed or processed_no_new_fact
- models_ranked: list of string, CORE — model labels that ranked
- models_left_out: object, CORE — model label to reason
- not_current_count: integer, CORE — facts held back as no longer current
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Bytes that are not UTF-8, a time that is not ISO 8601, or a mismatched removal -> TerminalProcessingError; nothing stored
- A version 2 event with a wrong payload fingerprint -> refused at capture "Payload SHA256 mismatch" before this tier sees it (run 2026-10-22)
- An unknown visibility mode -> ValueError
- No model with question numbers -> the most-recent fallback, labelled as not query relevance; facts are not searched
- A fact ended before the question's time -> held back and listed in not_current (run 2026-10-27 evening: 'lives in Bergen' and 'joined the Bergen Marine Institute' held back for 'Where does Ada work now?')
- An old episode whose facts have ended -> still ranks, because episodes carry no validity (run 2026-10-27 evening: the note 'joined the Bergen Marine Institute' stayed first for the work question)
- The no-numbers fallback returns the last episodes oldest first, as Graphiti's retrieve_episodes does, while its docstring says newest first (read from the code, not run)
- A retry of apply for an event already ingested -> the episode is upserted with a new created_at and an empty list of its facts, so a later removal no longer finds the facts the ingest step listed on it (read from the code, not run)
- The json episode kind -> built in the tier, never run (ledger row 12)
tests:
- type: unit
  description: Episodes survive a restart and a repeat (ledger row 12, run 2026-10-22 through the service)
  input: a plain text event and a version 2 event captured; the identical event captured again; the service restarted; retrieve with no numbers
  expected: both episodes returned labelled "most recent episodes, not query relevance"; the repeat accepted once; the same event id with different content refused
  tolerance: exact
- type: unit
  description: Facts ranked by numbers (run 2026-10-27)
  input: the two Ada notes ingested with numbers supplied for their facts; ask 'Who is Ada's manager?'
  expected: the fact 'Bo Eriksen is the manager of Ada Lovelace and leads the Kelp Project.' first for the model, with its timestamps and distance
  tolerance: exact
- type: invariant
  description: agent_memory:INV-013 an ended fact is held back
  input: a fact with invalid_at 2026-10-20 and a question asked after that date
  expected: the fact is in no ranking and appears in not_current with its valid_at and invalid_at
  tolerance: exact
- type: invariant
  description: agent_memory:INV-010 visibility constrains episodes and facts
  input: own visibility for agent a over episodes and facts of agents a and b
  expected: only agent a's episodes and facts, on both paths
  tolerance: exact
logging_tier: CORE

##### M_4_A_3 — Keyword Graph Search
job: Find what in the stored graph matches a question by words alone, using Graphiti's BM25 method with rank fusion.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- handle: DuckDB connection — the owner's open handle, read only
- groups: list of string or null — the group ids the caller may see; null means every group
- query: string — the question, plain words
- limit: integer — the most results of each kind
outputs:
- search_results: Graphiti SearchResults — edges (facts with their four timestamps), nodes (entities with summaries), episodes and communities, each list best first
artifacts: []
data_contracts:
- graphiti_graph_node.json
- graphiti_graph_edge.json
seam_contracts:
- keyword_search(handle, groups, query, limit) is called by the service's context operation (M_8_D_1), which renders the result with Graphiti's search_results_to_context_string, and by the Zep relevant-facts graph (M_4_D_1)
- the client bundle's language-model, embedder and cross-encoder slots refuse; a search that reached any of them would be wired wrongly
dependencies:
- M_4_A_1
- M_4_A_2
- M_4_B_1
invariants:
- agent_memory:INV-004
- agent_memory:INV-010
- agent_memory:INV-011
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. If the graphiti schema does not exist yet, return an empty result and write nothing.
  2. Build Graphiti's client bundle on the owner's handle with a language-model slot that refuses ("keyword search must not call a language model") and embedder and reranker slots that refuse ("numbers not yet supplied").
  3. Run Graphiti's search with one method per kind, bm25, and reciprocal rank fusion as the reranker, for edges, nodes, episodes and communities, limited to the groups and to limit.
  4. The driver's BM25 is plain SQL with Lucene's constants k1 1.2 and b 0.75, over names and facts for edges, names and summaries for entities, content, source and description for episodes, and names for communities. Words are letters and digits. A quoted phrase or a Boolean operator (AND, OR, NOT, &&, ||, or + - ! before a term) raises ValueError; a query over Graphiti's term budget becomes empty.
  5. Built in the driver but not used by this search: cosine similarity (1 plus cosine, halved) over stored JSON embeddings, breadth-first walks by recursive SQL, and the node-distance and episode-mention rerankers. Graphiti's MMR and cross-encoder rerankers and its 16 search recipes need numbers or a reranking model and are not run.
  6. Not built: Graphiti's own hybrid search (cosine plus BM25 plus breadth-first) and the local BGE reranker. What it must do when built: take each registered model's numbers for the question from the supplier and rank by every model, as the temporal tier does, with the reranker taken from the supplier's cross-scorer. Ranking facts by meaning on the service path today is done by the temporal tier (M_4_A_2), not by Graphiti's search.
tools:
- graphiti_core
- duckdb
- asyncio
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_4_A_3
- groups_searched: integer, CORE — how many groups, or every group
- edges_found: integer, DEBUG — facts returned
- nodes_found: integer, DEBUG — entities returned
- communities_found: integer, DEBUG — communities returned
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- A quoted phrase or a Boolean operator in the question -> ValueError; the context operation answers an error
- No graphiti schema -> empty result; the context block's WAITING section says FACTS, ENTITIES and COMMUNITIES wait on a language model while the graph holds no entity
- A stale fact still current in the graph -> returned with invalid_at Present; the content is only as right as the contradiction step (run 2026-10-23: 'Ada Lovelace lives in Bergen.' came through)
- Lucene's k1 1.2 and b 0.75 -> written in code, not stored settings (a Law 32 gap, stated)
- Graphiti's hybrid, cosine and reranked search -> not run; waits on numbers (ledger row 15)
tests:
- type: unit
  description: Facts with dates for the context block (ledger row 18, run 2026-10-23 through the context operation)
  input: the graph after both Ada notes; question "Where does Ada Lovelace work and live"
  expected: facts including "Ada Lovelace works at the Ministry of Fisheries" valid_at 2026-10-20 and the ended "joined the Bergen Marine Institute" fact with invalid_at 2026-10-20; entities with summaries; 0.2 to 0.4 s
  tolerance: exact
- type: unit
  description: Boolean syntax refused
  input: the question 'Ada AND Bergen'
  expected: ValueError naming a Boolean operator
  tolerance: exact
- type: invariant
  description: agent_memory:INV-004 keyword search never asks for numbers or a model
  input: a search over a graph whose embedder and model slots refuse
  expected: results returned and no refusal raised, proving neither slot was called
  tolerance: exact
logging_tier: DEBUG

#### M_4_B — Graphiti Model Steps
purpose: Run Graphiti's own language-model steps, made from the language_model setting, to turn a stored episode into resolved entities, dated facts, ended contradictions and summaries, and to name and summarise communities and sagas, with every similarity search replaced by words and shared entities because memory makes no numbers.
inputs:
  - model operation requests for part graphiti: ingest (event_ids, summaries), communities (group_id), saga_summary (group_id, saga_name, event_ids)
  - the stored episodes, entities and facts of the group
outputs:
  - entities, facts with four timestamps, ended facts, community names and summaries, saga summaries, stored in schema graphiti
  - numbers owed for every stored fact, once per registered Embedding model
dependencies:
  - M_2_B_1
  - M_4_A_1
  - M_4_A_2
tests:
  - Ingest of the first Ada note -> 6 entities and 4 dated facts, each grounded
  - Ingest of the second note with same_subject candidates -> 'lives in Bergen' and 'joined the Institute' ended
  - communities on the Ada group -> one community of 7 entities with an accurate summary
  - saga_summary for ada-story -> a grounded summary of the two episodes

##### M_4_B_1 — Ingest
job: Turn one stored episode into a resolved, dated, summarised piece of graph through Graphiti's own extraction steps, storing it with numbers owed for every fact.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- ingest_request: JSON object — event_ids (a non-empty list), summaries (graphiti_default or always_model, optional)
- model: string — the model name the model step router took from the language_model setting
outputs:
- ingest_answer: JSON object — per episode: event_id, fact_numbers_owed (requests and models, or the reason none), candidates, candidate_facts_shown_per_new_fact, stored_facts_before, entities (uuid, name, summary), facts (uuid, fact, source, target, four timestamps, new), ended_facts, and the seconds of each phase
artifacts:
- ingest_answer.json
data_contracts:
- graphiti_graph_node.json
- graphiti_graph_edge.json
- ingest_answer.json
seam_contracts:
- model part protocol: name graphiti, steps ingest, communities, saga_summary
- Graphiti's client bundle per call: OpenAIGenericClient on the recorded door from the language_model setting (model and small model both the routed model, base_url plus /v1, temperature and max_tokens when set); embedder and cross-encoder slots refuse with "numbers not yet supplied"
- numbers.request(model_id, revision, event_id, text, reason) records numbers owed by the supplier
dependencies:
- M_2_B_1
- M_7_A_3
- M_4_A_1
- M_4_A_2
invariants:
- agent_memory:INV-004
- agent_memory:INV-005
- agent_memory:INV-012
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Building the part requires graphiti.contradiction_candidates (M_4_B_2); the temporal tier is refused at settings load without it.
  2. Arguments: event_ids must be a non-empty list, else ValueError; a candidates argument is refused ("candidates is a setting now: graphiti.contradiction_candidates"); summaries must be graphiti_default or always_model, graphiti_default when absent. The model operation first refuses any event the caller may not see.
  3. For each event, one short read: the episode (none raises ValueError "event <id> has no episode in the graph yet"), the group's earlier episodes up to Graphiti's own schema limit, and the group's entities and facts.
  4. Outside any transaction: Graphiti's extract_nodes on the episode. Resolution, translated from resolve_extracted_nodes: for each extracted entity, candidates are the group's entities sharing at least one name word, most shared first, up to Graphiti's own candidate limit (this replaces Graphiti's search by similarity of numbers); try Graphiti's identity-property rule, then its exact-name similarity rule; entities still unresolved go together to Graphiti's model resolution (_resolve_with_llm); any left are new.
  5. Graphiti's extract_edges on the episode with the extracted entities, one generic Entity-to-Entity edge type, no custom ontology. Then dating and contradiction for every fact (M_4_B_2).
  6. Entity summaries by Graphiti's batch step; with always_model every summary goes through the model, otherwise Graphiti's own rule appends facts to a short summary without the model.
  7. One committed write: the entities, then the facts (resolved and ended), then the episode-to-entity MENTIONS links, then the episode with its list of facts. A failed model call stores nothing.
  8. Numbers owed: for every registered Embedding model and every distinct non-blank fact, record that the supplier owes its numbers for that event; the next numbers sync fetches each fact whole and stores it as a derived piece of the event, which is what lets the temporal tier rank facts. With no numbers services, answer requests 0 with the reason.
  9. Return the answer; the router adds requested_model, achieved_model, placement and seconds.
  10. Not built: embedding-based entity matching, add_triplet and bulk ingestion, which wait on numbers; custom Pydantic ontologies on the service path (only the MCP server's settings file declares entity and edge types).
tools:
- graphiti_core
- openai
- httpx
- duckdb
- asyncio
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_4_B_1
- event_id: string, CORE — the episode ingested
- entities_stored: integer, CORE — entities after resolution
- facts_stored: integer, CORE — facts stored new or resolved
- fact_numbers_owed: integer, CORE — numbers requests recorded
- achieved_model: string, CORE — the model the server's reply named
- phase_seconds: object, TRAINING — seconds per phase (extract entities, resolve, extract facts, date and contradiction, summaries)
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- An event with no episode yet -> ValueError, no model call
- A model answer cut at the token ceiling with invalid JSON -> the step fails and nothing is stored (2026-10-23: qwen38_4b_distill_cpu failed after 71 minutes, "Expecting value: line 1 column 4155")
- Any Graphiti path that asks for numbers -> RuntimeError "numbers not yet supplied: memory never makes numbers, so this Graphiti step that ranks by similarity cannot run here"
- A missed fact -> not stored (2026-10-23: "drinks green tea" became an entity but no fact; note 3's "the Institute thanked Bo for the data" missed)
- Entity summaries adding detail the note does not state (ledger row 19, revisited 2026-10-24) -> stored as written; the Bo Eriksen summary called him a marine biologist; row 19 not proven
- The proprietary Zep Cloud extraction model, reranker, embedder, Konig, dashboards and SOC 2 (ledger row 22) -> not reproduced and opaque; any local model slots into Graphiti's language-model slot (passed on ornith_35b_a3b_cpu), while the embedder and reranker slots refuse on purpose
- The 2026-10-27 ingest of both Ada notes with Ornith -> 636 s, 9 facts owed numbers
tests:
- type: unit
  description: Ingest of note 1 (ledger row 12, run 2026-10-23 with Ornith)
  input: the note "Ada Lovelace joined the Bergen Marine Institute on 1 March 2026 as a marine biologist. Her manager is Bo Eriksen, who leads the Kelp Project. Ada lives in Bergen and drinks green tea."
  expected: entities Ada Lovelace, Bergen Marine Institute, Bo Eriksen, Kelp Project, Bergen, green tea; four dated facts, "joined the Institute" valid from 2026-03-01; nothing invented
  tolerance: semantic
- type: unit
  description: Entity resolution by the model (ledger row 14)
  input: note 3 naming "Dr. Lovelace", "Bergen Institute" and "B. Eriksen" after notes 1 and 2
  expected: each resolved to the stored entity; no duplicate entity in the graph
  tolerance: semantic
- type: invariant
  description: agent_memory:INV-004 ingest makes no number
  input: ingest with the embedder slot refusing and numbers services attached
  expected: the step completes and records numbers owed for each fact; no vector is produced by memory
  tolerance: exact
- type: invariant
  description: agent_memory:INV-012 no write transaction during model calls
  input: ingest against a slow model server while another write runs
  expected: the other write commits during extraction; the entities and facts commit in one write after the model calls
  tolerance: exact
logging_tier: CORE

##### M_4_B_2 — Contradiction and Invalidation
job: Date each new fact while ending the stored facts it contradicts, using the language model over the candidate facts the stored setting names.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- new_facts: list of EntityEdge — the facts extracted from the episode, with resolved endpoints
- stored_facts: list of EntityEdge — the group's stored facts
- episode: EpisodicNode — the episode the facts came from
- candidate_mode: string — the graphiti.contradiction_candidates setting
outputs:
- resolved_facts: list of EntityEdge — each with valid_at and, when stated, invalid_at
- ended_facts: list of EntityEdge — stored facts given invalid_at and expired_at
- candidate_counts: list of integer — candidate facts shown to the model per new fact
artifacts: []
data_contracts:
- graphiti_graph_edge.json
seam_contracts:
- runs inside the ingest step (M_4_B_1), with the same client bundle and the same single write at the end
dependencies:
- M_4_B_1
invariants:
- agent_memory:INV-004
- agent_memory:INV-011
- agent_memory:INV-013
tunable_parameters:
  - param_name: graphiti.contradiction_candidates
    default_value: "same_subject"
    param_type: string
    description: Which stored facts the model is shown as possibly contradicted by a new fact; same_entity, same_subject or whole_group; a stored setting with no code default, required when the temporal tier is configured. The cost grows with the connected group, about one more candidate per new fact for each stored fact.
intelligence_pattern: none
logic: |
  1. The setting must be same_entity, same_subject or whole_group; anything else raises ValueError naming graphiti.contradiction_candidates when the part is built.
  2. For each new fact, skip a repeat of the same source, target and words (spacing and case ignored).
  3. Same-pair facts: the stored facts between the same two entities (this replaces Graphiti's search by numbers).
  4. Candidate facts: same_entity shows the stored facts touching either of the new fact's two entities; same_subject shows every stored fact connected to either entity through other stored facts, however many steps away; whole_group shows every stored fact of the group. Same-pair facts are not repeated among the candidates. The count shown is recorded.
  5. Graphiti's _dedupe_extracted_edge asks the model whether the new fact duplicates a same-pair fact, which candidates it contradicts, and its dates. A fact matched to a stored one keeps the stored uuid and is not new.
  6. Graphiti's _apply_temporal_invalidation ends each contradicted candidate: invalid_at is set from the new fact's valid_at, expired_at to now. Nothing is deleted: an ended fact stays in the graph with its four timestamps (created_at, valid_at, invalid_at, expired_at).
  7. The ended facts are stored in the ingest step's single write. From then on the temporal tier holds them back from answers asked after invalid_at (M_4_A_2), and the keyword search returns them with their dates.
  8. Not built: the candidate search by meaning (Graphiti's get_edge_invalidation_candidates over fact embeddings); it waits on a Graphiti embedder. What it must do when built: rank stored facts by each model's numbers for the new fact and show the nearest as candidates.
tools:
- graphiti_core
- openai
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_4_B_2
- candidate_mode: string, CORE — the setting in force
- candidates_shown: list of integer, CORE — per new fact
- facts_ended: integer, CORE — stored facts given invalid_at
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- The setting missing or not one of the three words -> ValueError naming it; the service does not build the part
- A candidates argument in the request -> ValueError "candidates is a setting now: graphiti.contradiction_candidates"
- same_entity misses a contradiction one step away (2026-10-24: ended only 'lives in Bergen', not 'joined the Institute')
- A wrong ending by the model -> stored as decided (2026-10-22 with qwen3_8b_cpu: 'Bo Eriksen leads the Kelp Project' was ended by Ada's job change; it did not recur on Ornith)
- The 4B model could not reach this step -> its extraction failed first (2026-10-23)
tests:
- type: unit
  description: same_subject on the Ada fixture (ledger row 13, rerun 2026-10-24 with Ornith)
  input: a store of 4 facts from note 1; note 2 (Ada moved to Oslo, left the Institute, now at the Ministry) ingested with same_subject
  expected: candidates 4, 3, 4; 'lives in Bergen' and 'joined the Bergen Marine Institute on 2026-03-01 as a marine biologist' ended with invalid_at 2026-10-20; 'Bo Eriksen leads the Kelp Project' kept current
  tolerance: semantic
- type: unit
  description: All four timestamps exercised
  input: the ended 'joined the Institute' fact after note 2
  expected: created_at set, valid_at 2026-03-01, invalid_at 2026-10-20, expired_at the day of ingest
  tolerance: exact
- type: invariant
  description: agent_memory:INV-013 an ended fact stays stored but leaves current answers
  input: a temporal search after 2026-10-20 for 'Where does Ada live?'
  expected: 'lives in Bergen' still in graphiti.graph_edge with its invalid_at, absent from the rankings and listed in not_current
  tolerance: exact
logging_tier: CORE

##### M_4_B_3 — Communities and Saga Summaries
job: Summarise a group's entity communities, each under a model-written name, or a saga of its episodes, with Graphiti's own summary prompts.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- communities_request: JSON object — group_id
- saga_summary_request: JSON object — group_id, saga_name, event_ids (optional list of episodes to link into the saga)
- model: string — the model name the model step router took from the language_model setting
outputs:
- communities_answer: JSON object — per community its name, summary and member names
- saga_answer: JSON object — saga name, summary and episodes_read
artifacts:
- communities_answer.json
- saga_answer.json
data_contracts:
- graphiti_graph_node.json
- graphiti_graph_edge.json
- communities_answer.json
- saga_answer.json
seam_contracts:
- model part protocol: steps communities and saga_summary of part graphiti
- the model operation refuses a group_id the caller may not see
dependencies:
- M_2_B_1
- M_4_A_1
- M_4_A_2
invariants:
- agent_memory:INV-001
- agent_memory:INV-005
- agent_memory:INV-012
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. group_id must be non-empty text, else ValueError "this step needs a group_id".
  2. communities: one read returns Graphiti's label-propagation clusters of the group's connected entities. Outside any transaction, Graphiti's build_community asks the model for each cluster's summary and name. One write stores each community and its HAS_MEMBER links. Return each community's name, summary and members.
  3. saga_summary: saga_name must be non-empty text and event_ids a list, else ValueError. One write finds the saga by name in the group or creates it, and links each named event's episode to it with a HAS_EPISODE edge whose uuid is derived from the saga and the episode; an event with no episode raises ValueError. The service makes the saga and its links because Graphiti makes them only inside add_episode, which needs numbers.
  4. Then Graphiti.summarize_saga runs unchanged on a DuckDB connection of its own to the same file, in the same process, with telemetry switched off by setting Graphiti's telemetry variable to false, and refusing embedder and reranker slots; it stores the summary on the saga. Return the saga's name, summary and how many episodes were read.
  5. Not built: Zep Cloud's observations (ledger row 21). The vendor's method is undisclosed ("clusters of facts and entities"); nothing is built and nothing was run.
tools:
- graphiti_core
- openai
- duckdb
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_4_B_3
- group_id: string, CORE — the group summarised
- communities_built: integer, CORE — communities stored
- episodes_read: integer, CORE — saga episodes summarised
- achieved_model: string, CORE — the model the server's reply named
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- A missing group_id, saga_name or a non-list event_ids -> ValueError, no model call
- An event with no episode yet -> ValueError, the saga write rolled back
- A community name that is a whole sentence -> stored as written; this is Graphiti's own prompt ("Create a short one sentence description of the summary"), not a fault of the step (2026-10-23 with Ornith)
- A one-entity community -> stored with an empty summary (2026-10-23, Ornith and the 4B)
- The 4B model's community name is filler that says nothing about the content (2026-10-23)
- Saga summary opens a second connection to the memory file in the same process -> one process still owns the file (agent_memory:INV-001 holds); a write on it is a write outside the owner's lifetime connection (read from the code)
- Zep Cloud observations (ledger row 21) -> opaque; not reproduced, nothing built, nothing run
tests:
- type: unit
  description: Communities on the Ada group (ledger row 14, run 2026-10-23, 188 s)
  input: the group after notes 1 and 2
  expected: one community of 7 entities whose summary states the Kelp Project lead, Ada's job, her move to Oslo and the Ministry; a second community "green tea" with an empty summary
  tolerance: semantic
- type: unit
  description: Saga summary (ledger row 16, run 2026-10-23, 55 s)
  input: saga_summary with saga_name ada-story and the two Ada events
  expected: a grounded summary of both episodes, nothing invented; episodes_read 2
  tolerance: semantic
- type: invariant
  description: agent_memory:INV-012 the model is asked outside any write
  input: communities against a slow model server while another write runs
  expected: the other write commits during the model call; communities commit in one write after it
  tolerance: exact
logging_tier: CORE

#### M_4_C — Graphiti Tools
purpose: Offer Graphiti's MCP server, with its 13 memory tools, over a DuckDB graph file, with every tool that needs a model refusing until an owner supplies one.
inputs:
  - a YAML settings file (transport, host, port, concurrency, database path, default group, user, custom entity and edge types)
  - MCP tool calls from an MCP client
outputs:
  - MCP tool answers, or refusals naming the missing model
dependencies:
  - M_4_A_1
tests:
  - get_status on a fresh DuckDB file -> connected
  - search_nodes with no embedder supplied -> refused naming the embedder
  - summarize_saga on an unknown saga -> the original's refusal

##### M_4_C_1 — MCP Server (13 memory tools)
job: Answer Graphiti's 13 MCP memory tools over one DuckDB graph file, refusing with a reason every tool whose model the owner has not supplied.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- settings_file: YAML file — server (transport http, stdio or sse; host; port; semaphore_limit), database (path), graphiti (group_id, user_id, entity_types, edge_types, edge_type_map); values may name environment variables
- command_line_overrides: arguments — transport, host, port, semaphore limit, database path, group id, user id, destroy-graph
- tool_call: MCP request — one of add_memory, search_nodes, search_memory_facts, delete_entity_edge, delete_episode, get_entity_edge, get_episodes, summarize_saga, build_communities, add_triplet, get_episode_entities, clear_graph, get_status
outputs:
- tool_answer: MCP response — the original's answer shape, or an error naming what was not supplied
artifacts:
- mcp_server_settings.yaml
data_contracts:
- mcp_server_settings.yaml
- graphiti_graph_node.json
- graphiti_graph_edge.json
seam_contracts:
- GraphitiService(config, llm_client, embedder, cross_encoder): an owner that has models builds the service itself and passes them; each one not passed is a stand-in that refuses with a reason
- QueueService runs each group's add_memory jobs one at a time
dependencies:
- M_4_A_1
invariants:
- agent_memory:INV-001
- agent_memory:INV-004
- agent_memory:INV-011
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Read one required YAML settings file, expanding environment variables, and apply command-line overrides. Transport, host, port, semaphore_limit, database path and default group have no defaults; a missing one is refused by the settings model.
  2. GraphitiService.initialize switches Graphiti's telemetry off, builds the custom entity and edge types and the edge type map from the settings, opens DuckDBDriver on the settings' database path, builds the Graphiti client with the supplied models or refusing stand-ins, and builds Graphiti's indexes.
  3. Thirteen tools are registered. get_status, get_episodes, get_entity_edge, get_episode_entities, delete_entity_edge, delete_episode and clear_graph need no model. add_memory validates and queues an episode per group, processed one at a time. search_nodes, search_memory_facts, add_triplet, build_communities, summarize_saga on a real saga and add_memory's background extraction need a model or an embedder and refuse with "no embedder/language model has been supplied" when it is a stand-in.
  4. A tool acts on the groups named, else on the configured default group.
  5. destroy-graph clears every Graphiti graph before serving.
  6. The command-line start supplies no models, so every model tool refuses when started that way.
  7. Not wired: the memory service never starts this server, and no settings file exists in the service's stored settings. What it must do when wired: run inside the memory service on the owner's lifetime connection, with the models and numbers the service routes, its settings as rows of the memory database, so it never opens the memory file a second time.
tools:
- mcp
- graphiti_core
- pydantic
- pyyaml
- asyncio
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_4_C_1
- tool_name: string, CORE — the MCP tool called
- group_ids: list of string, CORE — the groups acted on
- refused_reason: string, CORE — the missing model, when refused
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- A model tool with no model supplied -> refused with "no embedder/language model has been supplied" (measured 2026-10-20)
- A settings value missing -> refused by the settings model before serving
- Pointed at the memory database while the memory service runs -> the file is held by the service and cannot be opened (agent_memory:INV-001)
- Its settings live in a YAML file, not in the memory database -> outside agent_memory:INV-011 until wired
- build_communities through this tool -> its save embeds the community name, so it waits on numbers even though the service's communities step runs (ledger row 16)
- The server as a whole -> built, run only through an in-process MCP client on a throwaway file; ledger row 16 stays unproven
tests:
- type: unit
  description: The 13 tools are listed and the no-model tools answer (ledger row 16, run 2026-10-20)
  input: an in-process MCP client on a throwaway DuckDB file; list tools, then call get_status, get_episodes, delete_episode and clear_graph
  expected: 13 tools listed; each no-model tool gives the original's answer, including after a restart
  tolerance: exact
- type: unit
  description: A model tool refuses
  input: search_nodes with the command-line start (no models)
  expected: an error naming that no embedder has been supplied; nothing written
  tolerance: exact
- type: invariant
  description: agent_memory:INV-004 the server makes no numbers itself
  input: add_triplet with no embedder supplied
  expected: refused; no vector stored
  tolerance: exact
logging_tier: CORE

#### M_4_D — Zep
purpose: Keep Zep Community Edition's users, sessions and messages in schema zep_ce of the memory database, answer a session's relevant facts from the stored Graphiti graph by words, and keep thread and user summaries made with the configured language model.
inputs:
  - model operation requests for part zep: seed_session, relevant_facts, set_instructions, thread_summary, get_summary, user_summary; each needs project_uuid
outputs:
  - rows in schema zep_ce
  - messages with relevant facts; summaries with their summary points
dependencies:
  - M_2_B_1
  - M_4_A_1
tests:
  - An ended session asked to take a message -> refused
  - relevant_facts with a graph group holding facts -> the last messages and matching valid facts
  - thread_summary on 14 messages with window 12 -> older messages folded in, 6 kept
  - user_summary after a new preference -> the newer statement preferred

##### M_4_D_1 — Zep CE Store (users, sessions, messages, relevant facts)
job: Keep one project's Zep conversations, answering a session's memory with the stored graph's valid facts that match its last messages.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- project_uuid: UUID — the Zep project every row belongs to; zero is refused
- seed_session_request: JSON object — user_id, session_id, messages (role and content), first_name and last_name optional
- relevant_facts_request: JSON object — session_id, graph_group_id, last_n (optional, 0 when absent)
- store_calls: Zep CE operations — users, sessions, messages, metadata, listings, put_memory, get_memory, search_sessions, purge
outputs:
- seed_answer: JSON object — messages_stored
- relevant_facts_answer: JSON object — messages (role, content), relevant_facts (fact, uuid, valid_from), facts_unavailable
artifacts:
- zep_ce_users.json
- zep_ce_sessions.json
- zep_ce_messages.json
- zep_ce_graph_owed.json
data_contracts:
- zep_ce_users.json
- zep_ce_sessions.json
- zep_ce_messages.json
- zep_ce_graph_owed.json
seam_contracts:
- ZepGraph (add_node, delete_group, put_memory, get_memory, search): with none attached, node, group and hand-off steps are written to zep_ce.graph_owed and facts come back empty with the reason
- KeywordGraph is the service's ZepGraph: get_memory and search use the keyword graph search (M_4_A_3) on the caller-named group and keep only facts still valid; every write is refused
dependencies:
- M_2_B_1
- M_4_A_3
invariants:
- agent_memory:INV-004
- agent_memory:INV-006
- agent_memory:INV-010
- agent_memory:INV-011
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Every zep step first parses project_uuid (an invalid UUID raises ValueError) and ensures schema zep_ce: tables users, sessions, messages, graph_owed, summaries, user_summary_instructions, user_summaries, with their sequences.
  2. Users: an empty or used user_id is refused; create queues the user's graph node. Sessions: an empty or used session_id is refused; a session may name an existing user. Messages: an unknown role type is refused (norole, system, assistant, user, function, tool); a duplicate message uuid is refused; a missing session says "session does not exist" (the original said "message_uuid already exists").
  3. Metadata merges key by key; only a privileged caller may set the "system" key; a non-privileged caller's "system" key is dropped. An ended session refuses updates and new messages. Deleting is soft, and purge removes soft-deleted rows; deleting a user removes its sessions and messages and queues deleting its graph groups.
  4. Listings by cursor and by ordered pages (an order column outside a fixed list is refused, where the original passed the text to the database); a page past the end answers total 0 as the original does; last-N with and without a before-uuid; by-uuid in storage order.
  5. put_memory creates a missing session. get_memory reads at least the last 4 messages, returns the last last_n as history (none when last_n is 0), and asks the graph for up to 5 facts relevant to the last 4 messages, for the group named by the session's user (or the session). With no graph attached, facts are empty and facts_unavailable says "no graph service is attached, so no facts can be retrieved".
  6. seed_session (no model): user_id and session_id required; messages must be a list of objects with text role and content, else ValueError and nothing stored. In one write, create the user when missing, create the session and store the messages.
  7. relevant_facts (no model): session_id and graph_group_id required; last_n a whole number of at least 0. In one read, get_memory with KeywordGraph on graph_group_id: facts matching the messages' words by BM25, still valid, best first. Return messages, facts with their valid-from time, and facts_unavailable.
  8. Not built: facts chosen by meaning (they wait on numbers), and relevant facts written through Zep (the graph is written only by the Graphiti model steps).
tools:
- duckdb
- uuid
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_4_D_1
- project_uuid: string, CORE — the Zep project
- session_id: string, CORE — the session read or written
- facts_returned: integer, CORE — relevant facts returned
- facts_unavailable: string, CORE — the reason none were returned
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- An empty or duplicate user or session id, an unknown role type, a duplicate message uuid -> BadRequestError, nothing written
- An unknown user, session or message -> NotFoundError
- An ended session given an update or messages -> SessionEndedError, nothing written
- A write through KeywordGraph -> ReadOnlyGraphError "the graph is written by the Graphiti model steps"
- A stale fact still current in the graph -> returned as relevant (2026-10-23: "Ada Lovelace lives in Bergen." came back); content is only as right as the contradiction step
- The model operation checks graph_group_id against the caller's groups but not user_id, project_uuid or session_id -> any authenticated caller can read or seed any Zep session by id (read from the code, not run)
- The limits of 4 messages read, 4 messages for fact retrieval and 5 facts -> written in code as Zep CE's constants, not stored settings (a Law 32 gap, stated)
- ZepCeStore.open connects with duckdb.connect directly, skipping the SQLite refusal of connect_native; the service path never uses open (read from the code)
tests:
- type: unit
  description: The Zep CE store's refusals (ledger row 17, 43 checks by prove_zep_ce.py)
  input: an empty user id, a duplicate session id, an unknown role type, a message for an ended session
  expected: each raises the matching Zep error and writes nothing
  tolerance: exact
- type: unit
  description: Relevant facts through the service (run 2026-10-23, 0.17 s)
  input: relevant_facts for session s1 with the Ada group and last_n 4
  expected: the last four messages and five valid facts, among them "Ada Lovelace works at the Ministry of Fisheries" valid from 2026-10-20
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 deleting a session hides before it purges
  input: delete_session then a read before purge
  expected: the session and its messages hidden from every read; rows hard-deleted only by purge
  tolerance: exact
logging_tier: CORE

##### M_4_D_2 — Thread and User Summaries
job: Keep a running summary of each session plus a summary of each user, written with the configured language model under Zep's summary rules.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- set_instructions_request: JSON object — project_uuid, user_id, instructions (list of name and text)
- thread_summary_request: JSON object — project_uuid, session_id
- get_summary_request: JSON object — project_uuid, session_id
- user_summary_request: JSON object — project_uuid, user_id
- model: string — the model name the model step router took from the language_model setting
outputs:
- instructions_answer: JSON object — the stored instructions in order
- thread_summary_answer: JSON object — outcome not_due with counts, or summarised with summary, summary_uuid, summary_point_uuid, summary_tokens_estimated, messages_folded_in, messages_kept, model_calls, model_seconds, builds_on_previous_summary
- get_summary_answer: JSON object — summary, summary_point_uuid, summary_tokens, messages_after_summary_point
- user_summary_answer: JSON object — outcome nothing_new, or summarised with summary, instructions_used, messages_folded_in, facts_used, builds_on_previous_summary, model_seconds
artifacts:
- zep_ce_summaries.json
- zep_ce_user_summary_instructions.json
- zep_ce_user_summaries.json
data_contracts:
- zep_ce_summaries.json
- zep_ce_user_summary_instructions.json
- zep_ce_user_summaries.json
seam_contracts:
- model part protocol: name zep, steps seed_session, set_instructions, thread_summary, get_summary, user_summary, relevant_facts
- the summariser's complete(prompt, max_tokens) asks the routed model once through the recorded door
dependencies:
- M_2_B_1
- M_4_A_1
- M_4_D_1
invariants:
- agent_memory:INV-005
- agent_memory:INV-011
- agent_memory:INV-012
tunable_parameters:
  - param_name: zep.message_window
    default_value: "12"
    param_type: int
    description: Unsummarised messages that make a thread summary due; the newest half stay unsummarised; a stored setting with no code default.
  - param_name: zep.summary_max_output_tokens
    default_value: "1024"
    param_type: int
    description: Most tokens one summary answer may use; a stored setting with no code default.
  - param_name: zep.model_context_tokens
    default_value: "8192"
    param_type: int
    description: The summary model's context size in tokens, which bounds each batch's input; a stored setting with no code default.
  - param_name: zep.prompt_headroom_tokens
    default_value: "250"
    param_type: int
    description: Tokens kept free for the prompt wrapper when sizing a batch; a stored setting with no code default.
  - param_name: zep.characters_per_token
    default_value: "4"
    param_type: int
    description: Characters counted as one token, standing in for Zep's token counter (any number above 0); a stored setting with no code default.
intelligence_pattern: event_driven
logic: |
  1. Building the part: characters_per_token must be a number above 0, and message_window, summary_max_output_tokens, model_context_tokens and prompt_headroom_tokens whole numbers of at least 1, with model_context_tokens greater than the output tokens plus the headroom; else ValueError naming the setting. zep.summarizer_prompt is optional and must hold both {{.PrevSummary}} and {{.MessagesJoined}}; absent means Zep's own prompt.
  2. set_instructions (no model): instructions must be a list of objects with text name and text, else ValueError. More than 5 instructions, a blank name or text, a name or a text over 100 characters, or a repeated name breaks Zep's published limits and is refused with BadRequestError; an unknown user raises NotFoundError; otherwise, in one write, the user's instructions are replaced in the order given and read back.
  3. thread_summary: one read of the newest summary and the message_window newest unsummarised messages after its summary point; empty messages are dropped. Fewer than message_window answers not_due with the counts. Otherwise, outside any transaction, keep the newest message_window halved (rounded down) and fold the older ones into the previous summary in batches that each fit model_context_tokens minus summary_max_output_tokens minus prompt_headroom_tokens (tokens counted as characters divided by characters_per_token, rounded up), each batch one model call that starts from the previous batch's result (the original passed the first summary to every batch and lost earlier batches; the port chains them). An empty answer raises ValueError "no summary found after summarization". One write stores the summary with its summary point (the last folded message) and token estimate.
  4. get_summary (no model): the newest summary and the count of messages after its summary point.
  5. user_summary: one read of the user, its instructions, its stored summary with the last folded message id, the user's live messages after that id across its live sessions, and the group's valid facts. Nothing new answers nothing_new; with a stored summary and no new message it also answers nothing_new. Otherwise build Graphiti's entity-summary prompt (which mirrors Zep's own summary worker and says "When newer episode text conflicts with older summary content, prefer the newer explicit fact") with the previous summary, the new lines and the facts, and add the instructions as one marked block, since Zep publishes no prompt for them. Ask the model once, outside any transaction; no summary for the user raises ValueError. Cut to Graphiti's summary length at a sentence end; one write stores it with the last message folded in.
  6. Not built: the embedding of summaries (it waits on numbers, and a summary belongs to no captured event, so nothing owes its numbers), the NER pass on summaries, and Zep's Anthropic prompt variant.
tools:
- graphiti_core
- openai
- httpx
- duckdb
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_4_D_2
- session_id: string, CORE — the session summarised
- user_id: string, CORE — the user summarised
- outcome: string, CORE — not_due, nothing_new or summarised
- messages_folded_in: integer, CORE — messages folded into the summary
- model_calls: integer, CORE — model calls made
- achieved_model: string, CORE — the model the server's reply named
- model_seconds: float, TRAINING — time the model took
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Instructions over Zep's limits -> refused, nothing stored
- An empty summary from the model -> ValueError "no summary found after summarization", nothing stored
- No user summary for the user in the model's answer -> ValueError naming the user, nothing stored
- A missing or invalid zep setting -> ValueError naming it when the part is built
- user_summary reads graph facts with the raw user_id as the Graphiti group id, while the service's groups are "actor-" plus a SHA-256 -> unless a group is named after the user id, no facts are found and the summary is made from messages alone (read from the code, not measured)
- The limits 5 instructions and 100 characters, and the half window kept -> written in code (Zep's published values and Zep's rule), not stored settings (a Law 32 gap, stated)
- An update that should retire an old preference (2026-10-23) -> both Ornith and the 4B kept "dislikes coffee"; fixed 2026-10-24 by Graphiti's entity-summary prompt: the update read "She previously drank green tea every morning and never drank coffee, but she has started drinking coffee." (30 s)
- Zep Cloud's closed remainder, its Go worker for user summaries (growth row of 2026-10-24) -> REA territory, not translated; only its prompt is public, mirrored in Graphiti and used here
tests:
- type: unit
  description: Thread summary of 14 messages (ledger row 20, run 2026-10-23 with Ornith, 20 s)
  input: a session of 14 messages with message_window 12
  expected: outcome summarised, the older messages folded in, 6 messages after the summary point; the summary names the kelp study, the manager and the tea preference
  tolerance: semantic
- type: unit
  description: Not due below the window
  input: thread_summary on a session of 11 messages with message_window 12
  expected: outcome not_due with unsummarised_messages 11; no model call
  tolerance: exact
- type: unit
  description: User summary update prefers the newer statement (rerun 2026-10-24)
  input: a first summary saying Ada never drinks coffee, then the message 'I have started drinking coffee.'
  expected: the updated summary builds on the previous one and says she has started drinking coffee, inventing nothing
  tolerance: semantic
- type: invariant
  description: agent_memory:INV-011 no summariser number has a code default
  input: build the zep part with message_window removed from the settings
  expected: ValueError naming message_window; the part is not built
  tolerance: exact
logging_tier: CORE

### M_5 — Relational Memory (Cognee)
description: Keeps the relational tier as Cognee chunk nodes in the memory database, one per piece the supplier cut, ranks them once per Embedding model from received numbers, and offers Cognee's own language-model steps (graph extraction, entity description rewrite, dated events, question time windows) through the model router. Cognee runs on DuckDB only, through a fork of Cognee 1.6.5 (Apache-2.0) with added DuckDB adapters and the event-based temporal pipeline restored from upstream commit 1513b1f41^. Cognee's cognify, its 21 search types, loaders, importers, servers and evaluation framework are in the vendored source but are not reached by the memory service.
dependencies:
  - M_0
  - M_1
  - M_2
entry_conditions:
  - M_0, M_1 and M_2 are VERIFIED
  - the setting tiers.relational.database_name is stored in the memory database
  - the setting cognee.data_directory is stored when the cognee model part is wanted
exit_conditions:
  - all submodules VERIFIED
  - all artifacts schema-valid
  - all tests passed
  - end-to-end module test passed
module_test: Capture a relational event, let the supplier hand over its pieces and every registered model's numbers, and confirm the relational receipt says processed, one DocumentChunk node per piece exists in the cognee_chunks schema, and a retrieve answers with one ranking per fresh model and every stale model listed as left out with its reason; then run the cognee part's query_window step on "What happened in October 2026?" and confirm the window 2026-10-01 to 2026-11-01 with the model that answered named.

#### M_5_A — Cognee on DuckDB
purpose: Give Cognee a DuckDB home for its graph, vectors, relational records and session cache, so that every Cognee store lives in DuckDB and none in SQLite, Kuzu, LanceDB or Neo4j.
inputs:
  - native DuckDB handle: the owner's borrowed transaction handle, or a file under cognee.data_directory
  - Cognee data points, graph nodes, graph edges, relational records
outputs:
  - DuckDB tables in a named schema: graph_node, graph_edge, graph_metadata, one table per vector collection, Cognee's relational and cache tables
dependencies:
  - M_0_D_1
tests:
  - a node added on a borrowed handle whose transaction is rolled back -> the node is absent afterwards
  - a Cypher query sent to the adapter -> refused with "Cypher queries not supported in DuckDB."
  - two datasets created with backend access control on -> each has its own schema and a node in one is absent from the other
  - the sqlite relational provider requested -> refused with a message naming duckdb

##### M_5_A_1 — Graph and Vector Adapters
job: Keep every Cognee graph record or vector record as rows of DuckDB tables in one named schema, on a file it owns or on a handle it borrows.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- connection: native DuckDB handle, optional — the owner's handle to borrow; when absent the adapter opens its own file or an in-memory database
- database_name: string — the schema every table is created in (the relational tier passes the setting tiers.relational.database_name)
- embedding_engine: object, optional — Cognee's embedder slot; on the memory path it is ReceivedNumbersEmbeddingEngine, which answers only from received numbers
- nodes: list of (id, properties) pairs or Cognee data points — graph nodes to add or replace
- node_ids: list of string — graph nodes to read or delete
- points: list of {id, vector, payload} — vectors computed elsewhere, for upsert_raw_vectors
outputs:
- graph rows: rows in <schema>.graph_node (id, name, type, properties JSON, four provenance lists) and <schema>.graph_edge (source_id, target_id, relationship_name, properties JSON, four provenance lists)
- vector rows: one table per collection (id, text, vector FLOAT[n], payload JSON, created_at)
- search results: list of scored results ordered by ascending cosine distance
artifacts:
- cognee_chunk_node.json
data_contracts:
- cognee_chunk_node.json
seam_contracts:
- M_5_B_1 -> M_5_A_1: the relational tier builds one adapter per call on the owner's handle and calls initialize, add_nodes, delete_nodes and get_node; the adapter never begins, commits, rolls back or closes that handle
- M_5_B_2 -> M_5_A_1: Cognee's own get_graph_engine returns this adapter (registered for the provider name duckdb) on a file under cognee.data_directory
dependencies:
- M_0_D_1
invariants:
- agent_memory:INV-001
- agent_memory:INV-003
- agent_memory:INV-004
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. On construction with a connection: refuse a file location given beside it (ValueError), refuse a handle that is not native DuckDB (TypeError), check the handle is native DuckDB storage, and issue nothing else; every later statement runs inside the caller's transaction against tables qualified with the schema name.
  2. On construction without a connection: refuse a remote or md: location, open the file (or a private in-memory database) natively, create the schema and select it; every later write batch runs in a transaction the adapter begins, commits, or rolls back on failure.
  3. initialize creates the schema, graph_node, graph_edge and graph_metadata tables if they are absent, in one batch.
  4. add_nodes reads each node's existing provenance and properties, merges the new properties over the old, and writes every node with insert-or-update in one batch; delete_nodes deletes the nodes' incident edges first, then the nodes, in one batch.
  5. Graph reads answer by plain SQL: get_node, get_nodes, edges, neighbours, a breadth-first neighbourhood to a given depth with optional edge types, nodeset subgraphs, connections, top-degree nodes, triplets in pages, counts and metrics (node count, edge count, mean degree, density, connected components; diameter, average shortest path and clustering are answered -1).
  6. Provenance: attach, remove and find source references for nodes and edges by dataset, by pipeline run or by source reference key.
  7. Vectors: create_collection makes a table of the given width (or the embedder's width; neither refuses); create_data_points embeds through the embedder slot; upsert_raw_vectors stores vectors computed elsewhere, refusing a missing id, an empty vector or mixed widths before writing; search ranks by exact cosine distance with an optional belongs_to_set filter (any or all); batch_search, retrieve, delete_data_points, score_by_ids, update_payload and prune work on the same tables. No vector index is used: every search is an exact scan.
  8. Datasets: create_dataset gives each dataset its own schema ds_<uuid hex> in the configured DuckDB file; delete_dataset drops that schema only for the dataset's owner, only when the stored record names this file and this schema, in one transaction.
  9. query (Cypher) is refused: DuckDB holds no Cypher engine.
  10. On the memory path today only steps 1, 3, 4 and get_node are reached by the relational tier, and Cognee's own graph engine reaches the rest of the graph calls during the cognee model steps; the vector calls are built but not wired, because the relational tier ranks from the owner's per-model numbers tables, not from Cognee's collections.
tools:
- duckdb
- duckdb_engine
- cognee
- pydantic
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_5_A_1
- database_name: string, CORE — the schema the adapter worked in
- owns_connection: boolean, DEBUG — whether the adapter opened its own handle or borrowed the owner's
- rows_written: integer, DEBUG — nodes or edges written in one batch
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- A Cypher query is sent -> NotImplementedError "Cypher queries not supported in DuckDB."; any Cognee feature that needs Cypher cannot run on this adapter
- Graph metrics asked for diameter, average shortest path or clustering -> -1 is returned for each; they are not computed
- add_nodes or add_edges on an adapter that owns its file -> the read of existing provenance runs before the write batch's own transaction begins, so two writers can interleave; on the owner's borrowed handle both sit inside the owner's one transaction
- retrieve meets a database error -> the error is logged and an empty list is returned (an error turned into an empty answer); has_collection answers false on a database error; neither is reached by the memory path today
- search called without a limit uses 15, a number written in code (agent_memory:INV-011 broken in the vendored adapter, not reached by the memory path)
- Many graph methods carry a docstring that says only "Documentation."; the code works, the description is missing
- Borrowed handle that is not native DuckDB -> TypeError before anything is read
- create_collection with no width and no embedder -> ValueError, nothing created
tests:
- type: unit
  description: Nodes added on a borrowed handle commit with the caller
  input: a native DuckDB handle in an open transaction, two (id, properties) nodes added, then the caller commits
  expected: both nodes are readable with get_node and the adapter issued no BEGIN, COMMIT or ROLLBACK of its own
  tolerance: exact
- type: unit
  description: Cypher is refused
  input: query("MATCH (n) RETURN n", {})
  expected: NotImplementedError with the words "Cypher queries not supported in DuckDB."
  tolerance: exact
- type: invariant
  description: agent_memory:INV-003 a projection change commits with its receipt on the same connection
  input: the owner's transaction adds chunk nodes through a borrowed adapter, then rolls back
  expected: no chunk node remains and the receipt is unchanged
  tolerance: exact
- type: invariant
  description: agent_memory:INV-004 raw vectors are never computed here
  input: upsert_raw_vectors with an embedder slot that raises on any call
  expected: the vectors are stored and the embedder is never called
  tolerance: exact
logging_tier: DEBUG

##### M_5_A_2 — Relational, Cache and Permissions
job: Hold Cognee's relational records (users, roles, tenants, datasets, permissions, session cache, database ingestion) in DuckDB, with each dataset isolated in its own schema of one file.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- connection_string: string — a DuckDB SQLAlchemy address naming a local file; any other database engine is refused
- dataset_id: UUID — the dataset whose schema is created or dropped
- user: Cognee user record — the caller whose permissions are checked
- dlt_source: a dlt resource or a SQLite file — rows for Cognee's database ingestion
outputs:
- relational tables: Cognee's users, roles, tenants, datasets, permissions and pipeline tables, created by Cognee's own migrations made DuckDB-safe
- cache tables: cache_qa_entries, cache_trace_entries, cache_session_context, cache_usage_logs and a key-value table
- dataset schema: ds_<uuid hex> per dataset in the one configured DuckDB file
- ingested rows: dlt rows staged in DuckDB and read back by ingest_dlt_source
artifacts: []
data_contracts: []
seam_contracts:
- M_5_B_2 -> M_5_A_2: CogneeModel selects the duckdb relational provider for Cognee's stores under cognee.data_directory
dependencies:
- M_0_D_1
- M_5_A_1
invariants:
- agent_memory:INV-001
- agent_memory:INV-006
tunable_parameters:
- param_name: cognee.data_directory
  default_value: "absolute folder beside the memory database file"
  param_type: string
  description: The absolute folder Cognee keeps its own system and data stores under; a stored setting with no code default; Cognee's relational, graph and vector providers are all set to duckdb there.
intelligence_pattern: none
logic: |
  1. DuckDBRelationalAdapter refuses an address with no database path; create_database makes the file's folder and runs Cognee's migration chain; delete_database resets only Cognee's own tables and keeps the shared file.
  2. reset_relational_tables checks the migration revision, that no user view and no outside foreign key depends on Cognee's tables, and that every table has the exact expected shape; any mismatch refuses the reset; otherwise it drops only Cognee's tables.
  3. DuckDBCacheAdapter refuses an address that is not DuckDB and otherwise runs Cognee's own SQL cache operations (question and answer entries, traces, session context, usage logs, time-to-live, key-value) with DuckDB-safe deletes and upserts.
  4. The dataset handler derives the file from Cognee's relational setting and the schema ds_<uuid hex> from the dataset id; it refuses an in-memory or non-DuckDB relational setting; create makes only that schema; delete drops only that schema after checking the record matches and evicts the cached graph and vector engines for it.
  5. Permissions: Cognee's own access-control layer runs unchanged on these tables; an owner may read, write, delete and share; another user is refused until granted; a role grant is honoured; each dataset's graph lives in its own schema.
  6. Database ingestion: dlt loads rows into a DuckDB destination (SQLite files are read through DuckDB's sqlite extension, read-only) and ingest_dlt_source reads them back.
  7. Upstream changes in the fork: the default relational, cache, vector and graph provider is duckdb; the sqlite and turso providers are refused with a message naming duckdb; migrations no longer use partial unique indexes or cascading foreign keys for any database; startup treats duckdb like sqlite on first run; the BROAD record store keeps its records in DuckDB with external access off; text-to-SQL reads SQLite through DuckDB read-only; Kuzu is imported only when configured.
  8. Wired today: only through step 7's provider choice when a cognee model step opens Cognee's stores under cognee.data_directory. Built, proven by script, not wired: users, roles, tenants, dataset permissions and per-dataset isolation are never called by the owner, which decides visibility itself (M_1_A_2 caller identity, the owner's visibility policy).
tools:
- duckdb
- sqlalchemy
- alembic
- dlt
- cognee
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_5_A_2
- dataset_schema: string, DEBUG — the ds_<uuid hex> schema created or dropped
- reset_refused_reason: string, CORE — why a relational reset was refused, when it was
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- A sqlite or turso relational or cache provider is configured -> refused with a plain message naming duckdb
- A relational reset finds an unexpected table shape, a user view or an outside foreign key -> the reset is refused and nothing is dropped
- Another user deletes a dataset's storage -> PermissionError, nothing dropped
- The dataset record names another file or schema -> ValueError, nothing dropped
- Ladybug, Kuzu or LanceDB providers are selected -> they cannot load, because the cognee_db_workers package is absent
- The owner never calls Cognee's permissions -> Cognee's access control protects nothing on the memory path; the owner's own visibility does
tests:
- type: unit
  description: Permission refused until granted
  input: two users and one dataset owned by the first; the second asks to read, then the owner grants read, then the second asks to write
  expected: read refused, then read allowed, write refused
  tolerance: exact
- type: unit
  description: Database ingestion through dlt on DuckDB
  input: a dlt resource of three rows loaded into a DuckDB staging file
  expected: ingest_dlt_source reads back three rows (proofset.people, ids 1 to 3) and no SQLite file is created
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 a reset never deletes what is not Cognee's
  input: a DuckDB file holding Cognee's tables plus a user view over one of them, then delete_database
  expected: the reset is refused and every table and the view remain
  tolerance: exact
logging_tier: DEBUG

#### M_5_B — Cognee Tier and Model Steps
purpose: Make Cognee the owner's relational tier and offer Cognee's language-model steps through the model router.
inputs:
  - captured relational events and their removal events
  - the pieces and per-model numbers the supplier handed over
  - socket operation model with part cognee
outputs:
  - receipts processed or processed_no_new_fact, or a retryable failure "numbers not yet supplied"
  - per-model rankings of pieces with the models left out and their reasons
  - plain JSON step outputs with the model that answered
dependencies:
  - M_1_B_2
  - M_2_A_1
  - M_2_B_1
  - M_5_A_1
tests:
  - a relational event with no pieces yet -> retryable failure naming "numbers not yet supplied" and a pieces request recorded
  - a forget of a stored event -> its chunk nodes are deleted and the receipt says processed
  - query_window on "Who is Ada?" -> no window, reason no_time_constraint
  - rewrite_entity_description for a name not in the graph -> ValueError naming the entity

##### M_5_B_1 — Cognee Chunk Tier
job: Serve as the owner's relational tier, in which every supplied piece of an event is one Cognee chunk node ranked once per Embedding model from that model's received numbers.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- event_envelope: JSON object — a captured relational event, or an owner-authored system.expire or system.forget event
- verified_payload_bytes: bytes or null — the event's verified payload
- pieces: list of {piece_id, event_id, text, position, text_fingerprint} — the pieces the supplier cut, read from memory_pieces through the owner's reader
- scope: RetrievalScope — the asker's identity and visibility (own, granted or pool)
- query: string — the search question
- limit: integer — the most items per ranking
outputs:
- processor_result: {status, processor_id "cognee-duckdb-chunks", processor_version (the installed cognee version), projection_version "cognee-piece-chunk-1"}
- tier_rankings: {rankings: {model label: [items]}, left_out: {model label: reason}}
artifacts:
- cognee_chunk_node.json
- tier_rankings.json
- processor_result.json
data_contracts:
- supplied_piece.json
- cognee_chunk_node.json
- tier_rankings.json
- processor_result.json
seam_contracts:
- M_2_A_2 -> M_5_B_1: the owed-work processor calls prepare outside any write transaction, then apply on its open transaction, then records the receipt on the same connection
- M_5_B_1 -> M_7 numbers services: prepare asks for the event's pieces (request_pieces) when none exist; prepare_query asks question_vectors("event", query) for each fresh model; memory never embeds the question
- M_5_B_1 -> M_8_A_2: retrieve returns per-model rankings whose items each name their event, so the owner's live, not removed, not expired and visible checks still decide what is shown
- M_2_A_3 -> M_5_B_1: a rebuild of the relational tier calls reset_projection with a bound on items
dependencies:
- M_1_B_2
- M_1_C_1
- M_2_A_1
- M_5_A_1
invariants:
- agent_memory:INV-003
- agent_memory:INV-004
- agent_memory:INV-006
- agent_memory:INV-007
- agent_memory:INV-010
tunable_parameters:
- param_name: tiers.relational.database_name
  default_value: "cognee_chunks"
  param_type: string
  description: The DuckDB schema in the memory database where the relational tier's Cognee chunk nodes live; a stored setting with no code default; an optional tiers.relational.embedding_model (model_id, revision) binds Cognee's embedder slot to one registered model.
intelligence_pattern: event_driven
logic: |
  1. prepare, for a removal event: read the target event's live pieces and retired piece ids through the owner's reader and name for deletion every chunk id derived from them plus the chunk id the earlier one-event-one-chunk tier would have used.
  2. prepare, for any other event: read the event's live pieces and retired piece ids. If there are no pieces, record that pieces are owed (request_pieces with "numbers not yet supplied: no pieces yet") and raise NumbersNotYetSupplied; the owner records a retryable failure and retries on a later maintenance round.
  3. With pieces: build one chunk per piece, id uuid5 of the piece id (so a replay overwrites the same chunk), with name, type DocumentChunk, text, belongs_to_set (the producer's group), event id, piece id, position, text fingerprint, producer, event time, event type and the provenance fields (schema_version, source_revision, source_artifact_id, work_id, payload_sha256, exactness); name the retired pieces' chunks for deletion; if tiers.relational.embedding_model is set, bind a received-numbers embedder for that model. No text is cut here: Cognee's cutter slot is NoCutChunker, which refuses to read.
  4. apply, on the owner's open transaction: initialize the Cognee tables; for a removal, delete the named chunk nodes and their edges and answer processed if any existed, processed_no_new_fact otherwise; for an event, delete retired chunks, add or replace every chunk node, answer processed.
  5. prepare_query: ask the numbers services for the question's numbers once per model that is fresh for event pieces; a stale model, or one whose question numbers the supplier cannot give, is put in left_out with its reason.
  6. retrieve: for visibility own, limit to the asker's own events; for granted, to the granted producers, and an empty grant answers no rankings with the left-out models; for pool, no producer limit; any other mode raises ValueError.
  7. For each model with question numbers: in SQL, rank that model's non-stale event pieces by cosine distance to the question, leaving out removed events and retired pieces, ordered by distance then piece id, cut at limit; each item carries event id, piece id, chunk id, position, text, producer, source revision, distance and the words "cosine distance of <model>'s own numbers; no entity or relationship extraction". Models are never ranked across each other here.
  8. reset_projection (a rebuild): count the legacy DocumentChunk_text collection and the chunk nodes with an event id; more than the bound refuses; a chunk whose id does not match its piece or event, or whose event has no relational receipt, refuses; otherwise delete those chunk nodes and drop the legacy collection. Other Cognee graph records are kept. model_labels is empty, because this tier holds no numbers of its own.
  9. Not done here: Cognee's cognify is not run on captured events, so no entity, relationship or summary exists in this tier; Cognee's own search is not used.
tools:
- duckdb
- cognee
- asyncio
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_5_B_1
- event_id: string, CORE — the event processed
- receipt_status: string, CORE — processed, processed_no_new_fact or the failure recorded
- pieces_stored: integer, DEBUG — chunk nodes written for the event
- models_ranked: list of string, CORE — the model labels that produced a ranking
- models_left_out: object, CORE — model label to reason
memory_routing:
  emits_memory_events: false
  memory_tiers: [relational]
  memory_event_types: []
failure_modes:
- No pieces yet for an event -> retryable failure "numbers not yet supplied: the supplier has not yet handed over the pieces of <event>", pieces request recorded
- No owner reader or numbers services attached -> RuntimeError naming what is missing
- Unknown visibility mode -> ValueError, nothing returned
- Every model stale or without question numbers -> empty rankings with every model in left_out and its reason; never an empty success
- A rebuild meets more chunks than its bound, or a chunk without owner provenance -> ValueError, nothing deleted
- No embedding model is bound (tiers.relational.embedding_model unset) -> Cognee's embedder slot is empty; the chunk write path never embeds, so storing still works, and any Cognee vector call would refuse
- Measured weakness (runs of 2026-10-26 and 2026-10-27): the processor-only static reader ranked the right chunk first for "What pets does Ada keep?" and "Who leads the Kelp Project?"; with all seven readers merged and the second stage, the relational tier's final order was right first 3 of 8 times on the ground-truth set, too few cases to decide anything
tests:
- type: unit
  description: Replaying an event overwrites the same chunks
  input: one relational event with two supplied pieces, processed twice
  expected: two chunk nodes exist, with ids uuid5 of each piece id, and both receipts say processed
  tolerance: exact
- type: unit
  description: Search by meaning from received numbers
  input: stored chunks for the Ada notes and a cat chat with the door's numbers; question "What pets does Ada keep?"
  expected: the cat chat piece is first in the static reader's ranking (cosine distance about 0.51)
  tolerance: semantic
- type: invariant
  description: agent_memory:INV-007 a model without numbers is reported, not ranked empty
  input: a registered model with no numbers received for event pieces, then retrieve
  expected: the model is in left_out with "stale: no numbers for event pieces have been received for this model yet" and no empty ranking is invented for it
  tolerance: exact
- type: invariant
  description: agent_memory:INV-004 memory never cuts text
  input: Cognee's chunker slot asked to read a document on the memory path
  expected: RuntimeError saying Cognee's own cutter is switched off and pieces are cut by the supplier
  tolerance: exact
- type: invariant
  description: agent_memory:INV-010 the asker's visibility constrains the ranking
  input: two producers' events with numbers; an asker with visibility own
  expected: only the asker's own pieces appear in every model's ranking
  tolerance: exact
logging_tier: CORE

##### M_5_B_2 — Graph Extraction and Entity Description Rewrite
job: Run Cognee's knowledge-graph extraction on one text, or Cognee's entity description rewrite on one stored entity, with the configured language model, storing nothing.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- step: string — extract_graph or rewrite_entity_description
- text: string — for extract_graph, the text to read
- name: string — for rewrite_entity_description, the entity's name in Cognee's graph under cognee.data_directory
- model: string — the model the router chose (main or second opinion), from the language_model setting
outputs:
- extract_graph: {nodes, edges, timestamps [{node, timestamp_str, precision, time_at, time_until}]}
- rewrite_entity_description: {name, old_description, new_description, shown_to_model}
- router fields: part, step, opinion, requested_model, achieved_model, placement, seconds
artifacts:
- model_step_output.json
data_contracts:
- model_step_request.json
- model_step_output.json
seam_contracts:
- M_2_B_1 -> M_5_B_2: the router calls run_step(step, args, model) and adds the model that answered, its placement and the seconds taken
dependencies:
- M_2_B_1
- M_5_A_1
- M_5_A_2
invariants:
- agent_memory:INV-005
- agent_memory:INV-012
- agent_memory:INV-004
tunable_parameters:
- param_name: language_model.model
  default_value: "ornith_35b_a3b_cpu"
  param_type: string
  description: The main model Cognee's steps use; a stored setting with no code default; Cognee reaches it through Ollama's OpenAI-style door at language_model.base_url plus /v1.
- param_name: language_model.request_timeout_seconds
  default_value: "3000"
  param_type: int
  description: The time limit of one Cognee model request; a stored setting with no code default, passed to Cognee as its request timeout.
- param_name: cognee.data_directory
  default_value: "absolute folder beside the memory database file"
  param_type: string
  description: The folder holding Cognee's own graph the rewrite reads from; a stored setting with no code default.
intelligence_pattern: none
logic: |
  1. At service build, CogneeModel checks the language_model settings (provider must be ollama; model, base_url and api_key non-empty; timeout positive; options a table) and that cognee.data_directory is an absolute folder, points Cognee's system and data roots there, selects duckdb for graph, vectors and relational stores, and wraps Cognee's structured-output call once so the model the server reports is noted.
  2. run_step refuses an unknown step or a missing argument with ValueError; otherwise, holding one lock because Cognee's configuration is process-wide, it clears every per-stage and fallback model override, sets Cognee's model fields from the settings with only the model replaced by the router's choice (temperature, top_p and reasoning_effort as model arguments, max_tokens as the completion ceiling, the timeout), runs the step and returns plain JSON.
  3. extract_graph: run Cognee's extract_content_graph on the text; for every node whose name is a date, add the Timestamp Cognee makes (precision and span); store nothing.
  4. rewrite_entity_description: find the entity by name among Cognee's entity nodes (none found: ValueError naming it); read its neighbourhood (properties, edges, neighbours, types); build the neighbourhood text; ask Cognee's own query_LLM with Cognee's consolidation prompt; return the old description, the new one and the text shown; the new description is never written back.
  5. Ledger row 24's other parts are specified here but not built: feedback weights, truth state, conflict review, entity consolidation written back to the graph, cross-connecting and session-to-graph promotion (Cognee's memify tasks) are not reached by any step; asking for them is refused as an unknown step.
  6. The graph extracted here is never stored in the relational tier: the tier holds chunk nodes only, because cognify is not run on captured events.
tools:
- cognee
- asyncio
- openai
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_5_B_2
- step: string, CORE — the step run
- requested_model: string, CORE — the model asked for
- achieved_model: string, CORE — the model the server reported
- seconds: float, TRAINING — time the step took
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Model reply is not valid against Cognee's schema -> Cognee retries; after its retries the step fails with Cognee's error (the 4B model answered a copy of the schema and failed with "description Field required" after three retries)
- Unknown step or missing argument -> ValueError listing the steps or the missing arguments
- Entity not in the graph -> ValueError "No entity named ... is in the graph"
- The socket model operation's visibility checks do not cover Cognee's text steps: any caller may send any text, and the graph under cognee.data_directory is not limited by the caller's visibility (known gap)
- The model answers but carries no model name -> achieved_model stays at the previous value or none; the requested model is never reported in its place
- Measured (2026-10-23, main model): extract_graph on a five-sentence note gave 9 nodes and 7 edges in 217 s, all grounded; the rewrite gave a correct description in 45 s with one stale line ("a resident of Bergen") because the graph holds both facts
tests:
- type: unit
  description: Graph extraction finds grounded entities and dates
  input: extract_graph on the five-sentence Ada note
  expected: nodes include Ada Lovelace, Bergen Marine Institute, Bo Eriksen, Kelp Project, Oslo and two day-precision Timestamps (2026-03-01, 2026-10-20); no invented names
  tolerance: semantic
- type: invariant
  description: agent_memory:INV-005 no model is named in code
  input: the language_model.model setting changed and the service restarted, then extract_graph run
  expected: requested_model is the new setting's model and nothing else changed
  tolerance: exact
- type: invariant
  description: agent_memory:INV-012 the model that answered is recorded
  input: any extract_graph call
  expected: the answer carries achieved_model equal to the model name in the server's reply
  tolerance: exact
logging_tier: TRAINING

##### M_5_B_3 — Temporal Events and Query Windows
job: Turn text into dated Cognee events, or a question into a time window, with the event pipeline restored from upstream Cognee on the configured language model.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- step: string — extract_events, store_events, attach_event_entities, query_window or date_anchors
- text: string — for extract_events and store_events
- events: list of {name, description} — for attach_event_entities
- question: string — for query_window
- starts_at, ends_at: ISO 8601 string or null — for date_anchors
- chunk_ids, entity_ids: list of string — for date_anchors
- model: string — the model the router chose
outputs:
- extract_events: {events [{name, description, location, kind point or interval, timestamps}], refused [{event, reason}]}
- store_events: {chunks, stored_by_type, graph_total_nodes, graph_total_edges}
- attach_event_entities: {events [{event, attributes [{relationship, entity, entity_type}]}], answered_for, asked_for}
- query_window: {starts_at, ends_at, reason}
- date_anchors: {timestamps, anchors {timestamp_ids, chunk_ids, entity_ids}}
artifacts:
- model_step_output.json
data_contracts:
- model_step_request.json
- model_step_output.json
seam_contracts:
- M_2_B_1 -> M_5_B_3: the router calls run_step and adds the answering model, placement and seconds
- M_6_B_4 -> M_5_B_3: Mem0 Platform's temporal reasoning is reproduced, for absolute dates only, by query_window
dependencies:
- M_2_B_1
- M_5_A_1
- M_5_A_2
- M_5_B_2
invariants:
- agent_memory:INV-005
- agent_memory:INV-012
- agent_memory:INV-011
tunable_parameters:
- param_name: language_model.model
  default_value: "ornith_35b_a3b_cpu"
  param_type: string
  description: The main model the event and window steps use; a stored setting with no code default.
- param_name: cognee.data_directory
  default_value: "absolute folder beside the memory database file"
  param_type: string
  description: The folder holding the DuckDB graph store_events writes and date_anchors reads; a stored setting with no code default; this is Cognee's own file, not the memory database.
intelligence_pattern: none
logic: |
  1. The restored files are translated from upstream commit 1513b1f41^ (Cognee removed them on 2026-10-06; release 1.6.5 never had them): Event and Interval models, generate_event_datapoint, generate_timestamp_datapoint, extract_event_entities, extract_event_graph, the two prompts byte for byte, and the temporal_graph tasks. Changes stated: event times are built with the current timestamp_from_text (to the second); the removed prompt-path settings are replaced by the shipped prompt files; get_event_tasks has no fallback of 10 chunks per batch and refuses without chunks_per_batch; an event and entity count mismatch raises where the original paired silently; temporal_cognify is not brought back.
  2. Every step goes through the same lock and model configuration as M_5_B_2.
  3. extract_events: ask the model for the text's events; turn each into an Event node with Timestamp nodes for its time, start and end; an event whose stated time is not a real date goes to refused with the reason, never dropped silently; store nothing.
  4. store_events: refuse a blank text; make one document and one chunk whose ids derive from the text's hash (the same text rewrites the same document and chunk); run extract_events_and_timestamps, then extract_knowledge_graph_from_events (entities around each event joined by a named relationship); store the chunk with add_data_points into Cognee's graph only (no vector engine) under cognee.data_directory; answer counts by node type and the graph totals.
  5. attach_event_entities: build Event nodes from the given names and descriptions; ask the model once for all of their entities (enrich_events); attach each with add_entities_to_event; answer per event the relationship, entity and entity type, and how many events the model answered for; store nothing.
  6. query_window: run Cognee's extract_query_interval; answer the window start and end (null for an open side) and the reason when there is no window; relative dates such as "last week" are not guessed by Cognee's prompt and get no window.
  7. date_anchors: convert the window to epoch milliseconds; ask Cognee's graph for the Timestamp nodes in the window and for which given chunk or entity ids are attached to one; change nothing.
  8. Not joined: the restored flow hangs Timestamps on Event nodes, not on the chunk, so get_temporal_anchors returns no chunk for a date window and Cognee's TemporalHybridRetriever would not move the chunk up; that retriever also needs numbers and is not wired; the relational tier's chunks (M_5_B_1) are not linked to these events.
tools:
- cognee
- asyncio
- openai
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_5_B_3
- step: string, CORE — the step run
- achieved_model: string, CORE — the model the server reported
- events_found: integer, TRAINING — events the model found
- window_reason: string, TRAINING — why no window was given, when none was
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Blank text to store_events -> ValueError, nothing stored
- An event time that is not a real date -> listed under refused with the reason
- get_event_tasks called without chunks_per_batch in Cognee's cognify settings -> ValueError; the service does not call it (store_events calls the tasks directly)
- Date window asked of chunks -> no chunk is anchored, because Timestamps hang on events (known gap)
- Relative date question ("What did Ada do last week?") -> no window, reason no_time_constraint
- store_events writes a second DuckDB file under cognee.data_directory, opened by the memory process through Cognee, not the memory database; it is not covered by the owner's receipts or forgetting, and a forgotten event's text sent to this step stays there
- The socket model operation's visibility checks do not cover these text steps (known gap)
- The 4B second-opinion model gave no window for "What happened in March 2026?" (wrong; it should be 2026-03-01 to 2026-04-01)
tests:
- type: unit
  description: Question to window
  input: query_window on "What happened in October 2026?"
  expected: starts_at 2026-10-01, ends_at 2026-11-01, reason null
  tolerance: exact
- type: unit
  description: Events stored with their dates
  input: store_events on the five-sentence Ada note with the main model
  expected: one chunk stored with Event, Interval, Timestamp, Entity and EntityType nodes (the 2026-10-23 run stored 1 chunk, 8 Event, 3 Interval, 2 Timestamp, 9 Entity and 6 EntityType nodes)
  tolerance: semantic
- type: invariant
  description: agent_memory:INV-011 no batch size written into code
  input: get_event_tasks with chunks_per_batch unset in Cognee's settings and not given
  expected: ValueError saying chunks_per_batch is not set; no default of 10 is used
  tolerance: exact
- type: invariant
  description: agent_memory:INV-012 the model that answered is recorded
  input: any query_window call
  expected: achieved_model equals the model name in the server's reply
  tolerance: exact
logging_tier: TRAINING

##### M_5_B_4 — Search Types, Loaders, Importers and Evaluation
job: Specify Cognee's remaining surfaces (its add, cognify, search, remember, recall, improve, forget calls, 21 search types, ontology, loaders, importers, servers, evaluation framework), none of which the memory service reaches today.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- documents: text, CSV, code, image, audio, video, PDF or Office files — for Cognee's loaders
- question: string — for a Cognee search type
- search_type: one of Cognee's 21 search types
- ontology: OWL or RDF file — for ontology-grounded extraction
- source data: exported memories of Mem0, Zep, Graphiti or Letta — for the importers
outputs:
- search answers: per search type, Cognee's own result shape
- evaluation scores: LoCoMo or BEAM benchmark results
artifacts: []
data_contracts: []
seam_contracts:
- M_5_B_4 -> M_7 numbers services: any search type that ranks by meaning would have to take question and passage numbers from the supplier's door, as M_5_B_1 does; Cognee's own embedding calls are refused on the memory path
dependencies:
- M_5_A_1
- M_5_A_2
- M_5_B_1
- M_5_B_2
- M_5_B_3
invariants:
- agent_memory:INV-004
- agent_memory:INV-007
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. What it must do when built: accept documents through Cognee's loaders without Cognee's own cutter (pieces come from the supplier); run cognify (entities, relationships, summaries) through the model router; answer each of Cognee's 21 search types with numbers from the supplier's door only; honour an OWL or RDF ontology; import other engines' memories as captured events so the owner keeps the originals; serve Cognee's MCP server, REST service, UI and visualiser only behind the owner's caller identity; run the LoCoMo and BEAM evaluations through the router.
  2. What exists today: the code is in the vendored Cognee 1.6.5 fork, unadapted. text-to-SQL reads SQLite through DuckDB read-only; the BROAD record store keeps its records in DuckDB. None of it is called by the memory service, by a socket operation or by a model step.
  3. What has run: Cognee's extract_content_graph (the model half of cognify, M_5_B_2), Cognee's chunk search by meaning through the relational tier from the door's numbers (M_5_B_1, 2026-10-24), dlt database ingestion on DuckDB (M_5_A_2, 2026-10-22). Nothing else in this node has run.
  4. Every request for these surfaces today is answered by their absence: the socket has no operation for them, and the cognee model part answers only the seven steps of M_5_B_2 and M_5_B_3; any other step name is refused.
tools:
- cognee
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_5_B_4
- surface: string, CORE — the Cognee surface asked for
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- A Cognee search type, cognify, a loader, an importer, the MCP server, REST, UI or evaluation is asked for -> not built on the memory path; the cognee part refuses the step as unknown, listing its seven steps
- Cognee's vector indexing during cognify would call an embedding engine -> on the memory path the engine is ReceivedNumbersEmbeddingEngine, which raises when numbers were not received ("numbers not yet supplied")
- Ladybug, Kuzu or LanceDB paths -> cannot load (cognee_db_workers absent)
tests:
- type: unit
  description: An unbuilt surface is refused, not faked
  input: socket operation model with part cognee and step search
  expected: ValueError "part 'cognee' has no step 'search'" listing the seven steps
  tolerance: exact
- type: invariant
  description: agent_memory:INV-004 Cognee's embedder never computes numbers
  input: Cognee's embed_text on the memory path for a text with no received numbers
  expected: refused with "numbers not yet supplied"; no vector is produced
  tolerance: exact
logging_tier: DEBUG

### M_6 — Personalization Memory (Mem0)
description: Keeps the personalization tier as Mem0 memories in the memory database, scoped by the producer, consolidated by Mem0's ADD, UPDATE, DELETE or NONE decision restored from Mem0 1.0.11, and ranked once per Embedding model from received numbers; offers Mem0's language-model steps (facts, decisions, procedural text, graph triplets, relation endings) through the model router; and states which Mem0 Platform behaviours are reproduced through other nodes and which are opaque. Mem0 runs from a fork of Mem0 2.2.1 (Apache-2.0) with a DuckDB vector store and history, a received-numbers embedder and telemetry forced off.
dependencies:
  - M_0
  - M_1
  - M_2
  - M_4
  - M_5
entry_conditions:
  - M_0, M_1, M_2, M_4 and M_5 are VERIFIED
  - the settings tiers.personalization.memory (embedder, vector_store, enable_update_delete_branches) and language_model are stored in the memory database
exit_conditions:
  - all submodules VERIFIED
  - all artifacts schema-valid
  - all tests passed
  - end-to-end module test passed
module_test: Capture the chat "I am Ada. I keep two cats named Nora and Siv." as a version 2 personalization event, let the door hand over the numbers, and confirm the receipt says processed, memories "Name is Ada" and "Keeps two cats named Nora and Siv" exist with history rows, and a retrieve answers one ranking per fresh model; then capture "Update: I now have three cats, Nora, Siv and a new kitten named Tuva." and confirm the cats memory is updated under the same id.

#### M_6_A — Mem0 on DuckDB
purpose: Give Mem0 a DuckDB home on the owner's handle and make Mem0 the owner's personalization tier with numbers it only ever receives.
inputs:
  - the owner's native DuckDB handle
  - captured personalization events and removal events
  - received numbers looked up by text
outputs:
  - Mem0 tables: the memory collection, the entity collection, history, messages
  - receipts and per-model rankings
dependencies:
  - M_0_D_1
  - M_1_B_2
  - M_2_A_1
tests:
  - a plain-text note captured for personalization -> terminal failure "Payload is not a Mem0 capture"
  - a chat whose fact numbers are not yet received -> retryable failure "numbers not yet supplied" and derived requests recorded
  - a forget of a captured chat -> every memory derived from it is deleted with a DELETE history row

##### M_6_A_1 — DuckDB Vector Store and History
job: Keep Mem0's memories, entity links, change history, recent session messages as DuckDB tables on the owner's borrowed handle.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- connection: native DuckDB handle, optional — the owner's handle to borrow; when absent the store opens its own file
- collection_name: string — a plain SQL identifier naming the table
- vectors, payloads, ids: lists — memories to insert or replace
- filters: object — Mem0 filters with eq, ne, in, nin, gt, gte, lt, lte, contains, icontains, $or, $not
- messages, session_scope: list and string — session messages to keep
outputs:
- collection rows: (id VARCHAR, vector FLOAT[width], payload JSON)
- history rows: (id, memory_id, old_memory, new_memory, event ADD|UPDATE|DELETE, created_at, updated_at, is_deleted, actor_id, role)
- message rows: (id, session_scope, role, content, name, created_at)
- search results: memories ordered by exact cosine similarity, or by BM25 for keyword search
artifacts:
- mem0_memory_record.json
- mem0_history_row.json
data_contracts:
- mem0_memory_record.json
- mem0_history_row.json
seam_contracts:
- M_6_A_2 -> M_6_A_1: Memory(config, connection=owner handle) binds the vector store, the entity store and the history manager to the owner's handle; none of them begins, commits, rolls back or closes it
dependencies:
- M_0_D_1
invariants:
- agent_memory:INV-001
- agent_memory:INV-003
- agent_memory:INV-011
tunable_parameters:
- param_name: tiers.personalization.memory.vector_store.provider
  default_value: "duckdb"
  param_type: string
  description: Mem0's vector store; a stored setting with no code default; any other provider is refused at load; its width (vector_store.config.embedding_model_dims) is a required setting that must match the embedder's registered model row.
intelligence_pattern: none
logic: |
  1. DuckDBVectorStore refuses a collection name that is not a plain identifier; on a borrowed handle it checks the handle is native DuckDB and never opens another; it creates the collection table if absent.
  2. Every write (insert or replace, delete, update) runs as one unit: on an owned handle with no open transaction it begins and commits or rolls back; on a borrowed handle it joins the caller's transaction; before writing it drops this collection's full-text index so no stale or deleted text survives in it.
  3. search ranks by exact cosine similarity with the given filters and top_k; search_batch does the same for several vectors; get, list, col_info, list_cols, delete_col and reset read or manage the table. Filters accept the ten comparison operators plus $or and $not; an unknown operator or key is refused.
  4. keyword_search loads DuckDB's fts extension from the local install only (never downloads); when fts is not installed it warns once and returns None, which Mem0 reads as keyword search unsupported; otherwise it builds the index when absent and ranks by BM25.
  5. DuckDBManager creates history and messages tables if absent, migrates a legacy history table of the current schema only, records ADD, UPDATE and DELETE history rows, and on save_messages inserts the messages then keeps only the latest ten per session scope.
  6. The entity store is a second collection on the same provider and handle; Mem0 links each memory's entities to it; while use_planned_embeddings is active, entity upkeep takes numbers only from the prepared set and records any miss.
  7. On the owner path, Mem0's own search (semantic plus BM25 plus entity boost), keyword_search, get_all, update, delete_all and rerankers are built but not used; the tier ranks from the owner's per-model numbers tables (M_6_A_2).
tools:
- duckdb
- mem0
- pydantic
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_6_A_1
- collection_name: string, DEBUG — the table written or searched
- history_event: string, CORE — ADD, UPDATE or DELETE
- fts_available: boolean, DEBUG — whether the fts extension loaded
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- The fts extension is not installed -> keyword_search returns None with one warning; Mem0's keyword ranking is silently absent wherever it would be used (not on the owner path today)
- Width default 1536 and collection name default mem0 are written in code in the store and its settings class, and save_messages keeps ten messages per session by a number written in code (agent_memory:INV-011 broken; the owner always passes the width from the settings, so the 1536 default is not reached on the owner path; the ten-message cut is)
- save_messages deletes older session messages from Mem0's messages table; the originals stay in the owner's payload store, which nothing here touches
- Inherited constants of Mem0's own search written in code: entity similarity threshold 0.95, top_k 10000, 5 and 500, entity boost cut 0.5 and weight 0.001, fetch max(limit times 4, 60), delete-all batch 1000
- A collection name that is not a plain identifier -> ValueError, nothing created
- An unknown filter operator -> refused, nothing returned
tests:
- type: unit
  description: Filters with comparison operators
  input: three memories with payload score 1, 2 and 3; list with filter {"score": {"gte": 2}}
  expected: the two memories with score 2 and 3
  tolerance: exact
- type: unit
  description: Session messages are cut to the latest ten
  input: twelve messages saved in one session scope
  expected: the ten newest remain in messages for that scope
  tolerance: exact
- type: invariant
  description: agent_memory:INV-003 Mem0 writes join the owner's transaction
  input: an insert on a borrowed handle inside an open transaction that is then rolled back
  expected: no row remains and the store issued no BEGIN, COMMIT or ROLLBACK
  tolerance: exact
logging_tier: DEBUG

##### M_6_A_2 — Mem0 Tier (received numbers)
job: Serve as the owner's personalization tier, in which every Mem0 capture becomes scoped memories ranked once per Embedding model from received numbers.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- event_envelope: JSON object — a captured personalization event, or an owner-authored system.expire or system.forget event
- verified_payload_bytes: bytes or null — version 2 payload {messages [{role, content}], infer optional boolean}, or a removal payload {operation, target_event_id, target_actor_agent_id}
- received numbers: lookup (model id, revision, text) -> numbers or none, supplied by the owner's numbers services
- scope: RetrievalScope — the asker's identity and visibility
- query: string — the search question
- limit: integer — the most items per ranking
outputs:
- tier_result: {status processed|processed_no_new_fact, processor_id "mem0-duckdb", processor_version (installed mem0_duckdb version), projection_version "mem0-history-vector-1"}
- tier_rankings: {rankings {model label: [{item_id, id, event_id, actor_agent_id, memory, distance, source_artifact_id, source_revision}]}, left_out {model label: reason}, not_current []}
artifacts:
- mem0_memory_record.json
- tier_rankings.json
- processor_result.json
data_contracts:
- mem0_capture_payload.json
- removal_payload.json
- mem0_memory_record.json
- tier_rankings.json
- processor_result.json
seam_contracts:
- M_2_A_2 -> M_6_A_2: prepare runs outside any write transaction; apply runs on the owner's open transaction; the receipt commits on the same connection
- M_6_A_2 -> M_6_B_1: prepare calls Mem0's stage 1 (extract facts), stage 2 (find existing memories) and stage 3 (decide actions); apply calls stage 4 (execute actions)
- M_6_A_2 -> M_7 numbers services: the received-numbers embedder looks up numbers by text for the one configured embedder model; missing texts become derived requests (request_derived); a finished event is marked complete (derived_complete); prepare_query asks question_vectors("derived", query)
- M_6_A_2 -> M_8_A_2: retrieve returns per-model rankings whose items name their event
- M_2_A_3 -> M_6_A_2: a rebuild of the personalization tier calls reset_projection with a bound on items
dependencies:
- M_1_B_2
- M_1_C_1
- M_1_C_2
- M_2_A_1
- M_6_A_1
invariants:
- agent_memory:INV-003
- agent_memory:INV-004
- agent_memory:INV-006
- agent_memory:INV-007
- agent_memory:INV-010
- agent_memory:INV-012
tunable_parameters:
- param_name: tiers.personalization.memory.embedder.provider
  default_value: "received_numbers"
  param_type: string
  description: Mem0's embedder; a stored setting with no code default; any other provider is refused at load because memory never makes numbers; embedder.config.model, embedder.revision and embedder.config.received_numbers.revision name the one registered model whose numbers Mem0's own table holds.
- param_name: tiers.personalization.memory.enable_update_delete_branches
  default_value: "true"
  param_type: bool
  description: Turns on Mem0's ADD, UPDATE, DELETE or NONE flow; a stored setting with no code default; false is refused at load because the tier refuses Mem0's ADD-only pipeline.
intelligence_pattern: event_driven
logic: |
  1. At build, the settings are refused unless the vector store is duckdb, the embedder is received_numbers with a model, a revision and a width matching the vector store, enable_update_delete_branches is true, and no llm section is stored (Mem0's model is made from language_model). The tier stamps an embedding identity (provider, model, revision, width) on every memory. Identity scope: each producer's memories are consolidated under its own user id; a memory may be changed only by its own producer.
  2. initialize_storage, once at start inside a short owner transaction, binds Mem0 to the owner's handle and creates the vector, entity and history tables; a second, different handle is refused.
  3. prepare, for system.expire or system.forget: the payload must be JSON whose operation matches the event type, whose target_event_id equals the envelope's causation event and whose target actor is named; otherwise a terminal failure; no model and no read.
  4. prepare, for a capture: refuse (terminal) if the ADD-only pipeline is configured; bind the embedder to the owner's lookup; refuse if any stored memory carries another embedding identity (EmbeddingIndexMismatch: rebuild needed). Messages: a version 1.0.0 event's content becomes one user message; a version 2 payload must be a non-empty messages list of role and content with an optional boolean infer; anything else is a terminal failure "Payload is not a Mem0 capture".
  5. infer false: look up the numbers of every non-system message; the work stores each as written.
  6. infer true: extract facts with the language model in a worker thread; look up each fact's numbers; read the scoped memories nearest each fact through the owner's reader; decide ADD, UPDATE, DELETE or NONE with the language model; look up numbers for every ADD or UPDATE text and every entity text apply will link. All of this runs outside any write transaction.
  7. Any text without received numbers: record exactly those texts as owed for the configured model (closing requests for texts this attempt no longer has), then raise NumbersMissing "numbers not yet supplied"; the owner records a retryable failure. Otherwise mark the event's derived numbers complete.
  8. apply, on the owner's transaction: for a removal, delete through Mem0's own delete every memory derived from the target event by the target actor (history records each DELETE, entity links updated); answer processed if any were removed, processed_no_new_fact otherwise.
  9. apply, raw work: store each message as one memory, save the messages, answer processed (or processed_no_new_fact for none).
  10. apply, action work: for every UPDATE or DELETE re-read the target; gone since the plan is a ValueError (retryable); authored by another producer is a terminal failure; retire the derived pieces of the text being replaced; execute the actions; save the messages; answer processed if anything was added, updated or deleted, processed_no_new_fact otherwise. If entity upkeep needed a number not prepared, raise so the owner rolls back and prepares again.
  11. prepare_query: ask the question's numbers once per model fresh for derived texts; a stale model, or one without question numbers, goes to left_out with its reason.
  12. retrieve: own limits to the asker's memories, granted to the granted producers (an empty grant answers no rankings with the left-out models), pool to none; any other mode raises ValueError. For each model, in SQL, join that model's non-stale derived pieces to the live memories by event and text fingerprint, leave out retired pieces and removed events, order by cosine distance then id; re-check every hit against the visibility (a hit outside it raises); skip expired memories; cut at limit.
  13. reset_projection (a rebuild): refuse more rows than the bound, a memory without owner provenance or an entity with unmanaged links; otherwise reset the memory and entity collections; history and messages are never reset.
tools:
- mem0
- duckdb
- asyncio
- openai
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_6_A_2
- event_id: string, CORE — the event processed
- receipt_status: string, CORE — processed, processed_no_new_fact or the failure recorded
- actions: object, TRAINING — counts of ADD, UPDATE, DELETE and NONE decided
- owed_texts: integer, DEBUG — texts whose numbers were owed
- models_ranked: list of string, CORE — model labels that produced a ranking
- models_left_out: object, CORE — model label to reason
memory_routing:
  emits_memory_events: false
  memory_tiers: [personalization]
  memory_event_types: []
failure_modes:
- Payload is not a Mem0 capture (for example a plain-text note) -> terminal failure "Payload is not a Mem0 capture: needs a messages list", by design
- Numbers not received for a fact, decision text or entity text -> retryable failure "numbers not yet supplied", exactly those texts owed
- Stored memories carry another embedding identity -> EmbeddingIndexMismatch, nothing written or searched until a rebuild
- UPDATE or DELETE of another producer's memory -> terminal failure naming both producers
- Target memory gone since the plan -> ValueError, retried
- Unknown visibility mode, or a hit outside the visibility -> ValueError, nothing returned
- The language model fails or answers invalid JSON -> the error propagates as a failure of the attempt; the receipt is not marked processed
- The model that actually answered during tier processing is not recorded: receipts carry processor and projection versions only, and the achieved model is recorded only on the model-step path (M_6_B_1) (agent_memory:INV-012 only half met)
- An expiration date in a memory's payload that is not a valid date is read as not expired (inherited Mem0 behaviour)
- Measured weakness (2026-10-26 and 2026-10-27): "Name is Ada" ranks first for every question naming Ada ("How many cats", "How does Ada get to work", "Where does Ada work now?", "Who is Ada's manager?") on every reader and the cross-scorer; "Has three cats" comes first only with the cross-scorer weight 0.6; on the ground-truth set the merged order of all seven readers was right first 0 of 9 times, the nomic reader alone 5 of 9, the second stage 4 of 8; not fixed
tests:
- type: unit
  description: A chat becomes scoped memories from received numbers
  input: version 2 capture "I am Ada. I keep two cats named Nora and Siv." with the door's numbers available
  expected: memories "Name is Ada" and "Keeps two cats named Nora and Siv", each with an ADD history row and the producer's user id; receipt processed
  tolerance: semantic
- type: unit
  description: UPDATE by meaning keeps the id
  input: then the capture "Update: I now have three cats, Nora, Siv and a new kitten named Tuva."
  expected: the cats memory becomes "Has three cats: Nora, Siv, and a new kitten named Tuva" under the same id with an UPDATE history row
  tolerance: semantic
- type: invariant
  description: agent_memory:INV-004 no number is computed
  input: a capture whose facts have no received numbers
  expected: retryable failure "numbers not yet supplied", derived requests recorded for exactly those facts, no memory written
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 forgetting withholds without deleting the original
  input: forget of a captured chat
  expected: every derived memory is deleted with a DELETE history row, the event's original payload row is still in the payload store, and retrieve no longer returns the memories
  tolerance: exact
- type: invariant
  description: agent_memory:INV-012 model work runs outside the write transaction
  input: a version 2 chat capture processed with a language model that takes several seconds to answer
  expected: fact extraction and the decision return before the owner opens its write transaction for apply; apply makes no model call
  tolerance: exact
logging_tier: CORE

#### M_6_B — Mem0 Model Steps
purpose: Run Mem0's language-model steps, inside tier processing and on request through the model router, and state which Mem0 Platform behaviours exist here.
inputs:
  - messages, facts, existing memories, text, stored relations
  - socket operation model with part mem0
outputs:
  - facts, decisions with an id map, procedural text, entity triplets, relations to end
  - the model that answered, its placement and the seconds taken
dependencies:
  - M_2_B_1
  - M_6_A_2
tests:
  - decide_actions on four stored memories and three new facts -> every id maps back to a real id and Mem0's validation passes
  - graph_triplets without a user_id -> ValueError naming the missing argument
  - procedural on a three-message chat -> a structured entry whose quotes are exact substrings of the chat

##### M_6_B_1 — Fact Extraction and Action Decisions
job: Turn a conversation into Mem0 facts with a decision (ADD, UPDATE, DELETE or NONE) for each one, using the configured language model.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- messages: list of {role, content} — the conversation
- user_id: string — the identity scope (on the step path it must be a producer the caller may see)
- existing: list of {id, text} — stored memories nearest the facts
- facts: list of string — the new facts
- model: string — the model the router chose, or the main model during tier processing
outputs:
- facts: list of string
- decisions: {memory [{id, text, event ADD|UPDATE|DELETE|NONE, old_memory}]}
- id_map: object — small integer id to real memory id
- outcome: decided or refused with reason (step path)
artifacts:
- model_step_output.json
data_contracts:
- model_step_request.json
- model_step_output.json
seam_contracts:
- M_2_B_1 -> M_6_B_1: the router calls run_step and adds the answering model, placement and seconds
- M_6_A_2 -> M_6_B_1: tier processing calls stages 1 to 3 in prepare and stage 4 in apply
dependencies:
- M_2_B_1
- M_6_A_1
- M_6_A_2
invariants:
- agent_memory:INV-005
- agent_memory:INV-012
- agent_memory:INV-003
tunable_parameters:
- param_name: language_model.model
  default_value: "ornith_35b_a3b_cpu"
  param_type: string
  description: The main model Mem0's steps use; a stored setting with no code default; Mem0 reaches it through Ollama's OpenAI-style door at language_model.base_url plus /v1.
- param_name: language_model.second_opinion_model
  default_value: "qwen38_4b_distill_cpu"
  param_type: string
  description: The model a step uses when the caller asks for the second opinion; a stored setting with no code default.
- param_name: language_model.request_timeout_seconds
  default_value: "3000"
  param_type: int
  description: The time limit of one Mem0 model request; a stored setting with no code default.
intelligence_pattern: none
logic: |
  1. Stage 1, extract facts: one model call with Mem0's fact prompt (or the stored custom_fact_extraction_prompt, plus custom_instructions when set) asking for JSON; an empty reply gives no facts; a reply that is not JSON is searched for a JSON object; anything else raises LLMError "Invalid fact extraction response"; reads and writes nothing.
  2. Stage 2, find existing memories: for each fact, search Mem0's own table for the 5 nearest memories within the identity scope; every result is checked against the scope; expired memories are left out; answer the unique {id, text} rows.
  3. Stage 3, decide actions: replace real ids with small integers so the model cannot invent an id; one model call with Mem0's update prompt (or the stored custom_update_memory_prompt) asking for JSON; a request failure or invalid JSON raises LLMError; an empty reply gives no actions; with no facts no call is made; the decisions are validated against the id map before they are returned.
  4. Stage 4, execute actions (in tier apply only): ADD stores the text and links its entities; UPDATE and DELETE first re-read the target and refuse one missing or out of scope; NONE only refreshes a newly supplied agent or run id; every change writes a history row.
  5. Step path (socket operation model, part mem0): extract_facts needs messages and a user_id and answers {facts}; decide_actions needs existing and facts as lists and answers {outcome decided, decisions, id_map}, or {outcome refused, reason} when Mem0's validation or the model refuses; the step stores nothing. The step runs on a shallow copy of the tier's Mem0 object whose model client is built for the router's chosen model and records the model the server reports.
tools:
- mem0
- openai
- asyncio
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_6_B_1
- requested_model: string, CORE — the model asked for
- achieved_model: string, CORE — the model the server reported (step path only)
- facts_found: integer, TRAINING — facts extracted
- decisions: object, TRAINING — counts of ADD, UPDATE, DELETE and NONE
- seconds: float, TRAINING — time the step took
memory_routing:
  emits_memory_events: false
  memory_tiers: [personalization]
  memory_event_types: []
failure_modes:
- Fact reply not JSON -> LLMError "Invalid fact extraction response"
- Decision request fails or reply not JSON -> LLMError; on the step path answered as outcome refused with the reason
- A decision names an id outside the map -> refused by Mem0's validation; nothing written
- extract_facts without a user_id, or decide_actions without lists -> ValueError naming what is missing
- During tier processing the achieved model is not recorded (see M_6_A_2)
- Measured: on 2026-10-22 the 8B model gave wrong ids and an empty reply; on 2026-10-23 the main model decided all five actions right in 742 s (UPDATE "Lives in Bergen" to "Moved to Oslo", ADD the coffee fact, NONE for the rest); DELETE and NONE have not been run through the door's numbers
tests:
- type: unit
  description: Facts from a short chat
  input: extract_facts on a 3-message chat about Ada (Bergen, marine biologist, green tea, cilantro) with user_id os-uid-1000
  expected: five grounded facts (name is Ada, lives in Bergen, marine biologist at the university, prefers green tea, hates cilantro), nothing invented
  tolerance: semantic
- type: unit
  description: Decisions keep real ids out of the model's reach
  input: decide_actions with existing "Name is Ada", "Lives in Bergen", "Likes green tea", "Hates cilantro" and facts "Moved to Oslo", "Likes green tea", "Has started drinking coffee"
  expected: UPDATE of "Lives in Bergen" to "Moved to Oslo", ADD of the coffee fact, NONE for the others, every id mapped back to its real id
  tolerance: semantic
- type: invariant
  description: agent_memory:INV-012 the model that answered is recorded on the step path
  input: extract_facts with opinion second
  expected: requested_model is the second-opinion setting and achieved_model is the model in the server's reply
  tolerance: exact
logging_tier: TRAINING

##### M_6_B_2 — Graph Triplets and Delete Decisions
job: Ask the configured language model for the entity triplets of a text, or for the stored relations a new text ends, with Mem0's restored graph-memory prompts, storing nothing.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- step: string — graph_triplets or graph_delete_decision
- text: string — the new text
- user_id: string — the identity self-references are mapped to
- stored: list of {source, relationship, destination} — for graph_delete_decision
- model: string — the model the router chose
outputs:
- graph_triplets: {entities {name: type}, relations [{source, relationship, destination}]}
- graph_delete_decision: {relations_to_end [{source, relationship, destination}]}
artifacts:
- model_step_output.json
data_contracts:
- model_step_request.json
- model_step_output.json
seam_contracts:
- M_2_B_1 -> M_6_B_2: the router calls run_step and adds the answering model, placement and seconds
dependencies:
- M_2_B_1
- M_6_A_2
invariants:
- agent_memory:INV-005
- agent_memory:INV-012
tunable_parameters:
- param_name: language_model.model
  default_value: "ornith_35b_a3b_cpu"
  param_type: string
  description: The main model the graph steps use; a stored setting with no code default.
intelligence_pattern: none
logic: |
  1. Mem0 removed its open-source graph memory on 2026-04-14 (commit a488e190); its model half is translated into graph_extraction.py from Mem0 v1.0.11 graph_memory.py, with the prompts and tool definitions restored unchanged from v1.0.11 graphs/utils.py and graphs/tools.py.
  2. Both steps need non-empty text and user_id, else ValueError.
  3. graph_triplets: ask the model with the extract_entities tool (self references become the user id); names and types are lower-cased with spaces made underscores; then ask with the relations tool for the relations among those entities, cleaned as the original cleans them; answer both; store nothing.
  4. graph_delete_decision: stored must be a list of relations each with text source, relationship and destination, else ValueError; ask the model with Mem0's delete prompt and the delete_graph_memory tool; answer the relations it asks to end; delete nothing.
  5. Not built: storing nodes matched by similarity, invalidating edges in a graph store and neighbourhood retrieval; each needs numbers for node names, and Graphiti's store (M_4) is the graph that keeps invalid-not-deleted edges. The old Neo4j MemoryGraph file (graph_memory.py) is restored but imported by nothing, and it fails to import because langchain_neo4j and rank_bm25 are not declared dependencies.
tools:
- mem0
- openai
- asyncio
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_6_B_2
- achieved_model: string, CORE — the model the server reported
- relations_found: integer, TRAINING — relations returned
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Missing or empty text or user_id -> ValueError
- stored not a list of three-text relations -> ValueError
- A reply with no tool call -> an empty entity map or an empty relation list, reported as such
- Importing graph_memory.py -> ImportError for langchain_neo4j; nothing imports it, so the service is unaffected; the file is dead code
- Storage by similarity is asked for -> not built; waits on Graphiti node matching by numbers (M_4)
tests:
- type: unit
  description: Triplets from a sentence
  input: graph_triplets on "I live in Bergen and I work with Bo Eriksen on the Kelp Project. Bo leads the project." with user_id os-uid-1000
  expected: entities os-uid-1000 (person), bergen (location), bo_eriksen (person), kelp_project (project); relations lives_in, works_with, works_on, leads
  tolerance: semantic
- type: unit
  description: Only the contradicted relation is ended
  input: graph_delete_decision with stored "lives_in bergen" and "bo_eriksen leads kelp_project" and text "I moved to Oslo and left Bergen."
  expected: relations_to_end holds only lives_in bergen
  tolerance: semantic
- type: invariant
  description: agent_memory:INV-005 the graph prompts reach the model from the setting
  input: language_model.model changed and the service restarted, then graph_triplets
  expected: requested_model is the new setting's model
  tolerance: exact
logging_tier: TRAINING

##### M_6_B_3 — Procedural Memory
job: Turn a conversation into a Mem0 procedural memory text with Mem0's own procedural prompt on the configured language model.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- messages: list of {role, content} — the conversation
- prompt: string, optional — a system prompt in place of Mem0's PROCEDURAL_MEMORY_SYSTEM_PROMPT
- model: string — the model the router chose
outputs:
- procedural_memory: string — the procedural entry with any code fences removed
artifacts:
- model_step_output.json
data_contracts:
- model_step_request.json
- model_step_output.json
seam_contracts:
- M_2_B_1 -> M_6_B_3: the router calls run_step and adds the answering model, placement and seconds
dependencies:
- M_2_B_1
- M_6_A_2
invariants:
- agent_memory:INV-005
- agent_memory:INV-012
tunable_parameters:
- param_name: language_model.model
  default_value: "ornith_35b_a3b_cpu"
  param_type: string
  description: The main model the procedural step uses; a stored setting with no code default.
intelligence_pattern: none
logic: |
  1. Refuse messages that are not a non-empty list of role and content texts with ValueError.
  2. Send Mem0's procedural system prompt (or the given prompt), the messages, and the closing user line "Create procedural memory of the above conversation."
  3. Remove code fences from the reply and answer it; store nothing.
  4. Not built: storing the procedural memory. Mem0 2.2.1 marks procedural-memory support for removal (an inherited comment in main.py says so), and Mem0's own procedural store is not used on the owner path; a stored procedural entry would need its numbers from the supplier like every other derived text.
tools:
- mem0
- openai
- asyncio
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_6_B_3
- achieved_model: string, CORE — the model the server reported
- seconds: float, TRAINING — time the step took
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Messages missing or malformed -> ValueError naming what is missing
- The procedural entry is asked to be stored -> not built; the step only answers the text
- Measured: on 2026-10-22 the 8B model echoed the prompt's headings (weak); on 2026-10-23 the main model gave a grounded entry in 437 s with exact quotes and one illustrative file name
tests:
- type: unit
  description: A procedure from a short chat
  input: procedural on a 3-message chat about a weekly Norway sales report
  expected: an entry with the trigger, the four steps (open the workbook, filter Country to Norway, sum Amount by week, export CSV), the naming rule and the user's quotes as exact substrings
  tolerance: semantic
- type: invariant
  description: agent_memory:INV-012 the model that answered is recorded
  input: any procedural call
  expected: achieved_model equals the model in the server's reply
  tolerance: exact
logging_tier: TRAINING

##### M_6_B_4 — Platform Behaviours
job: Specify Mem0 Platform's temporal reasoning, Dream consolidation, feedback features, opaque ranking constants, stating for each what this system reproduces through other nodes versus what stays opaque.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- question: string — a question that may name a time ("last week", a month, a reference date)
- memories: the personalization tier's stored memories
- feedback: POSITIVE, NEGATIVE or VERY_NEGATIVE — Mem0 Platform's feedback values
outputs:
- time window: start and end, or the reason there is none (through M_5_B_3)
- consolidation: superseded or merged memories (through M_6_B_1 and M_4_B_2)
artifacts: []
data_contracts: []
seam_contracts:
- M_6_B_4 -> M_5_B_3: a question's absolute time window comes from Cognee's query_window step
- M_6_B_4 -> M_6_B_1: Dream's Supersede and Merge are Mem0's own action decision
- M_6_B_4 -> M_4_B_2: Dream's Supersede for dated facts is Graphiti's contradiction step
- M_6_B_4 -> M_8_B_2: Mem0 Platform's memory decay (ledger row 32) is carried by the recency decay node
dependencies:
- M_4_B_2
- M_4_D_2
- M_5_B_3
- M_6_B_1
- M_8_B_2
invariants:
- agent_memory:INV-007
- agent_memory:INV-011
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Temporal reasoning (ledger row 33): what it must do: turn a question's time words, including relative ones against a reference date, into a window, then boost memories inside it at search time. Reproduced: absolute windows through Cognee's query_window ("What happened in October 2026?" gives 2026-10-01 to 2026-11-01); Graphiti's valid-from and valid-to fields supply the data side. Not reproduced: relative dates ("last week" gets no window, reason no_time_constraint) and the Mem0 time search; opaque: the boost function, described only in Mem0's changelog of 2026-05-13; there is no Mem0 code to translate.
  2. Dream background consolidation (ledger row 34): Mem0 publishes only a description (Synthesis, Supersede, Merge), no code or prompt. Reproduced: Supersede and Merge are Mem0's action decision (M_6_B_1, which updated "Lives in Bergen" to "Moved to Oslo" and kept the rest) and Graphiti's contradiction step (M_4_B_2, which ends and dates contradicted facts). Opaque, not built: Synthesis of pattern memories from many memories; nothing is invented for it.
  3. Feedback, custom categories, summaries, webhooks, export and batch operations (ledger row 35): Platform API shapes only; the open code has no feedback-ranking, category-assignment or summary method to translate, so nothing is built; webhooks would need a network service; the ranking effect of feedback is undisclosed. The open summary ability is Zep's thread and user summary (M_4_D_2).
  4. Exact ranking constants (ledger row 36): the graph boost, the temporal boost and the decay time constant are undisclosed; there is nothing to run. Decay here (M_8_B_2) uses the published bounds 0.3 and 1.5, the clamp to 0 to 1 and never filtering, with this project's own exponential form and the stored setting ranking.decay_time_constant_seconds.
  5. Every request today for a behaviour marked opaque or not built is answered by its absence: no socket operation or model step offers it.
tools:
- mem0
- cognee
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_6_B_4
- behaviour: string, CORE — the Platform behaviour asked about
- status: string, CORE — reproduced, opaque or not built
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- A relative-date question -> no window, reason no_time_constraint; nothing guesses the dates
- Synthesis asked for -> not built (opaque method)
- Feedback, categories, summaries, webhooks, export or batch operations asked for -> not built; no socket operation exists for them
- Exact Platform constants asked for -> opaque; none is invented
tests:
- type: unit
  description: An absolute window is reproduced
  input: query_window on "What happened in October 2026?"
  expected: 2026-10-01 to 2026-11-01
  tolerance: exact
- type: unit
  description: A relative window is not guessed
  input: query_window on "What did Ada do last week?"
  expected: no window, reason no_time_constraint
  tolerance: exact
- type: invariant
  description: agent_memory:INV-011 no undisclosed constant is invented in code
  input: the code paths for decay, temporal boost and graph boost on the memory search path
  expected: the decay time constant is read from the stored setting ranking.decay_time_constant_seconds; no temporal or graph boost constant exists
  tolerance: exact
logging_tier: DEBUG

### M_7 — Numbers From Embedding
description: Receives, stores and keeps current the numbers that the Embedding readers give each memory and each question, one table per model revision, without ever making a number, cutting a text or loading a model. A model is a registered row of data; its numbers arrive from the supplier's door (today agent_embedding's direct door; agent_corpus's three doors are the declared alternative), are checked against the registered width and stored beside every other model's numbers; what the supplier cannot hand over yet is kept as owed work and asked for again at each maintenance round.
dependencies:
  - M_0
  - M_1
entry_conditions:
  - M_0 is VERIFIED
  - M_1 is VERIFIED
exit_conditions:
  - all submodules VERIFIED
  - all artifacts schema-valid
  - all tests passed
  - end-to-end module test passed
module_test: On a throwaway memory database with two embedding models registered as stored setting rows and a recorded stand-in door that answers for one model and refuses the other, capture one event and run one sync round; confirm the answering model's own table holds one fresh row for the event, the refusing model's table is untouched and the models report lists it with its reason and one owed request; then ask for one question's numbers inside a batch and confirm each model was asked once.

#### M_7_A — Models and Numbers Store
purpose: Hold every registered model revision, its numbers kept side by side with every other model's, and the durable list of numbers still owed.
inputs:
  - model rows: stored setting models, or the trusted register_model call
  - supplied batches: pieces with one model's numbers, from a supplier client
  - supplier replies: current rows, saved numbers, refusals
outputs:
  - memory_models, memory_model_checks and one memory_numbers_<digest> table per model revision
  - memory_pieces, memory_piece_retirements and memory_number_requests rows
  - model freshness states with their reasons
dependencies:
  - M_0_D_2
  - M_1_B_2
tests:
  - Registering the same model row twice -> one registry row, the stored row returned
  - Registering a known revision with another width -> refused, nothing written
  - A batch with one vector of the wrong length -> refused whole, nothing stored
  - A sync round where the supplier refuses one event -> that event's request recorded as owed, the model's pull stops

##### M_7_A_1 — Model Registry
job: Keep each registered model revision as a row of data with its own numbers table, reporting whether it is fresh for search.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- model_id: string — the model's identity as the supplier reports it
- revision: string — the model revision
- width: integer — numbers per text (the class count for a classifier)
- kind: string — embedding or classifier
- supplier_collection: string — the supplier's collection label
- supplier_recipe: string — the supplier's recipe (on agent_embedding's door, the capability id)
- registered_at: string — ISO 8601 time of registration
- source: string — event or derived, the kind of piece freshness is measured for
outputs:
- model_row: object — the stored registry row with its label "id@revision" and its numbers table name
- model_state: object — fresh, reasons, rows, stale_rows, missing_pieces and source
- models_report: JSON object — every model's event and derived freshness plus the numbers still owed
artifacts:
- memory_models
- memory_model_checks
- memory_numbers_<digest>
- models_report.json
data_contracts:
- memory_models
- memory_model_checks
- memory_numbers_<digest>
- models_report.json
seam_contracts:
- M_0_D_2 -> M_7_A_1: at start the search storage setup creates the registry tables and registers every stored models row inside the owner's transaction
- M_7_A_1 -> M_8_C_2: after any registration the starting combiner is seeded when none is current
dependencies:
- M_0_D_2
invariants:
- agent_memory:INV-005
- agent_memory:INV-004
- agent_memory:INV-001
tunable_parameters:
- param_name: models
  default_value: "seven embedding readers: static_retrieval_mrl_en_v1, bge_small_en_v1_5, nomic_embed_text_v1_5, e5_base_v2, qwen3_embedding_0_6b, dinghy_law_0_6b_v1, bge_m3"
  param_type: string
  description: The model rows (model_id, revision, width, kind, supplier_collection, supplier_recipe) registered at every start; a stored setting with no code default, so adding a model is adding a row.
intelligence_pattern: none
logic: |
  1. Refuse a registration whose model_id, revision, supplier_collection, supplier_recipe or registered_at is empty or not text, whose width is not a positive integer (a boolean is refused), or whose kind is not embedding or classifier.
  2. Derive the numbers table name: the prefix memory_numbers_ followed by the first 20 hexadecimal characters of the SHA-256 of the model id, a unit separator and the revision. No model is named in code; only the registry row maps a model to its table.
  3. If the revision is already registered with the same width, kind, collection and recipe, return the stored row and write nothing.
  4. If it is already registered with any of those different, refuse with "register a new revision instead"; a revision's width, kind and supplier never change.
  5. Otherwise create the model's numbers table (event_id, piece_id as primary key, model_id, revision, width, text_fingerprint, received_at, stale, stale_reason, numbers as a fixed array of width floats) and insert the registry row, both inside the caller's transaction.
  6. List models in registration order, optionally of one kind; get one model by id and revision, or nothing when absent.
  7. Measure a model's freshness for one source (event pieces cut from event text, or derived texts such as a Mem0 memory or a Graphiti fact): read its latest label check, count its rows of that source, its stale rows, and the live pieces of that source (event not removed, piece not retired) that have no row for it.
  8. Add a reason for each of: the latest label check is a mismatch; any row is stale; any live piece lacks a row; no row of that source exists. The model is fresh exactly when there is no reason; a stale model is stale for itself only.
  9. Check a vector before it is stored or used: exactly width values, every value a finite number and not a boolean; never pad, truncate or repair.
  10. At every start, register each stored models row (identical rows are left as they are), then seed the starting combiner when an embedding model exists and none is current; the trusted register_model call does the same for one row.
  11. For the models socket operation, report every model's row with its event freshness (fresh, reasons, rows, stale_rows, missing_pieces) and derived freshness (derived_fresh, derived_reasons, derived_rows, derived_missing), plus every owed request without its text.
tools:
- duckdb
- hashlib
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_7_A_1
- model_label: string, CORE — the model named as id@revision
- fresh: boolean, CORE — whether the model may rank this source
- stale_reasons: list of string, DEBUG — every reason the model is not fresh
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Empty name, non-positive or boolean width, or unknown kind -> ValueError naming the field, nothing written
- Known revision with another width, kind or supplier -> ValueError "register a new revision instead", nothing written
- A stored models row is invalid at start -> the start fails before the socket is bound
- A model registered after a combiner is current -> it joins no combiner; search reports it under ranked_not_combined until a combiner naming it is adopted (M_8_C_2)
- A classifier row is registered -> it is stored and reported, but no search path ranks or classifies with it today; only embedding models rank
- The models operation is answered to any authenticated caller with the event ids of owed numbers (only their text is removed), although its own description says it names no event -> a known exposure, not fixed
tests:
- type: unit
  description: A registration creates one numbers table named from the label
  input: model_id m, revision r1, width 4, kind embedding, a collection and recipe
  expected: one memory_models row whose numbers_table is memory_numbers_ plus the first 20 hexadecimal characters of the label digest, and a table with a numbers column of 4 floats
  tolerance: exact
- type: unit
  description: Freshness names every reason
  input: a model with one stale row and one live event piece without a row
  expected: fresh false with the stale-row reason and the missing-piece reason
  tolerance: exact
- type: invariant
  description: agent_memory:INV-005 no model is named in code
  input: two model rows added only as stored setting rows
  expected: both registered and listed with their own tables; no code change was needed
  tolerance: exact
logging_tier: CORE

##### M_7_A_2 — Numbers Receiver
job: Store one supplier batch of pieces with one model's numbers in that model's own table, or refuse the whole batch.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- supplied_numbers: object — model_id, revision, width, pieces, numbers by piece id, and retires for a re-cut
- received_at: string — ISO 8601 time of receipt
- stale_mark: object — a model, a reason and optionally the piece ids to mark stale
outputs:
- counts: object — pieces_new, rows_new and rows_replaced
- stale_count: integer — rows newly marked stale
- numbers_for_text: list of float or nothing — the newest fresh numbers of one model for a text
artifacts:
- memory_pieces
- memory_piece_retirements
- memory_numbers_<digest>
data_contracts:
- supplied_numbers.json
- memory_pieces
- memory_piece_retirements
- memory_numbers_<digest>
seam_contracts:
- M_7_B_1 -> M_7_A_2: a supplier client hands one supplied_numbers batch per model per event
- M_7_A_2 -> M_2_A_2: a re-cut sets the event's relational receipt back to pending so the relational tier drops old chunks and writes new ones through the ordinary processor path
dependencies:
- M_7_A_1
- M_1_B_2
invariants:
- agent_memory:INV-004
- agent_memory:INV-006
- agent_memory:INV-003
- agent_memory:INV-001
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Run every receive, stale mark and lookup as one job on the owner's single worker, inside one transaction that commits whole or rolls back whole.
  2. Refuse the batch when its model revision is not registered, when its width differs from the registered width, or when its numbers are not for exactly its pieces.
  3. Check the cut rules per event: an event's first cut arrives whole with no retires; a later batch for another model carries exactly the same live piece ids; a different set is a re-cut, accepted only when retires names exactly the event's current piece ids; retires that name no current cut being replaced are refused.
  4. For each piece, refuse an unknown source (only event or derived), a fingerprint that is not the SHA-256 of its UTF-8 text, a derived piece whose id is not "derived:<event>:<fingerprint>", an event that was never captured, or a piece id already stored with other content.
  5. Check every vector (exact width, finite numbers) before anything is written.
  6. For a re-cut, add a retirement row for each old piece (reason "the supplier re-cut the event", naming the replacement pieces) and set the event's relational receipt back to pending; old pieces and their numbers are kept, they stop ranking and counting.
  7. Insert each piece if new, then insert or replace that model's row for the piece with stale false, counting new against replaced rows; no other model's table is touched.
  8. Close the pending requests the new row answers (same model, same fingerprint, or a pieces request for that event), clearing the text they carried.
  9. Mark stale on request: every not-stale row of one model, or only the named pieces, with a required reason; rows stay until the supplier's numbers replace them.
  10. Retire the derived piece of a text an engine no longer holds (a Mem0 memory updated or deleted), deleting nothing; return whether one was retired.
  11. Look numbers up by the text's fingerprint: the newest not-stale row of that model, or nothing, which means numbers not yet supplied; never make a vector.
tools:
- duckdb
- duckdb_sqlalchemy_async
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_7_A_2
- model_label: string, CORE — the model whose table was written
- rows_new: integer, CORE — rows added
- rows_replaced: integer, CORE — rows replaced
- retired_pieces: integer, DEBUG — pieces retired by a re-cut
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Unregistered model, wrong width, numbers not matching the pieces, unknown source, bad fingerprint, wrong derived id, unknown event, piece stored with other content, or a bad vector -> ValueError naming the piece, nothing from the batch stored
- A re-cut not sent as the complete new set with exactly the old piece ids in retires -> ValueError quoting the supplier's re-cut duty, nothing stored
- Stale mark with no reason -> ValueError, nothing changed
- Throwing one model's numbers away and rebuilding them from the originals on request (ledger N3, the per-model rebuild) -> not built; today a model's rows are replaced only when a label mismatch marks them stale and a later sync receives new numbers
tests:
- type: unit
  description: A valid batch is stored in its own model's table only
  input: one registered model, one captured event, one piece with a 4-number vector
  expected: pieces_new 1, rows_new 1, the other model's table unchanged, the owed request for that event closed
  tolerance: exact
- type: unit
  description: A re-cut retires the old pieces
  input: an event cut into piece a, then a batch with pieces b and c and retires [a]
  expected: a retired with reason and replacement, b and c stored, the relational receipt pending
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 nothing is deleted
  input: a re-cut followed by a stale mark
  expected: the old piece, its numbers row and the event's payload all still present
  tolerance: exact
- type: invariant
  description: agent_memory:INV-004 a bad vector is never repaired
  input: a vector of 3 numbers for a width-4 model
  expected: ValueError, no row written
  tolerance: exact
logging_tier: CORE

##### M_7_A_3 — Owed Numbers and Sync
job: Keep the durable list of numbers the supplier still owes, pulling whatever it can hand over at each maintenance round.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- supplier: object — the configured supplier client, or the unavailable supplier when none is declared
- request: object — a model, an event id or nothing, a text or nothing, and the reason
outputs:
- sync_report: JSON object — per model label, received_events, received_derived and the pending reason
- request_id: string — the durable owed request
- pending_requests: list of object — requests still owed, oldest first, removed events left out
artifacts:
- memory_number_requests
- memory_model_checks
data_contracts:
- memory_number_requests
- memory_model_checks
- supplied_numbers.json
seam_contracts:
- M_0_A_3 -> M_7_A_3: the maintenance loop calls the trusted sync_numbers every maintenance interval; the service runs no timer of its own
- M_7_A_3 -> M_7_B_1: the supplier interface current_row, saved_numbers, question_numbers and recompute; every method may answer with the sentinel "numbers not yet supplied"
dependencies:
- M_7_A_1
- M_7_A_2
invariants:
- agent_memory:INV-004
- agent_memory:INV-001
- agent_memory:INV-006
tunable_parameters: []
intelligence_pattern: state_aware_poll
logic: |
  1. Declare the supplier interface: current_row(collection, recipe), saved_numbers(collection, recipe, model, revision, event), question_numbers(model, revision, text, role) and recompute(collection, recipe, model, revision, events, reason). Every method may raise the one sentinel NumbersNotYetSupplied, whose message begins "numbers not yet supplied" and names the pending work.
  2. When neither a supplier nor an embedding door is declared, use the unavailable supplier: every call raises the sentinel with the reason that no supplier is connected; it never returns numbers, a row or an empty list.
  3. Record an owed request durably; asking again for the same model, event and text while the earlier request is pending returns the earlier id and writes nothing.
  4. For an event's derived texts, make the open requests exactly the texts now missing: close as superseded (clearing the text) any pending request for a text a retry no longer produces, then record one for each text; close all of them when a retry found every number.
  5. Start each sync round by withdrawing every pending request whose event was removed, clearing its text.
  6. For each registered model, ask the supplier for its current row. On the sentinel, append an unverified label check, set the model's pending reason and pull nothing for it.
  7. If the supplier's model, revision or width differs from the registered label, in one transaction mark all the model's rows stale, append a mismatch check and record a pieces request; then ask the supplier to recompute the events that need numbers. The door accepts; agent_corpus refuses, and its refusal is added to the pending reason. The request stays owed either way.
  8. If the label matches, append a current check, list the live non-system events whose pieces have no row or only a stale row for this model, and ask the supplier for each event's saved numbers in capture order, receiving each batch through the receiver. On the sentinel, record an owed request for that event, set the pending reason and stop this model's pull.
  9. When the model's pull ended with nothing pending, fetch its numbers for every live derived text it lacks or that has an open request, one text at a time through the single-text door with the role passage; store each as the one derived piece keyed by event and fingerprint at position 0. On the sentinel, record the owed request for that text and stop this model's pull.
  10. Report per model what was received and what is still pending; never write another model's rows.
tools:
- duckdb
- duckdb_sqlalchemy_async
- uuid
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_7_A_3
- model_label: string, CORE — the model this summary line is for
- received_events: integer, CORE — events whose numbers arrived this round
- received_derived: integer, CORE — derived texts whose numbers arrived this round
- pending_reason: string, CORE — the supplier's refusal, or nothing when nothing is owed
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Supplier unreachable, answering an error status or not JSON -> sentinel; label check recorded as unverified or the event's request recorded as owed; the next round asks again
- Supplier now runs another model, revision or width -> that model's rows marked stale, mismatch recorded, recompute asked, request kept owed; other models untouched
- A batch breaks a receive rule -> ValueError from the receiver; the round fails, is logged by the maintenance loop and the next round still runs
- Requests for events forgotten or expired -> withdrawn at the next round
- agent_corpus recompute -> always refused (it has no recompute door); the request stays owed
tests:
- type: unit
  description: A refusal is kept as owed work
  input: one registered model, one captured event, a stand-in supplier whose saved_numbers raises the sentinel
  expected: one pending request for that model and event with the supplier's reason; the model's summary names it
  tolerance: exact
- type: unit
  description: A label mismatch marks only that model stale
  input: two models with rows, a supplier whose current row for the first reports another revision
  expected: every row of the first stale with the reason, a mismatch check appended, the second model's rows unchanged
  tolerance: exact
- type: invariant
  description: agent_memory:INV-004 no number is made when the supplier cannot answer
  input: the unavailable supplier and three captured events
  expected: no numbers row written; every model's check is unverified with the reason that no supplier is connected
  tolerance: exact
logging_tier: CORE

#### M_7_B — Suppliers
purpose: Reach the program that makes the numbers, and give the search the question's numbers, cross-scores and keyword rankings, without memory ever computing any of them.
inputs:
  - the stored embedding_door or supplier settings (exactly one of the two, or neither)
  - an event's original text, read through the event loader
  - search questions and candidate passages
outputs:
  - supplied batches, current rows, question vectors, cross-scores and keyword orders
  - the sentinel "numbers not yet supplied" with the door's reason
dependencies:
  - M_7_A_3
  - M_1_D_1
tests:
  - The door answers for another occupant than asked -> the sentinel, nothing stored
  - A batch of three questions -> each fresh model asked once
  - A supplier with no cross-scorer -> the second stage reports not applied with the reason

##### M_7_B_1 — Embedding Door Supplier
job: Fetch event numbers, question numbers, cross-scores or keyword rankings from agent_embedding's door, labelled by the reader that answered.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- base_url: string — the door's address
- requester_agent_id: string — the requester id the door requires in its header
- timeout_seconds: float — the time limit of one request
- event_text: function — reads a memory event's original text through the event loader
- prefixes: object — per model id, the words its authors put before a question and before a passage
outputs:
- current_row: object — the occupant, revision and width the door lists for a capability
- supplied_numbers: object — the event's whole text as one piece with the door's vector
- question_vector: list of float — one reader's numbers for one text
- question_vectors: list of list of float — one reader's numbers for several texts, in order
- cross_scores: object — candidate id to the cross-scorer's score
- word_order: list of string — candidate ids sharing words with the question, best first
artifacts:
- embedding_capabilities_reply.json
- embedding_embed_request.json
- embedding_embed_reply.json
- embedding_feature_pairs_request.json
- embedding_feature_words_request.json
- embedding_feature_reply.json
data_contracts:
- embedding_capabilities_reply.json
- embedding_embed_request.json
- embedding_embed_reply.json
- embedding_feature_pairs_request.json
- embedding_feature_words_request.json
- embedding_feature_reply.json
- supplied_numbers.json
seam_contracts:
- M_7_B_1 -> agent_embedding: GET /capabilities, POST /embed and POST /feature/{capability}, each with the header requester-agent-id
- M_1_D_1 -> M_7_B_1: the event loader returns the verified original, from which the event's text is read
dependencies:
- M_7_A_3
- M_1_D_1
invariants:
- agent_memory:INV-004
- agent_memory:INV-005
tunable_parameters:
- param_name: embedding_door.base_url
  default_value: "http://127.0.0.1:9100"
  param_type: string
  description: Where agent_embedding's door answers (the address named in the 2026-10-23 handoff and the 2026-10-26 run); a stored setting with no code default; refused together with a supplier table, since numbers come from one source.
- param_name: embedding_door.timeout_seconds
  default_value: "unrecorded"
  param_type: float
  description: The time limit of one door request, required and above 0; a stored setting with no code default. Its live value is not recorded in the 2026-10-23 proof database or the 2026-10-27 handoff; the live store holds it.
- param_name: embedding_door.prefixes
  default_value: "declared for nomic_embed_text_v1_5, e5_base_v2, bge_small_en_v1_5 and qwen3_embedding_0_6b"
  param_type: string
  description: Per model id, the question words and passage words the model's authors say go in front of the text; a model with no entry is sent its text unchanged by declaration. A stored setting with no code default; the door adds none of its own.
intelligence_pattern: none
logic: |
  1. Refuse construction when base_url or requester_agent_id is empty or timeout_seconds is not above 0.
  2. Make each request blocking on a worker thread with the JSON body and the requester header. An error status raises the sentinel quoting the status and the first 400 characters of the door's detail; a refused connection, a timeout or an operating-system error raises the sentinel naming the address; a reply that is not JSON raises the sentinel. Never retry.
  3. Read the door's reader list from GET /capabilities; a reply without a list of readers raises the sentinel.
  4. Current row: find the reader whose capability id equals the model's recipe; it must list an integer width and an occupant, else the sentinel. Return the occupant as model id, its revision and width, kind embedding. A capability the door does not list raises the sentinel.
  5. Saved numbers for an event: read the event's original text; no text or blank text raises the sentinel. The whole text is one piece with id "<event>#0" at position 0, so memory cuts nothing. Put the model's passage words in front, POST /embed with the recipe as capability id, and store the fingerprint of the unprefixed text.
  6. Check every embed reply: the reported model id and revision must equal those asked for (a reader swapped behind the door would carry another label), vectors must be a mapping and the width an integer, and each asked id must have a vector of exactly that width; any break raises the sentinel.
  7. Question numbers for one text: find the reader whose occupant and revision equal the model asked for (none: sentinel), put the model's words for the role (question or passage) in front, embed it under the id "question" and return the vector.
  8. Question numbers for several texts: ask each distinct text once in a single request with ids question0, question1 and onward, so the door loads that reader once however many texts there are; return the vectors in the order of the texts, repeats included.
  9. Cross-scores: an empty candidate list returns nothing to score; duplicate ids are refused; POST /feature/{capability} with the declared ids and one pair per candidate (the question as text_a, the candidate as text_b); every candidate must get a number that is not a boolean, else the sentinel. A higher score answers the question better.
  10. Keyword ranking: an empty candidate list returns an empty order; the question is sent as one more passage with id question (a candidate may not use that id); POST /feature/{capability} with the declared ids and all passages; the question's row of scores must be a mapping, else the sentinel. Keep candidates with a score above 0 (no shared word is an absence of evidence, not a rank) ordered by score then id.
  11. Recompute: accept and do nothing, because the door makes numbers when asked; the next sync asks afresh and the request stays owed until numbers arrive.
tools:
- urllib
- asyncio
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_7_B_1
- capability_id: string, CORE — the door capability asked
- achieved_model: string, CORE — the occupant and revision the door reported
- seconds: float, DEBUG — the time one request took
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Door down, timed out, error status or reply not JSON -> sentinel naming the door path and reason; the caller records owed work or leaves the model out
- Door answers with another occupant or revision than asked -> sentinel; nothing is stored under the wrong label
- A reader refuses a long text (measured: bge_m3 refused 23,600 characters with 409) -> sentinel; that model's numbers for the event stay owed, because memory never cuts text
- The door holds one graphics-card reader at a time (25 to 70 s a swap, measured 2026-10-27) -> slow answers; question numbers are batched by M_7_B_3 to load each reader once
- Error detail cut to 400 characters -> a length written in code, a known gap against agent_memory:INV-011
- This direct door is the interim supplier by the 2026-10-24 ruling; the fuller flow through agent_corpus is not built (ledger N10)
tests:
- type: unit
  description: Passage words are put in front of the event text
  input: prefixes {nomic: {passage: "search_document: "}}, an event text "Ada keeps cats", a recorded door reply for nomic
  expected: the door was sent "search_document: Ada keeps cats" and the stored piece text is "Ada keeps cats" with that text's fingerprint
  tolerance: exact
- type: unit
  description: Keyword ranking drops zero scores
  input: a recorded words.bm25 reply scoring candidates a 2.0, b 0, c 1.0
  expected: the order [a, c]
  tolerance: exact
- type: invariant
  description: agent_memory:INV-004 a mislabelled reply is never stored
  input: a recorded embed reply naming revision r2 when r1 was asked
  expected: the sentinel; the receiver is never called
  tolerance: exact
logging_tier: CORE

##### M_7_B_2 — Corpus Supplier
job: Fetch saved numbers or question numbers from agent_corpus's three number doors.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- base_url: string — agent_corpus's address
- requester_agent_id: string — the requester id the doors require as a parameter
- timeout_seconds: float — the time limit of one request
- event_text: function — reads a memory event's original text through the event loader
outputs:
- catalogue: list of object — model, revision, width and last_saved for every saved model
- current_row: object — the revision saved most recently under the recipe's model id
- supplied_numbers: object — the event as one piece when its whole text is one saved passage
- question_vector: list of float — one model's numbers for a question
artifacts:
- corpus_catalogue_reply.json
- corpus_saved_numbers_page.json
- corpus_question_request.json
- corpus_question_reply.json
data_contracts:
- corpus_catalogue_reply.json
- corpus_saved_numbers_page.json
- corpus_question_request.json
- corpus_question_reply.json
- supplied_numbers.json
seam_contracts:
- M_7_B_2 -> agent_corpus: GET /saved_numbers/models, GET /saved_numbers and POST /question_numbers
dependencies:
- M_7_A_3
- M_1_D_1
invariants:
- agent_memory:INV-004
- agent_memory:INV-005
tunable_parameters:
- param_name: supplier.base_url
  default_value: "absent (the live store declares embedding_door instead)"
  param_type: string
  description: Where agent_corpus answers; a stored setting with no code default; declaring it together with embedding_door is refused.
- param_name: supplier.timeout_seconds
  default_value: "absent (the live store declares embedding_door instead)"
  param_type: float
  description: The time limit of one request to agent_corpus, above 0; a stored setting with no code default.
intelligence_pattern: none
logic: |
  1. Refuse construction when base_url or requester_agent_id is empty or timeout_seconds is not above 0.
  2. Make each request blocking on a worker thread; GET doors carry the requester id as a parameter. An error status raises the sentinel quoting the status and the whole detail; unreachable or not JSON raises the sentinel. Never retry.
  3. Catalogue: GET /saved_numbers/models; an empty or missing catalogue raises the sentinel with the door's reason.
  4. Current row: among catalogue entries whose model id equals the recipe, take the one with the latest last_saved; none raises the sentinel. Kind is always embedding, because the catalogue carries no kind.
  5. Saved numbers for an event: read its original text (none: sentinel); compute agent_corpus's passage fingerprint (SHA-256 of the text in Unicode NFC with every line end as one line feed); read the whole collection for that model revision page by page until the cursor is empty, cached per collection, model and revision; if the fingerprint is absent re-read once without the cache; still absent raises the sentinel saying no saved passage equals the event's whole text (it may be cut into several passages, or not read yet).
  6. Return the match as one piece at position 0 with the corpus unit id as piece id, memory's own fingerprint of the raw text, and the saved vector and width.
  7. Question numbers: POST /question_numbers once per distinct text (the latest reply is kept and reused for the same text); return the vector for the model and revision asked; when absent raise the sentinel with any unreached reason the door gave. The role is accepted and ignored, because this door reads every text the same way.
  8. Recompute: always refuse with the sentinel, because agent_corpus has no recompute door.
tools:
- urllib
- unicodedata
- asyncio
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_7_B_2
- door_path: string, CORE — the door asked
- pages_read: integer, DEBUG — saved-number pages read for one collection
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- agent_corpus down, error status or reply not JSON -> sentinel; numbers stay owed
- An event cut into several passages, or not read yet -> sentinel after one re-read; numbers stay owed
- Recompute asked -> always refused; the request stays owed
- This client offers no cross-scorer, no keyword ranking and no several-texts door -> with it, the second stage and the keyword ranking report not applied with the reason, and a batch asks question by question
- Not in use: the live store declares embedding_door, and this client has not been run against a live agent_corpus (ledger N10: the flow through agent_corpus is not built)
tests:
- type: unit
  description: The corpus fingerprint normalises line ends
  input: an event text "a\r\nb" and a saved passage "a\nb"
  expected: the event matches that passage and its piece keeps memory's own fingerprint of "a\r\nb"
  tolerance: exact
- type: unit
  description: Recompute is refused
  input: any recompute call
  expected: the sentinel naming the missing recompute door
  tolerance: exact
- type: invariant
  description: agent_memory:INV-004 no match means no numbers
  input: an event whose text equals no saved passage after a re-read
  expected: the sentinel; nothing returned that could be stored
  tolerance: exact
logging_tier: CORE

##### M_7_B_3 — Search-Time Numbers
job: Give each search the question's numbers from every fresh model, asking each model once per batch of questions.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- questions: list of string — the questions of one batch
- source: string — event or derived, the kind of piece the asking tier ranks
- question: string — one question
- candidates: list of object — id and text, for the cross-scorer or the keyword ranking
outputs:
- question_vectors: object — per fresh model label, the model row and the question's numbers
- left_out: object — per model label, why it cannot rank this question
- cross_scores: object — from the supplier's cross-scorer
- word_order: list of string — from the supplier's keyword ranking
artifacts: []
data_contracts: []
seam_contracts:
- M_7_B_3 -> M_3_B_1, M_4_A_2, M_5_B_1, M_6_A_2: each tier is handed these services through attach_numbers when it is registered
- M_2_A_2 -> M_7_B_3: the dispatcher hands the owner's reader through attach_reader at construction
- M_7_B_3 -> M_5_B_1: the received-numbers embedder object Cognee's adapter is given
dependencies:
- M_7_A_1
- M_7_A_2
- M_7_A_3
invariants:
- agent_memory:INV-004
- agent_memory:INV-007
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Hold the numbers store and the supplier; receive the owner's reader and its event loop when the dispatcher is built; refuse every read before that with RuntimeError.
  2. Open a question batch around a search: on the first open block, prefetch; while any block is open, answer every question-number request from what was fetched; when the last block closes, clear everything, so a later search always asks again and sees a newer revision.
  3. Prefetch: for every embedding model fresh for event pieces, ask the supplier for all the batch's questions with the role question, in one request when the supplier offers several-texts numbers, otherwise text by text; check each vector's width and finiteness. A sentinel is kept as that model's reason for those texts.
  4. Question numbers for one text: require a registered model; answer from the batch when present (a kept reason raises the sentinel again); otherwise ask the supplier, check width and finiteness, and keep the vector while a batch is open. A model fresh only for derived texts is not prefetched; it is asked once per question inside the batch and kept for the rest of it.
  5. Question vectors for a tier: list the embedding models fresh for the tier's source; models that are not fresh are left out with "stale: " and their reasons; a fresh model whose question numbers cannot be had is left out with "question numbers not supplied: " and the reason. A left-out model is never an empty ranking.
  6. For a tier built without these services, answer with no vectors and no left-out models, so the tier falls back to its own answer.
  7. Cross-scores and keyword ranking: hand the call to the supplier when it offers that capability; a supplier without it raises the sentinel naming the missing capability. Memory never scores a pair or a word itself.
  8. Received-number lookups for the tiers: by text fingerprint, the newest fresh row, or nothing; a blocking form is refused on the event loop's own thread, where waiting would deadlock the owner.
  9. Owed requests for the tiers: pieces of an event, one request per registered embedding model; derived texts of an event made exactly the texts now missing; closed once a retry found every number; a derived piece retired on the tier's open transaction.
  10. For Cognee, an embedder object with provider received_numbers bound to one embedding model: it answers each text from received numbers and raises the sentinel for the first text with none; its vector size is the registered width.
tools:
- asyncio
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_7_B_3
- batch_size: integer, CORE — questions in the batch
- models_prefetched: integer, CORE — models asked during the prefetch
- models_left_out: object, CORE — model label to reason
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Supplier cannot give a model's question numbers -> that model left out with the reason for every question of the batch; the others rank
- A model stale for the tier's source -> left out with every stale reason
- Supplier sends a wrong-width or non-finite vector during the prefetch -> ValueError; the whole retrieve or retrieve_batch request answers Validation Error
- Supplier without a cross-scorer or keyword ranking -> sentinel; the stage reports not applied
- Reader never attached -> RuntimeError
- Measured 2026-10-27 with seven readers: a batch of three questions took 206 s and 368 s on two runs, where one question alone took 700 to 824 s before batching
tests:
- type: unit
  description: Each model is asked once per batch
  input: two fresh models, a stand-in supplier offering several-texts numbers, a batch of three questions
  expected: exactly one supplier request per model; every tier's question-number request answered from the batch
  tolerance: exact
- type: unit
  description: A refusing model is left out with its reason
  input: one fresh model whose supplier raises the sentinel
  expected: left_out names the model with "question numbers not supplied" and the sentinel's text
  tolerance: exact
- type: invariant
  description: agent_memory:INV-004 memory never embeds the question
  input: the unavailable supplier and a search question
  expected: no vector returned; every fresh model left out with the reason
  tolerance: exact
logging_tier: CORE

### M_8 — Search and Ranking
description: Answers a question across the four memory kinds for one authenticated caller. Every registered Embedding reader ranks each memory kind from its own numbers, and the rankings are merged together with the supplier's keyword ranking by weighted reciprocal rank fusion under the one current combiner (the owner's ruling: memory uses every Embedding model together and must get better, never worse, as models are added; the weights are stored settings). A recency bias, a second-stage cross-scorer and a validity hold-back for ended facts follow; every answer reports the combiner, the models used and each model left out with its reason, and nothing removed, expired or invisible ever reaches it. A combiner is replaced only by the owner's ruling or by scoring strictly better on held-back judged cases. A context block for prompts is built from the same search.
dependencies:
  - M_0
  - M_1
  - M_2
  - M_3
  - M_4
  - M_5
  - M_6
  - M_7
entry_conditions:
  - M_0 is VERIFIED
  - M_1 is VERIFIED
  - M_2 is VERIFIED
  - M_3 is VERIFIED
  - M_4 is VERIFIED
  - M_5 is VERIFIED
  - M_6 is VERIFIED
  - M_7 is VERIFIED
exit_conditions:
  - all submodules VERIFIED
  - all artifacts schema-valid
  - all tests passed
  - end-to-end module test passed
module_test: On a throwaway memory database with two embedding models holding numbers for four captured events, one forgotten, and a recorded stand-in door for question numbers, cross-scores and keyword scores, send retrieve_batch with two questions; confirm each tier answers with the combiner, both models used, the forgotten event withheld, a merged order equal to a hand computation of weighted reciprocal rank fusion, a rerank report and, in the temporal tier, an ended fact listed under not_current rather than ranked.

#### M_8_A — Retrieval
purpose: Decide what the caller may see, ask every tier, and withhold every hit from an event that is removed, expired or not visible.
inputs:
  - the authenticated principal from the transport
  - retrieve, retrieve_batch or context requests
outputs:
  - per tier either a plain filtered hit list or per-model rankings handed to ranking
  - withheld counts, unavailable and failed statuses with reasons
dependencies:
  - M_1_A_2
  - M_1_C_3
  - M_7_B_3
tests:
  - A caller's own event -> visible; another user's event -> withheld and counted
  - A forgotten event still in a tier's index -> withheld
  - A tier rebuild in progress -> that tier answers failed with the rebuild named

##### M_8_A_1 — Visibility
job: Resolve what an authenticated caller may see, as one visibility applied to every hit, tier query or graph group.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- principal: object — agent_id and scope label from the transport authenticator or the operating system's peer credentials
- event_actor: string — the producer of an event
outputs:
- visibility: object — mode own, pool or granted, with the granted actors
- permits: boolean — whether an event's producer is visible to the principal
- producer_condition: object — the SQL condition on the event's producer for the tiers, or nothing when no producer is visible
- allowed_groups: set of string — the Graphiti group ids the principal may see, or nothing meaning every group
artifacts: []
data_contracts: []
seam_contracts:
- M_1_A_2 -> M_8_A_1: the authenticated principal; the request body never names it
- M_8_A_1 -> M_3_B_1, M_4_A_2, M_5_B_1, M_6_A_2: the retrieval scope carrying the resolved visibility; every tier restricts its reads to it
dependencies:
- M_1_A_2
invariants:
- agent_memory:INV-010
tunable_parameters:
- param_name: visibility.pool_scope_labels
  default_value: "[\"shared\"]"
  param_type: string
  description: The authenticator scope labels placed in the shared pool; when the list is non-empty the scope-label policy is used, otherwise the restricted policy. A stored setting with no code default.
intelligence_pattern: none
logic: |
  1. Use the restricted policy when no pool labels are declared: every principal sees only the events it produced.
  2. Use the scope-label policy when pool labels are declared: a principal whose scope label is in the list sees the shared pool; every other principal sees only its own events.
  3. Permit an event when its producer is known and the mode allows it: own permits only the principal's own events; pool permits every event; granted permits the listed actors. An unknown mode or a missing producer permits nothing.
  4. For a tier's SQL, give the producer condition: own is the principal's id; granted is membership in the listed actors, or nothing when the list is empty (the tier ranks nothing); pool is always true; an unknown mode raises ValueError.
  5. For the graph, give the allowed groups: pool means every group; own means the principal's group; granted means the listed actors' groups plus the principal's. A group id is "actor-" followed by the SHA-256 of the actor.
  6. A policy may refuse a principal with PermissionError; the request then answers "Visibility Refused".
  7. As run today: the runtime registers no authenticator, so every caller is "os-uid-<uid>" with scope label "restricted"; "restricted" is not in the declared ["shared"], so every caller resolves to own, and every process of one operating-system user is one principal. No policy produces granted; that needs a permission owner's policy, which is not built.
tools:
- hashlib
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_8_A_1
- principal_id: string, CORE — the authenticated principal
- visibility_mode: string, CORE — own, pool or granted
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Unknown visibility mode -> permits nothing; the tiers' condition raises ValueError and that tier answers failed
- Policy refuses the principal -> PermissionError, the request answers "Visibility Refused"
- Granted mode with no actors -> the tiers rank nothing for that principal
- The pool label "shared" is never reached by any live caller, because no authenticator supplies that label -> the shared pool is unused today
tests:
- type: unit
  description: Own visibility admits only the principal's events
  input: principal os-uid-1000 with scope restricted, pool labels ["shared"], events by os-uid-1000 and os-uid-1001
  expected: the first permitted, the second not
  tolerance: exact
- type: unit
  description: Group ids for own visibility
  input: principal os-uid-1000 with mode own
  expected: one group "actor-" followed by the SHA-256 of "os-uid-1000"
  tolerance: exact
- type: invariant
  description: agent_memory:INV-010 the request body never widens visibility
  input: a retrieve request whose body names another agent id
  expected: answers contain only the transport principal's events
  tolerance: exact
logging_tier: CORE

##### M_8_A_2 — Tier Retrieval and Withholding
job: Ask every tier for a question's hits, withholding any hit whose event is removed, expired or not visible to the caller.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- principal: object — the authenticated principal
- query: string — one question (retrieve)
- queries: list of string — several questions (retrieve_batch), non-empty
- limit: integer — at least 1, the most hits per answer
outputs:
- retrieve_answer: JSON object — status success and tiers, one answer per memory kind
- retrieve_batch_answer: JSON object — status success and answers, each the question with its tiers, in order
- filtered_hits: object — status ok, hits and withheld, for a tier that ranks by no model
- tier_rankings: object — per-model rankings, left-out models and facts no longer current, handed to ranking
artifacts:
- retrieve_answer.json
- retrieve_batch_answer.json
data_contracts:
- retrieve_request.json
- retrieve_batch_request.json
- retrieve_answer.json
- retrieve_batch_answer.json
- tier_answer.json
seam_contracts:
- M_11_A_1 -> M_8_A_2: the socket operations retrieve and retrieve_batch
- M_8_A_2 -> M_8_B_1: a tier's per-model rankings (rankings by model label, left_out by model label, not_current facts)
- M_3_B_1, M_4_A_2, M_5_B_1, M_6_A_2 -> M_8_A_2: prepare_query off the database, then retrieve on the owner's handle, returning a plain hit list or per-model rankings; every hit names its event_id
dependencies:
- M_8_A_1
- M_1_C_3
- M_2_A_3
- M_7_B_3
- M_3_B_1
- M_4_A_2
- M_5_B_1
- M_6_A_2
invariants:
- agent_memory:INV-010
- agent_memory:INV-006
- agent_memory:INV-007
- agent_memory:INV-001
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. For retrieve, refuse a query that is not text or a limit that is not an integer of at least 1 with "Invalid retrieval request". For retrieve_batch, refuse a list that is empty, holds anything but text, or a bad limit with "Invalid batch retrieval request".
  2. Answer one question as a batch of one. Open one question batch around all questions (when numbers services exist), so each fresh model is asked for every question's numbers in one request; the second-stage cross-scorer is asked afterwards, question by question.
  3. For each question, resolve the caller's visibility once into a retrieval scope (principal id, scope label, visibility).
  4. Ask the tiers in fixed order: episodic, temporal, relational, personalization. A tier with no registered retriever answers unavailable with "no retriever registered for this tier" and no hits.
  5. Before preparing, and again on the worker just before the read, refuse a tier whose projection rebuild is processing or failed: the tier answers failed naming the rebuild.
  6. Let the tier prepare the question off the database (fetching its question numbers through the batch), then read on the owner's handle.
  7. For the episodic and temporal tiers, the shared per-model event ranking orders, for every model with question numbers, the visible live events by the cosine distance between the question's numbers and that model's numbers for each live, unretired, not-stale event piece, nearest first, one row per event, cut to the limit.
  8. A plain hit list goes through withholding and is answered with combiner nothing and the note "this memory kind ranks by no model's numbers". Per-model rankings go to the ranking stage (M_8_B_1 to M_8_B_4).
  9. Withholding: every hit must be an object naming its event_id as text, else the tier fails. Keep a hit only when its event is known, not forgotten, not expired (expiry is recomputed on every call, so an event past its time-to-live is withheld before the maintenance round records its expiry) and its producer is permitted; count the rest as withheld.
  10. Any error inside a tier is logged and answered as that tier's status failed with "<error type>: <message>" and no hits; the other tiers still answer.
tools:
- duckdb
- duckdb_sqlalchemy_async
- logging
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_8_A_2
- principal_id: string, CORE — who asked
- tier_status: object, CORE — tier to ok, partial, unavailable or failed
- withheld: integer, CORE — hits withheld per tier
- questions: integer, CORE — questions in the request
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Malformed query, queries or limit -> "Validation Error", no search run
- No retriever for a tier -> that tier unavailable with the reason
- Rebuild of a tier processing or failed -> that tier failed naming the rebuild
- A tier's retriever raises -> that tier failed with the error, logged; other tiers answer
- A hit without an event_id -> ValueError, that tier failed
- A tier's schema absent because nothing was stored yet -> the tier answers with no hits
- A wrong-width question vector during the batch prefetch -> the whole request answers "Validation Error"
tests:
- type: unit
  description: A tier without a retriever is unavailable, never empty success
  input: a service with only the episodic retriever and one question
  expected: temporal, relational and personalization each status unavailable with the reason
  tolerance: exact
- type: unit
  description: Batch answers keep question order
  input: retrieve_batch with queries [q1, q2] and limit 3
  expected: answers [{query q1, tiers}, {query q2, tiers}] in that order
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 a forgotten event is withheld before its projection is removed
  input: an event forgotten while its tier receipt is still pending, then a question matching it
  expected: the hit withheld and counted; the original still stored
  tolerance: exact
- type: invariant
  description: agent_memory:INV-010 the same visibility constrains every tier
  input: two principals, each with one event, both events matching the question
  expected: each principal's answer holds only its own event in every tier
  tolerance: exact
logging_tier: CORE

#### M_8_B — Ranking
purpose: Turn a tier's per-model rankings into one ordered answer: merged with the keyword ranking, biased by age, re-read by the cross-scorer, rolled up to events and reported in full.
inputs:
  - per-model rankings, left-out models and facts no longer current, from M_8_A_2
  - the search settings and the current combiner
outputs:
  - the tier's answer with combiner, models, decay, words, merged_order, rerank, not_current, rankings and ground_truth_case
dependencies:
  - M_8_A_2
  - M_8_C_1
  - M_8_C_2
tests:
  - Two models ranking the same items in opposite orders -> scores equal to the hand computation of weighted reciprocal rank fusion
  - A model without a declared weight -> left out with "no weight is declared"
  - Cross-scorer unreachable -> merged order stands, rerank not applied with the reason

##### M_8_B_1 — Per-Model Merge
job: Merge one tier's per-model rankings, with the keyword ranking, into one order by weighted reciprocal rank fusion under the current combiner.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- tier_rankings: object — model label to that model's hits best first, left-out models with reasons, facts no longer current
- query: string — the question
- settings: object — the validated search settings
- combiner: object — the current combiner row
outputs:
- merged: list of object — item id with its fused score, best first
- models: object — used, ranked_not_combined and left_out with reasons
- words_report: object — applied or not, capability, ranked, candidates and order, or the reason
artifacts: []
data_contracts:
- tier_answer.json
seam_contracts:
- M_8_A_2 -> M_8_B_1: per-model rankings, each hit carrying item_id and event_id
- M_8_B_1 -> M_7_B_3: the keyword ranking through the supplier's words capability
- M_8_B_1 -> M_8_B_2: the merged order, when decay is declared
dependencies:
- M_8_A_2
- M_8_C_1
- M_8_C_2
- M_7_B_3
invariants:
- agent_memory:INV-007
- agent_memory:INV-008
- agent_memory:INV-004
- agent_memory:INV-011
tunable_parameters:
- param_name: ranking.merging_constant
  default_value: "60"
  param_type: float
  description: The constant added to every rank in reciprocal rank fusion, above 0; a stored setting with no code default.
- param_name: ranking.model_weights
  default_value: "1.0 for each of the seven readers"
  param_type: string
  description: Model label to the weight of its ranking in the fusion, each a finite number above 0; declared together with ranking.words_weight; when absent every ranking counts alike. A stored setting with no code default; equal now, to be learned against hand-verified answers.
- param_name: ranking.words_weight
  default_value: "1.0"
  param_type: float
  description: The weight of the supplier's keyword ranking in the fusion, above 0; declared together with ranking.model_weights. A stored setting with no code default.
- param_name: ranking.words_capability
  default_value: "words.bm25"
  param_type: string
  description: The supplier capability that ranks candidates by shared rare words; when absent no keyword ranking is merged. A stored setting with no code default.
intelligence_pattern: none
logic: |
  1. Refuse to merge with RuntimeError when no search settings were declared; the tier answers failed.
  2. Pass each model's ranking through withholding (M_8_A_2) and add up what was withheld.
  3. Read the current combiner (the latest seeded or adopted decision). When none is current, answer unavailable with "no combiner is current: no embedding model is registered", no hits, and the left-out models.
  4. Used models are the combiner's models that returned a ranking. Each combiner model with no ranking and no reason yet is left out with "the tier returned no ranking for this model".
  5. When model weights are declared, leave out each used model with no weight ("no weight is declared for this model in ranking.model_weights") and drop it from the used list.
  6. Report models: used, ranked_not_combined (models that ranked but are not in the combiner, sorted) and left_out with every reason.
  7. When no model is used, answer unavailable with "no model of the current combiner could rank this question", the combiner and the models report; never an empty success.
  8. When the words capability is declared and the combiner's rule is reciprocal rank fusion, gather candidates from the used rankings (each item's first non-blank text, memory or content) and ask the supplier's keyword ranking. No candidate with text: not applied, "no candidate hit carries text". Sentinel: not applied with the supplier's reason; the merge goes on without it.
  9. Fuse under reciprocal rank fusion: each used model's item ids in its own order, plus the keyword order as the ranking "words:<capability>" when there is one. An item's score is the sum over rankings of the ranking's weight (model_weights for models, words_weight for the keyword ranking, 1 when no weights are declared) divided by the merging constant plus the item's rank counted from 1; an item absent from a ranking adds nothing. Order by score, then the best rank any ranking gave the item, then item id, so the order is repeatable.
  10. Under an external-artifact combiner, ask the artifact supplied for that combiner id to merge the rankings and keep its order, with 1/rank as a reporting score; an artifact not supplied, or an unknown rule, raises RuntimeError and the tier fails.
  11. The fusion is this project's own function: the fusion library chosen for weighted fusion could not be installed, because no project declares the sovereign_memory environment and the environment hook refuses installs against it; replacing the function by the library call waits on the toolkit window.
  12. By the owner's ruling, memory uses every Embedding reader together: the combiner rrf-all-embedding-models is current over all seven registered readers with equal weights 1.0. Getting better and never worse as readers are added rests on weights being learned against hand-verified answers and on M_8_C_3's held-back comparison; neither learning nor any automatic adoption of a new reader is built.
tools:
- duckdb_sqlalchemy_async
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_8_B_1
- combiner_id: string, CORE — the combiner that merged
- models_used: list of string, CORE — model labels fused
- models_left_out: object, CORE — model label to reason
- words_applied: boolean, CORE — whether the keyword ranking was fused
- merged_order: list of string, TRAINING — item ids in fused order
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- No current combiner -> tier unavailable with the reason
- No model of the combiner could rank -> tier unavailable with every left-out reason
- Some combiner models missing -> status partial, the merge uses the rest
- Keyword ranking cannot be had -> words not applied with the reason; the models' merge stands
- Artifact combiner not supplied -> RuntimeError, the tier fails
- A reader registered after the combiner was adopted -> listed under ranked_not_combined and not merged until a combiner naming it is adopted
- Graphiti's own hybrid search parts (breadth-first search, its sixteen search recipes, its five rerankers, its injection-safe filters and the local BGE reranker in its slot; ledger row 15) are not part of this path -> not built; ranking uses each reader's cosine distance, the keyword ranking and the supplier's cross-scorer only
- Measured 2026-10-27 on the eight Ada questions: the merged order of all seven readers was right first 0 of 9 times in the personalization tier and 4 of 12 in the temporal tier, while the nomic reader alone was right first 5 of 9 and 7 of 12 -> equal weights do not yet beat the best single reader on this small set; the counts decide nothing yet
tests:
- type: unit
  description: Weighted fusion matches the hand computation
  input: rankings A [x, y], B [y, x], words [x], weights A 1.0, B 2.0, words 1.0, merging constant 60
  expected: x scores 1/61 + 2/62 + 1/61 and y scores 1/62 + 2/61, ordered best first with ties broken by best rank then id
  tolerance: numerical
- type: unit
  description: A model without a weight is left out
  input: model_weights naming only A, rankings from A and B
  expected: B under left_out with "no weight is declared for this model in ranking.model_weights"; only A fused
  tolerance: exact
- type: invariant
  description: agent_memory:INV-007 every answer names the combiner and each left-out model
  input: a tier where one of two combiner models is stale
  expected: status partial, combiner described, the stale model under left_out with its reasons
  tolerance: exact
logging_tier: CORE

##### M_8_B_2 — Recency Decay
job: Bias each merged score by the age of its event within the declared bounds, never removing an item.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- merged: list of object — item id with its fused score
- created_at: object — each involved event's recorded creation time
- settings: object — the decay time constant and the lower and upper bounds
outputs:
- biased: list of object — item id with its biased score, re-ordered
- decay_report: object — applied with the time constant, or not applied with the reason
artifacts: []
data_contracts: []
seam_contracts:
- M_8_B_1 -> M_8_B_2: the merged order
- M_8_B_2 -> M_8_B_3: the biased order, which the answer reports as merged_order
dependencies:
- M_8_B_1
- M_8_C_1
invariants:
- agent_memory:INV-011
- agent_memory:INV-006
tunable_parameters:
- param_name: ranking.decay_time_constant_seconds
  default_value: "86400"
  param_type: float
  description: The time constant of the age bias in seconds, a finite number above 0; declared together with the two bounds or not at all; absent means the combiner's order stands. A stored setting with no code default.
- param_name: ranking.decay_lower_bound
  default_value: "0.3"
  param_type: float
  description: The smallest factor an old memory's score is multiplied by, zero or above and not above the upper bound; a stored setting with no code default.
- param_name: ranking.decay_upper_bound
  default_value: "1.5"
  param_type: float
  description: The largest factor a score is multiplied by; a stored setting with no code default.
intelligence_pattern: none
logic: |
  1. When no decay time constant is declared, leave the order untouched and report not applied, "no decay_time_constant_seconds is declared".
  2. Refuse with RuntimeError when the bounds are missing; refuse bounds that are negative or out of order, and a time constant that is not finite or not above 0.
  3. Read the recorded creation time of every involved event on the owner's worker; a time with no zone is read as UTC.
  4. For each item, age is now minus its event's creation time, counted as 0 when negative; the factor is e to the power of minus age over the time constant, held between the lower and upper bounds; the biased score is the score times that factor, held within 0 and 1.
  5. Re-order by biased score, keeping the combiner's order among equal scores; never remove an item.
  6. Report applied with the time constant. The answer's merged_order is this biased order, before the second stage.
  7. Provenance: the 0.3 and 1.5 bounds, the clamp to 0 and 1, and "never filters" are the Mem0 Platform changelog's (2026-05-04); no source publishes the time constant or the form, so the exponential form is this project's own.
tools:
- math
- duckdb_sqlalchemy_async
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_8_B_2
- decay_applied: boolean, CORE — whether the bias ran
- time_constant_seconds: float, CORE — the constant used
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- An event's creation time is not ISO 8601 -> ValueError, the tier fails, because its age cannot be known
- Upper bound never binds -> for a non-negative age the factor is at most 1, so 1.5 never applies and only the 0.3 floor shapes results (read from the formula, not measured)
- The clamp of the biased score to 0 and 1 is written in code -> a published rule kept as code, a known gap against agent_memory:INV-011
- The ordering effect of decay alone on a live model ranking was not separated (2026-10-26 run showed only that it is applied) -> unproven how much it moves an answer
tests:
- type: unit
  description: The measured decay example
  input: scores 0.5, 0.4, 0.3 for events aged 30 days, 1 day and 30 seconds; time constant 86400; bounds 0.3 and 1.5
  expected: 0.15, 0.1472 and 0.2999, ordered new, old, mid
  tolerance: numerical
- type: unit
  description: Decay off when undeclared
  input: settings without decay_time_constant_seconds
  expected: order unchanged; report applied false with the reason
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 decay never filters
  input: ten items whose events are all a year old
  expected: ten items returned, each score at 0.3 of its fused score
  tolerance: exact
logging_tier: CORE

##### M_8_B_3 — Second-Stage Cross-Scorer
job: Re-order the best merged results by fusing the merged order with the supplier cross-scorer's reading of each question-passage pair.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- query: string — the question
- merged: list of object — item id with its first-stage score, best first
- texts: object — each item's first non-blank text, memory or content from the used rankings
- settings: object — rerank capability, candidates, weight and merging constant
outputs:
- order: list of object — the final item order with scores
- rerank_report: object — applied, capability, candidates, weight, cross_scorer_order and ordered (item id, cross-score, first 80 characters of text), or not applied with the reason
artifacts: []
data_contracts:
- embedding_feature_pairs_request.json
- embedding_feature_reply.json
seam_contracts:
- M_8_B_2 -> M_8_B_3: the merged order after decay
- M_8_B_3 -> M_7_B_3: pair scores through the supplier's cross-scorer capability
dependencies:
- M_8_B_1
- M_8_B_2
- M_8_C_1
- M_7_B_3
invariants:
- agent_memory:INV-004
- agent_memory:INV-007
- agent_memory:INV-011
tunable_parameters:
- param_name: ranking.rerank_capability
  default_value: "pairs.cross_score"
  param_type: string
  description: The supplier capability that reads the question beside each candidate; declared together with rerank_candidates and rerank_weight, which turn the second stage on. A stored setting with no code default.
- param_name: ranking.rerank_candidates
  default_value: "8"
  param_type: int
  description: How many of the best merged results are offered to the cross-scorer, an integer of at least 2; a starting guess to be tuned against ground truth. A stored setting with no code default.
- param_name: ranking.rerank_weight
  default_value: "0.6"
  param_type: float
  description: The cross-scorer order's share against the merged order in the second fusion, strictly between 0 and 1; 0.5 was tried first, then 0.6. A stored setting with no code default.
intelligence_pattern: none
logic: |
  1. When no rerank capability is declared, keep the order and report not applied, "no rerank_capability is declared".
  2. Take the head: of the first rerank_candidates items of the merged order, those that carry text (items without text inside that window are skipped, not replaced by later items).
  3. When fewer than two items carry text, keep the order and report not applied, "fewer than two of the best results carry text".
  4. Ask the supplier's cross-scorer for each head item paired with the question; on the sentinel keep the merged order and report not applied with the supplier's reason.
  5. Order the head by cross-score, highest first, ties kept in merged order.
  6. Fuse the head's merged order with the cross-scorer's order by reciprocal rank fusion with the merging constant, the merged order weighted 1 minus rerank_weight and the cross-scorer order weighted rerank_weight; ties go to the best rank, then item id.
  7. Append every item below the head in its merged order with its first-stage score; the head's scores are the second fusion's, so scores above and below the head are not on one scale.
  8. Report applied with the capability, the head size, the weight, the cross-scorer's own order and, in final order, each head item's cross-score and the first 80 characters of its text.
  9. Memory never scores a pair itself; the cross-scorer runs in agent_embedding. Graphiti's own cross-encoder slot is filled with a refuser and never asked.
tools:
- duckdb_sqlalchemy_async
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_8_B_3
- rerank_applied: boolean, CORE — whether the second stage ran
- candidates: integer, CORE — head items scored
- cross_scores: object, TRAINING — item id to cross-score
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Cross-scorer unreachable or missing a score -> merged order stands, not applied with the reason
- Supplier offers no cross-scorer (agent_corpus) -> not applied with the reason
- Fewer than two head items with text -> not applied; the threshold of two is written in code, a known gap against agent_memory:INV-011
- Report text cut to 80 characters -> a length written in code, a known gap against agent_memory:INV-011
- Measured 2026-10-27, personalization tier, 'How many cats does Ada have?': at weight 0.5 the fusion tied and the tie-break kept 'Name is Ada' first; at 0.6 'Has three cats' was first. For 'Where does Ada work now?' and 'Who is Ada's manager?' every reader and the cross-scorer still put 'Name is Ada' first -> not fixed
- On the eight Ada questions the second stage helped in the personalization tier (right first 4 of 8) and did not help in the episodic, relational or temporal tiers (3 of 8 each) -> small counts, decide nothing yet
tests:
- type: unit
  description: Second fusion with weight 0.6
  input: head [a, b] in merged order, cross-scores a 0.1, b 0.9, weight 0.6, merging constant 60
  expected: b scores 0.4/62 + 0.6/61 and a scores 0.4/61 + 0.6/62, so b is first
  tolerance: numerical
- type: unit
  description: Items below the head keep their order
  input: merged [a, b, c, d], rerank_candidates 2, cross-scores reversing a and b
  expected: final order b, a, c, d with c and d keeping first-stage scores
  tolerance: exact
- type: invariant
  description: agent_memory:INV-004 memory never scores a pair
  input: a supplier without a cross-scorer
  expected: merged order unchanged; rerank applied false naming the missing capability
  tolerance: exact
logging_tier: CORE

##### M_8_B_4 — Answer Assembly
job: Roll the final order up to events, building the tier's answer with every report the search measured.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- final_order: list of object — item id with score
- merged_order: list of object — the order after decay, before the second stage
- kept_rankings: object — each used model's visible hits
- reports: object — models, words, decay, rerank, withheld
- not_current: list of object — facts the temporal tier held back as no longer true at the question's time
- limit: integer — the most events answered
outputs:
- tier_answer: JSON object — status, hits, withheld, settings_used, combiner, models, decay, words, merged_order, rerank, not_current, rankings and ground_truth_case
artifacts:
- tier_answer.json
data_contracts:
- tier_answer.json
seam_contracts:
- M_4_A_2 -> M_8_B_4: not_current facts (item_id, event_id, fact, valid_at, invalid_at), held back from ranking when valid_at is after the question's reference time or invalid_at is at or before it
- M_8_B_4 -> M_9_A_1: the run's stages (each reader's ranking, the keyword, merged and final orders, the rerank report, the settings and the reports) for the case recorder, which records a case only when the question has a known answer or two readers' first items differ
dependencies:
- M_8_A_2
- M_8_B_1
- M_8_B_2
- M_8_B_3
invariants:
- agent_memory:INV-007
- agent_memory:INV-013
- agent_memory:INV-009
- agent_memory:INV-010
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. For each item take the hit the first used model that ranked it gave, so every event hit shows real text from a real hit.
  2. Group items by event in final order; each event takes the score of its first item and lists its items, each with its final score.
  3. Hand the stages to the case recorder (M_9_A_1). The run report keeps the models, words, decay, the rerank report with its cross-scores but without its ordered text, the not_current facts without their fact text, and the withheld count. A recording failure is added as ground_truth_error and the answer still returns.
  4. Status is ok when every combiner model was used, otherwise partial.
  5. Answer: withheld, settings_used (every search setting in force), status, hits (the first limit events), combiner (id, rule, models, settings_read), models (used, ranked_not_combined, left_out), decay, words, merged_order, rerank, not_current, rankings (each used model's own order cut to limit, each hit's item_id, event_id, distance and whichever of text, memory and content it carries) and ground_truth_case.
  6. not_current lists every fact the temporal tier held back: a fact Graphiti has ended (invalid_at at or before the question's time) or that begins after it. The question's time is the moment the tier prepared the query. expired_at is returned on ranked facts but not used to hold back. A held-back fact is reported, never dropped unseen.
  7. A plain-list tier answers status ok, hits, withheld, combiner nothing and combiner_note.
  8. A rolled-up hit carries event_id, score and items only; the per-hit fields (ranking, content, valid_at) are inside its items.
tools:
- duckdb_sqlalchemy_async
- hashlib
- json
- logging
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_8_B_4
- tier: string, CORE — the memory kind answered
- status: string, CORE — ok or partial
- events_answered: integer, CORE — events in hits
- not_current_count: integer, CORE — facts held back as no longer true
- ground_truth_case: string, TRAINING — the case recorded, or nothing
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Recording the case fails with ValueError -> ground_truth_error in the answer; the search still answers
- Episodes carry no validity -> an old episode can rank first for a present-tense question (measured 2026-10-27: 'joined the Bergen Marine Institute' stays first in the temporal and episodic tiers for 'Where does Ada work now?' while the two ended facts are held back)
- not_current facts are read under the visibility condition and the removal check in the tier's own query but are not passed through the live-producer check -> a fact from an event past its time-to-live, whose expiry the maintenance round has not yet recorded, could be listed with its text (an inference from reading, not measured)
- Rolled-up hits have no top-level ranking or content key -> consumers that read them as raw hits fail (see M_8_D_1)
tests:
- type: unit
  description: Items roll up to events in final order
  input: final order [i1 of e1, i2 of e2, i3 of e1]
  expected: hits [e1 with items i1 and i3 and i1's score, e2 with i2]
  tolerance: exact
- type: unit
  description: An ended fact is reported, not ranked
  input: a fact with invalid_at one day before the question and a current fact, both near the question
  expected: the current fact in hits; the ended fact only under not_current with its dates
  tolerance: exact
- type: invariant
  description: agent_memory:INV-013 a fact ended before the question's time is held back
  input: 'Ada Lovelace lives in Bergen.' ended by contradiction, question 'Where does Ada work now?'
  expected: the fact listed under not_current, absent from hits and rankings
  tolerance: exact
- type: invariant
  description: agent_memory:INV-007 the answer reports the models
  input: any ranked tier answer
  expected: combiner, models.used, models.ranked_not_combined and models.left_out all present
  tolerance: exact
logging_tier: TRAINING

#### M_8_C — Combiner Choice
purpose: Hold the search settings in force, the registry of combiners and the record of which one is current, and change the current one only by the owner's ruling or a strictly better held-back score.
inputs:
  - the stored ranking settings
  - registered embedding models
  - held-back judged cases from the ground-truth store
outputs:
  - validated search settings and their append-only record
  - memory_combiners and memory_combiner_decisions rows
dependencies:
  - M_0_B_1
  - M_7_A_1
tests:
  - A missing merging_constant -> the service refuses to start, naming it
  - A candidate scoring equal on enough held-back cases -> current stays, decision recorded
  - adopt-combiner with no embedding model -> refused, nothing written

##### M_8_C_1 — Search Settings Record
job: Validate the stored ranking settings at start, appending each changed value to the search settings record.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- ranking: object — the stored ranking.* settings rebuilt into one table by the settings loader
- known_scores: list of string — the comparison scores memory can compute
outputs:
- search_settings: object — the thirteen values as declared, none defaulted
- written: list of string — names appended to memory_settings
artifacts:
- memory_settings
data_contracts:
- memory_settings
seam_contracts:
- M_0_B_1 -> M_8_C_1: the ranking table read from memory_runtime_settings; the loader requires it whenever the relational or personalization tier is configured
- M_8_C_1 -> M_8_B_1, M_8_B_2, M_8_B_3, M_8_B_4, M_8_C_3: the validated settings object; its report is what answers and decisions carry as settings_used
dependencies:
- M_0_B_1
invariants:
- agent_memory:INV-011
tunable_parameters:
- param_name: ranking.comparison_score
  default_value: "mean_reciprocal_rank"
  param_type: string
  description: The score used to compare combiners, mean_reciprocal_rank or top_one_accuracy; required, a stored setting with no code default.
- param_name: ranking.held_back_share
  default_value: "0.2"
  param_type: float
  description: The fraction of ground-truth cases held back from training, strictly between 0 and 1; required, a stored setting with no code default.
- param_name: ranking.smallest_held_back_set
  default_value: "2"
  param_type: int
  description: The fewest held-back judged cases that may decide a combiner comparison, at least 1; required, a stored setting with no code default.
- param_name: ranking.merging_constant
  default_value: "60"
  param_type: float
  description: The constant added to a rank in reciprocal rank fusion, a finite number above 0; required, a stored setting with no code default.
- param_name: ranking.decay_time_constant_seconds
  default_value: "86400"
  param_type: float
  description: Optional; turns on the recency bias; declared with both decay bounds. A stored setting with no code default.
- param_name: ranking.decay_lower_bound
  default_value: "0.3"
  param_type: float
  description: Optional; the bias floor, zero or above. A stored setting with no code default.
- param_name: ranking.decay_upper_bound
  default_value: "1.5"
  param_type: float
  description: Optional; the bias ceiling, not below the floor. A stored setting with no code default.
- param_name: ranking.words_capability
  default_value: "words.bm25"
  param_type: string
  description: Optional; the supplier's keyword capability id. A stored setting with no code default.
- param_name: ranking.model_weights
  default_value: "1.0 for each of the seven readers"
  param_type: string
  description: Optional; model label to fusion weight above 0; declared with words_weight. A stored setting with no code default.
- param_name: ranking.words_weight
  default_value: "1.0"
  param_type: float
  description: Optional; the keyword ranking's fusion weight above 0; declared with model_weights. A stored setting with no code default.
- param_name: ranking.rerank_capability
  default_value: "pairs.cross_score"
  param_type: string
  description: Optional; the supplier's cross-scorer capability id; declared with rerank_candidates and rerank_weight. A stored setting with no code default.
- param_name: ranking.rerank_candidates
  default_value: "8"
  param_type: int
  description: Optional; head size for the second stage, at least 2. A stored setting with no code default.
- param_name: ranking.rerank_weight
  default_value: "0.6"
  param_type: float
  description: Optional; the cross-scorer's share, strictly between 0 and 1. A stored setting with no code default.
intelligence_pattern: none
logic: |
  1. Refuse a ranking value that is not a table, a table missing any of comparison_score, held_back_share, smallest_held_back_set or merging_constant, or one holding any key that is neither required nor optional; name each.
  2. Check comparison_score is a known score; held_back_share a number strictly between 0 and 1; smallest_held_back_set an integer of at least 1; merging_constant a finite number above 0. A boolean is never accepted as a number.
  3. Optional groups: the decay time constant (finite, above 0) with both bounds (finite, zero or above, lower not above upper) together or not at all; words_capability non-blank text; model_weights a non-empty table of label to a finite weight above 0 together with words_weight, or neither; rerank_capability non-blank text with rerank_candidates an integer of at least 2 and rerank_weight strictly between 0 and 1, all three or none.
  4. Return the settings with nothing defaulted; an absent optional group means that feature is off.
  5. At construction the service refuses a missing ranking table whenever any tier ranks by numbers, so a missing value fails before anything starts.
  6. At every start, inside the search-storage transaction, append one memory_settings row (name, value as JSON, source "memory_runtime_settings table, [ranking]", time) for each setting whose value differs from its latest record; unchanged settings add nothing and no row is ever edited.
  7. Answers and comparisons report the values from the settings object in force (settings_used); the memory_settings record is written for history and is not read back for values.
tools:
- duckdb
- json
- math
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_8_C_1
- settings_written: list of string, CORE — names appended at this start
- settings_used: object, CORE — every value in force
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Missing, unknown, wrongly typed or out-of-range setting -> ValueError naming it; the runtime reports "ranking: <reason>" and exits 2 before anything starts
- A group declared in part (decay, weights or rerank) -> ValueError naming the group
- Settings changed while the service runs -> refused by set-setting, because the service holds the file; values change only at the next start
tests:
- type: unit
  description: Missing required setting is named
  input: a ranking table without merging_constant
  expected: ValueError "The search settings are missing: merging_constant"
  tolerance: exact
- type: unit
  description: Only changed values are appended
  input: two starts with identical settings, then a start with rerank_weight 0.6 instead of 0.5
  expected: the second start writes nothing; the third writes one rerank_weight row
  tolerance: exact
- type: invariant
  description: agent_memory:INV-011 no default in code
  input: a ranking table with only the four required settings
  expected: decay, keyword ranking, weights and second stage all reported off; nothing invented
  tolerance: exact
logging_tier: CORE

##### M_8_C_2 — Combiner Registry and Adoption by Ruling
job: Keep every proposed combiner with every decision about which one is current, so exactly one combiner is current at any time.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- combiner: object — id, rule, models, settings read, proposer, time
- ruling: object — combiner id, settings read, the owner's words, who recorded it, when
outputs:
- combiner_row: object — the stored combiner
- current_combiner: object — the latest seeded or adopted combiner, or nothing
- decision_row: object — the appended decision
artifacts:
- memory_combiners
- memory_combiner_decisions
data_contracts:
- memory_combiners
- memory_combiner_decisions
seam_contracts:
- M_0_A_2 -> M_8_C_2: the adopt-combiner command opens the database while the service is stopped and records the ruling in one transaction
- M_8_C_2 -> M_8_B_1: the current combiner read at every search
dependencies:
- M_7_A_1
- M_8_C_1
invariants:
- agent_memory:INV-008
- agent_memory:INV-005
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Accept two rules: reciprocal_rank_fusion and external_artifact; refuse any other.
  2. Register a combiner: it needs an id and at least one model; every model must be a registered label; models and settings read are stored sorted. The identical combiner again returns the stored row; the same id with different content is refused. Registering never makes a combiner current.
  3. The current combiner is the one named by the latest decision that is seeded or adopted, latest by decision time and then by insertion.
  4. Seed at every start and after every model registration: when no combiner is current and at least one embedding model is registered, register rrf-start (reciprocal rank fusion over every registered embedding model, reading merging_constant, proposed by owner-system) and append a seeded decision. With no embedding model, nothing is seeded.
  5. Adopt by the owner's ruling (adopt-combiner): refuse when no embedding model is registered; otherwise register reciprocal rank fusion over every embedding model now registered with the settings it reads, and append one adopted decision naming the combiner it replaces and the reason "adopted by the owner's ruling: <words>", with no score because no comparison was made.
  6. Decisions are only appended; an earlier decision is never edited. A later candidate is still compared on held-back cases (M_8_C_3).
  7. A candidate may be proposed through the trusted propose_combiner call; it does not become current.
  8. As run: rrf-all-embedding-models was adopted over the seven readers by the owner's ruling on 2026-10-27.
tools:
- duckdb
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_8_C_2
- combiner_id: string, CORE — the combiner written or read
- decision: string, CORE — seeded, adopted or kept_current
- replaces: string, CORE — the combiner it replaced, or nothing
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Unknown rule, no id, no models, or an unregistered model -> ValueError, nothing written
- Same id with different content -> ValueError
- adopt-combiner with no embedding model -> ValueError, rolled back, exit 2
- adopt-combiner while the service runs -> the file lock refuses, "stop the service first", exit 2
- A reader registered later -> joins no combiner by itself; it is reported as ranked_not_combined until a ruling or a winning comparison names it
- No non-test caller of propose_combiner was found by searching the repository (an inference; a search proves no absence) -> proposing a candidate has no command or socket operation today
tests:
- type: unit
  description: The starting combiner is seeded once
  input: a fresh database, two embedding models registered, two starts
  expected: one rrf-start row and one seeded decision; current is rrf-start
  tolerance: exact
- type: unit
  description: Adoption by ruling replaces the current combiner
  input: rrf-start current, seven models registered, adopt-combiner rrf-all-embedding-models with ruling words
  expected: current is rrf-all-embedding-models over seven labels; the decision names rrf-start as replaced and carries the ruling
  tolerance: exact
- type: invariant
  description: agent_memory:INV-008 registering never changes the current combiner
  input: register a candidate over one model while rrf-start is current
  expected: current still rrf-start
  tolerance: exact
logging_tier: CORE

##### M_8_C_3 — Held-Back Comparison
job: Decide whether a candidate combiner replaces the current one by scoring both on the same held-back judged cases.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- candidate_id: string — the proposed combiner
- memory_kind: string — optionally one memory kind to compare on
- cases: list of object — held-back, not-removed cases whose latest judgment names the right items, with every model's stored ranking
- artifacts: object — trained combiners supplied by agent_uagent_builder, by combiner id
outputs:
- outcome: object — decision, candidate_score, current_score, held_back_cases, reason, candidate, current_before, settings_used
artifacts:
- memory_combiner_decisions
data_contracts:
- memory_combiner_decisions
seam_contracts:
- M_9_A_3 -> M_8_C_3: the held-back judged cases (scorable_held_back) read from the ground-truth store
- M_8_C_3 -> agent_uagent_builder: an artifact combiner is handed the held-back inputs without judgments and returns one merged ranking per case
dependencies:
- M_8_C_1
- M_8_C_2
- M_8_B_1
invariants:
- agent_memory:INV-008
- agent_memory:INV-009
tunable_parameters:
- param_name: ranking.comparison_score
  default_value: "mean_reciprocal_rank"
  param_type: string
  description: The score both combiners are judged by; a stored setting with no code default.
- param_name: ranking.smallest_held_back_set
  default_value: "2"
  param_type: int
  description: The fewest shared held-back judged cases that may decide; a stored setting with no code default.
intelligence_pattern: none
logic: |
  1. Refuse with ValueError when no search settings are declared.
  2. Read the candidate and the current combiner; refuse when either is missing or they are the same.
  3. Take the held-back cases whose source was not removed and whose latest judgment is right or pick, optionally of one memory kind, keeping only those with a stored ranking for every model of both combiners, so both are scored on the very same cases.
  4. For a reciprocal-rank-fusion combiner, merge each case's stored model rankings with the merging constant. For an artifact combiner, hand it the cases (case id, memory kind, input, its models' rankings) without judgments; when its artifact is not supplied, keep the current combiner with that reason and no scores.
  5. Score both by the declared score: mean reciprocal rank (mean over cases of 1 over the rank of the first right item, 0 when none) or top-one accuracy (share of cases whose first item is right).
  6. With fewer cases than smallest_held_back_set, keep the current with the reason naming both counts. When the candidate scores strictly higher, adopt it. Otherwise keep the current with both scores in the reason.
  7. Append the decision row with the score name, both scores, the case count, the smallest set and the settings in force; never edit an earlier decision.
tools:
- duckdb
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_8_C_3
- candidate_score: float, TRAINING — the candidate's score
- current_score: float, TRAINING — the current combiner's score
- held_back_cases: integer, TRAINING — cases compared
- decision: string, CORE — adopted or kept_current
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Too few held-back judged cases -> kept_current with the counts (run: 0 found, 2 needed)
- Candidate equal or worse -> kept_current with both scores
- Artifact not supplied -> kept_current with the reason
- The comparison fuses without model weights, without the keyword ranking, without decay and without the second stage -> it judges the unweighted first stage only, not the order search answers with; a known gap
- No socket operation or command runs a comparison; no non-test caller of evaluate_combiner was found by searching the repository (an inference) -> a winning or losing comparison on real judged cases has not been run
- Learning the weights against hand-verified answers (planned with ranx's optimiser and qrels) and the decision model are not built
tests:
- type: unit
  description: A strictly better candidate is adopted
  input: three held-back cases judged right, candidate first-right ranks 1, 1, 1 against current 2, 1, 3, smallest set 2
  expected: decision adopted, candidate mean reciprocal rank 1.0 against about 0.611
  tolerance: numerical
- type: unit
  description: Too few cases keep the current
  input: one shared held-back case, smallest set 2
  expected: kept_current with "too few held-back judged cases: 1 found, 2 needed"
  tolerance: exact
- type: invariant
  description: agent_memory:INV-008 a tie never replaces the current combiner
  input: candidate and current with equal scores on five cases
  expected: kept_current; current combiner unchanged
  tolerance: exact
logging_tier: TRAINING

#### M_8_D — Context
purpose: Answer "what do you know that matters for this" as one prompt-ready block built from the same visible search.
inputs:
  - context requests (query, limit) from the socket
outputs:
  - the context text with a WAITING section, the waiting lines and the episode count
dependencies:
  - M_8_B_4
  - M_4_A_3
tests:
  - Tiers answering plain lists -> a block with EPISODES and a WAITING section
  - A ranked episodic tier -> the known KeyError, the client gets no reply

##### M_8_D_1 — Context Block Assembly
job: Turn a question's visible episodic or temporal hits plus the keyword graph search into one context block for a prompt, with a WAITING section.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- principal: object — the authenticated principal
- query: string — the question
- limit: integer — at least 1, the most hits per tier and per graph kind
outputs:
- context_answer: JSON object — status success, context, waiting and episodes
artifacts:
- context_answer.json
data_contracts:
- context_request.json
- context_answer.json
seam_contracts:
- M_11_A_1 -> M_8_D_1: the socket operation context
- M_8_D_1 -> M_8_A_2: the same retrieve the caller would get
- M_8_D_1 -> M_4_A_3: Graphiti keyword search (BM25 with rank fusion only) over the groups the caller may see
dependencies:
- M_8_A_1
- M_8_A_2
- M_8_B_4
- M_4_A_3
invariants:
- agent_memory:INV-010
- agent_memory:INV-007
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Refuse a query that is not text or a limit that is not an integer of at least 1 with "Invalid context request".
  2. Run the ordinary retrieve for the caller.
  3. Take the hits of the temporal tier, then the episodic tier, and read each involved event's recorded creation time.
  4. Turn each hit into a Graphiti episode: name the event id, group the tier, valid_at from the hit or else the event's creation time (no zone read as UTC), source description "<tier> memory; <the hit's ranking>", content from the hit's content or text.
  5. Work out the caller's allowed graph groups (M_8_A_1) and run Graphiti's keyword search over them with the question and limit; count the entities the graph holds.
  6. Waiting lines: while the graph holds no entity, "FACTS, ENTITIES and COMMUNITIES: waiting on a language model (Graphiti extracts them from episodes; none has been extracted yet)". For each tier whose status is not ok: with left-out models, "<tier>: waiting on numbers (<model>: <reason>; ...)"; with no retriever, "<tier>: not configured on this service"; otherwise "<tier>: <reason>".
  7. Render the episodes with the graph's facts, entities and communities through Graphiti's own search_results_to_context_string (facts with valid and invalid dates, entities, episodes, communities), then append "    <WAITING>", one indented line per waiting line, and "    </WAITING>".
  8. Answer status success, the context text, the waiting lines and the number of episodes.
  9. The project's older assemble_context in the context assembler is not this function, produces no date ranges, entities or summaries, and no caller of it was found by searching the repository (an inference).
tools:
- graphiti_core
- graphiti_duckdb
- duckdb_sqlalchemy_async
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_8_D_1
- episodes: integer, CORE — episodes placed in the block
- waiting: list of string, CORE — what could not be answered and why
- entity_count: integer, DEBUG — entities the graph holds
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Known defect, read from the code and not yet run: when the episodic or temporal tier ranks by numbers (true whenever any reader is fresh, as in the live service since 2026-10-27), its hits are rolled-up events carrying only event_id, score and items; reading the hit's ranking raises KeyError, which is outside the error kinds the socket handler answers, so the connection closes with no reply and the failure is not logged by the service; the content would also be empty, because content and text sit inside the items -> the context operation fails for every ranked question until the hits are read from their items
- Tiers answering plain lists (Letta's substring fallback, Graphiti's most-recent fallback) -> works; each hit carries its ranking label
- Malformed query or limit -> "Validation Error"
- Graph never written -> empty graph results and the language-model waiting line
- Graph facts from keyword search are filtered by the caller's groups only, not by the live-producer check -> a fact from an event forgotten but not yet processed out of the graph could appear (an inference, not measured)
tests:
- type: unit
  description: Plain-list tiers render with a WAITING section
  input: episodic and temporal tiers with no fresh reader, one visible event, relational with a stale model
  expected: an EPISODES entry for the event and a WAITING line "relational: waiting on numbers" naming the model and reason
  tolerance: semantic
- type: unit
  description: The known defect with ranked tiers
  input: the episodic tier ranking by a fresh reader, one visible matching event
  expected: today the connection closes with no reply; once fixed, the block holds the event's content
  tolerance: exact
- type: invariant
  description: agent_memory:INV-010 the block holds only what the caller may see
  input: two principals with one event each, graph groups for both
  expected: each block holds only its own principal's episode and group facts
  tolerance: exact
logging_tier: CORE

### M_9 — Ground Truth and Review
description: Keeps, append-only and permanently, the record from which every ranking can be judged: a ground-truth case for every search run whose readers disagree or whose question has a known answer, every reader's ranking with the keyword, merged and second-stage orders, the settings and reports of the run, the known-right answers to test questions, the owner's judgments made on the review screen, scoring views that say whether each ranking was right, and fingerprinted exports a training run can repeat exactly. A case points to events by id and never copies their text; a correction is a new row that supersedes, never an edit; a forgotten event hides its cases without deleting a row. By Shawn's ruling 13.1 (2026-10-27) memory collects ground truth now and builds no decision model: the decision model is designed only after agent_uagent_builder's prior-art research, and training belongs to agent_uagent_builder, never to memory. Status in the capability ledger: rows N7, N8 and N9 built 2026-10-20, not proven end to end; run 2026-10-27 (evening) through the live service: the eight Ada questions recorded as known answers by construction, and one batch of the eight recorded 41 cases with 392 rankings scored, read back through the service.
dependencies:
  - M_0
  - M_1
  - M_7
  - M_8
entry_conditions:
  - M_0, M_1, M_7 and M_8 are VERIFIED
  - test fixtures are prepared on a throwaway copy of the memory database
exit_conditions:
  - all submodules VERIFIED
  - all artifacts schema-valid
  - all tests passed
  - end-to-end module test passed
module_test: On a throwaway database with captured events, record a known answer for a question with record_known, ask that question with retrieve on a tier that ranks by models, read memory_gt_ranking_scores with ground_truth_table and confirm every recorded ranking of the case has a row with its first right rank, then select a case with review_select, judge it twice with review_judge and confirm the second judgment supersedes the first, and finally make a training export and confirm it verifies, then forget one event of a training case and confirm the verification reports that case as removed and the fingerprint no longer matches.

#### M_9_A — Ground Truth
purpose: Record what each search run ranked, what is known to be right, and say for every recorded ranking whether it was right, all append-only and limited to what the caller may see.
inputs:
  - a ranked tier's search run: question, tier, current combiner, every reader's filtered ranking, the merged, keyword and second-stage orders, the run report
  - record_known request over the local socket: query, answer, events, basis, said_by
  - ground_truth_table request over the local socket: table, limit
outputs:
  - rows in memory_gt_cases, memory_gt_case_events, memory_gt_outputs, memory_gt_runs, memory_gt_known
  - the views memory_gt_current_known, memory_gt_ranking_items, memory_gt_ranking_scores
  - ground_truth_case id (or ground_truth_error) carried in the search answer
  - known answer id; table rows with the count withheld
dependencies:
  - M_0_D_2
  - M_1_A_2
  - M_1_B_2
  - M_1_C_1
  - M_1_C_3
  - M_7_A_1
  - M_8_A_1
  - M_8_C_1
tests:
  - two readers rank different first items for a question with no known answer -> one case of kind model_choice is recorded with every reader's ranking, the combined order and a run record
  - every reader ranks the same first item and the question has no known answer -> nothing is recorded and the answer carries ground_truth_case null
  - a second record_known for the same question -> a new row that supersedes the first; the first row is unchanged
  - ground_truth_table on memory_gt_ranking_scores by a principal who may not see a case's events -> that case's rows are left out and counted in withheld

##### M_9_A_1 — Case Recorder
job: Record a ranked search run as a ground-truth case whenever its readers disagree on the first answer or its question has a known answer.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- tier: string — the memory kind searched: episodic, temporal, relational or personalization
- principal: authenticated principal — the caller, named from the operating system's peer credentials as os-uid-<uid>
- query: string — the search question, kept exactly as asked
- combiner: combiner row — the combiner current when the search ran
- kept: mapping of reader label to list of hits — each reader's ranking after the withholding filter, the label being model_id@revision
- stages: mapping — merged order (after decay) with scores, final order (after the second stage) with scores, keyword order or nothing, the second stage's report, and the run report
outputs:
- ground_truth_case: string or null — "case-" followed by a SHA-256 hex digest, or null when nothing needed recording or recording failed
- ground_truth_error: string or absent — the exception type and message when recording failed
artifacts:
- ground_truth_case.json
- ground_truth_output.json
- ground_truth_run.json
data_contracts:
- ground_truth_case.json
- ground_truth_output.json
- ground_truth_run.json
seam_contracts: []
dependencies:
- M_0_D_2
- M_1_B_2
- M_1_C_1
- M_7_A_1
- M_8_C_1
invariants:
- UMST:INV-001
- UMST:INV-003
- agent_memory:INV-005
- agent_memory:INV-006
- agent_memory:INV-009
- agent_memory:INV-011
tunable_parameters:
  - param_name: ranking.held_back_share
    default_value: "0.2"
    param_type: float
    description: The fraction of cases held back from training, strictly between 0 and 1; it fixes each new case's split once, at creation. A stored setting with no code default.
  - param_name: ranking.merging_constant
    default_value: "60"
    param_type: float
    description: The constant added to a rank in reciprocal rank fusion; read here only as part of the case identity, so a run under a different constant is a different case. A stored setting with no code default.
intelligence_pattern: none
logic: |
  1. At start the schema setup (M_0_D_2) runs this node's one ground-truth schema statement inside its start transaction. It creates, if absent, the tables memory_gt_cases, memory_gt_case_events (each row tied to a captured event by a foreign key), memory_gt_outputs, memory_gt_judgments, memory_gt_exports, memory_gt_known and memory_gt_runs, and the views memory_gt_current_known, memory_gt_ranking_items and memory_gt_ranking_scores. Nothing in these tables is ever updated or deleted.
  2. Answer assembly calls this node once per ranked tier per question, after the merge, the keyword ranking, decay and the second stage, and before the answer is returned. A tier that answers with a plain list (ranking by no model's numbers) is never recorded.
  3. If no search settings are declared, raise RuntimeError; the merge refuses that case earlier, so the search never reaches here without settings.
  4. Shape every reader's ranking for the record, for every label in kept, whether or not the current combiner used that reader: each item keeps item_id, event_id, item_kind (fact when the hit carries a fact, memory when it carries a memory, episode when it carries an episode id, other otherwise) and score (the hit's distance, or else its score). No text is copied.
  5. Read the current known answer for the exact question text (M_9_A_2). Decide disagreement: collect the first item id of every reader's non-empty ranking; the readers disagree when that set has more than one member, so fewer than two answering readers never disagree.
  6. If there is no known answer and the readers do not disagree, record nothing and return no case and no error.
  7. Derive the case id as "case-" followed by the SHA-256 of the JSON of: tier, question, combiner id, merging constant, every reader's item order, the merged order, the final order and the keyword order. The same question with the same orders under the same combiner always gives the same case id.
  8. Collect the involved events: the sorted, unique event ids of every kept hit.
  9. Shape the extra outputs: "combined" is the merged order with its scores; "words" is added only when a keyword ranking exists, its items with score null; "reranked" is added only when the second stage was applied, the final order with each candidate's cross_score.
  10. In one transaction on the owner's worker: record the case with kind model_choice, input kind question, memory kind the tier, created_at now (UTC), created_by the principal, and its split fixed from its id: the first eight hex digits of the SHA-256 of the case id, read as a fraction of 16 to the power 8, give held_back when below ranking.held_back_share and training otherwise. Record one "model" output per reader, splitting its label at the last "@" into model id and revision; the revision must be registered in the model registry, whose width is stored with it. Record each extra output under its role name with no model. Record the run: question, tier, time, the full search settings report, and the run report (models used, ranked but not combined, or left out with reasons; keyword report; decay report; second-stage report without its ordered list but with every candidate's cross-score; facts held back as no longer current, without their text; the count withheld). Commit; on any error roll the whole transaction back.
  11. Recording rules applied inside step 10: an unknown case kind or input kind is refused; an empty id or empty event list is refused; an event absent from the event store is refused; a case id already stored with the same content returns the stored case, and with different content is refused; an output already stored for the same case and key is a no-op when identical and refused when different; a run already stored for the case is a no-op when identical and refused when different.
  12. If the transaction raised ValueError, log it with its traceback and return no case with the error as "ValueError: <message>"; answer assembly puts ground_truth_error in the answer and the search still answers. Any other error is not caught here.
  13. Otherwise return the case id, which answer assembly reports as ground_truth_case.
  14. Reading a case back returns its fields, its events and source_removed, which is worked out at the moment of reading from the removal records (true when any of its events has been forgotten or expired through the owner); no case is rewritten when an event is forgotten. Listing case ids returns them in creation order, optionally of one split and one memory kind. Reading a case's outputs returns them models first in a stable order, optionally only those recorded at or before a given instant.
tools:
- duckdb
- hashlib
- json
- uuid
- logging
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_9_A_1
- case_id: string, TRAINING — the case recorded, or null
- recorded_reason: string, TRAINING — known_answer, disagreement, or nothing recorded
- outputs_recorded: integer, TRAINING — the number of output rows the case holds
- split: string, TRAINING — training or held_back, fixed at creation
- ground_truth_error: string, CORE — the recording failure returned in the answer, when one occurred
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- No known answer and the readers agree on the first item -> nothing is recorded; the answer carries ground_truth_case null and no error
- A reader's label is not a registered model revision -> ValueError, the transaction is rolled back, the answer carries ground_truth_error and still returns the search result
- The question has a known answer but every reader's ranking is empty after withholding -> ValueError "A case needs an id and at least one event", ground_truth_error in the answer, nothing written
- The same question is asked again with identical orders but different scores or settings, for example decay applied while an involved event is still young enough that its decay factor moves with the clock, or a changed rerank weight that leaves the orders unchanged -> the stored output or run differs, ValueError "already has a different output" or "already has a different run record", ground_truth_error in the answer and no new case (inferred from reading the code, not measured)
- A database error that is not a ValueError while writing -> the transaction is rolled back, the error escapes this node, and the whole tier's answer becomes failed with the reason, so that tier's search result is lost for that question
- An event of a recorded case is forgotten or expires later -> the case reads as source_removed true; its case, output, run and judgment rows all stay
- A run is recorded or skipped -> nothing is logged; only a recording failure is logged, so the decision itself leaves no log line
tests:
- type: unit
  description: A disagreement is recorded with every ranking
  input: two registered readers whose first items differ, no known answer for the question, keyword ranking and second stage applied
  expected: one case of kind model_choice, input kind question; one model output per reader; combined, words and reranked outputs; one run record; the answer carries ground_truth_case equal to the derived case id
  tolerance: exact
- type: unit
  description: Agreement with no known answer records nothing
  input: two readers with the same first item, no known answer
  expected: no new row in any ground-truth table; ground_truth_case null; no ground_truth_error
  tolerance: exact
- type: invariant
  description: agent_memory:INV-009 ground truth is append-only
  input: the same search run twice with identical orders and scores, then an attempt to record a different output for the same case and key
  expected: the second run returns the same case id and adds no row; the different output is refused with ValueError and the stored output is unchanged
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 forgetting hides without deleting
  input: a recorded case, then one of its events forgotten
  expected: reading the case gives source_removed true; the case, outputs and run rows are still present
  tolerance: exact
logging_tier: TRAINING

##### M_9_A_2 — Known Answers
job: Append the known-right answer to a test question, superseding any earlier answer to the same question.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- known_answer_request: JSON object — op record_known, query, answer, events, basis, said_by
- principal: authenticated principal — the caller from the operating system's peer credentials
outputs:
- known_answer_reply: JSON object — status success and the new known_id
artifacts:
- known_answer.json
- known_answer_reply.json
data_contracts:
- known_answer_request.json
- known_answer.json
- known_answer_reply.json
seam_contracts: []
dependencies:
- M_9_A_1
- M_1_A_2
- M_1_C_3
- M_8_A_1
invariants:
- UMST:INV-001
- agent_memory:INV-006
- agent_memory:INV-009
- agent_memory:INV-010
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Read query, answer, events, basis and said_by from the request. If query, answer, basis or said_by is not non-empty text, or events is not a non-empty list of text, raise ValueError "Invalid known answer: query, answer, basis, said_by (text) and events (list) required".
  2. Resolve the caller's visibility and read the live producers of the named events. If any event is unknown, forgotten, expired, or produced by someone the caller may not see, raise PermissionError "An event of the known answer is not visible to this principal"; nothing is written.
  3. In one transaction: check again that query, answer, basis, said_by and events are present; check that every unique event exists in the event store, else ValueError naming the event; read the current known answer for the exact question text; insert a new row with a fresh random known_id, the question, the answer in words, the sorted unique right events as JSON, the basis ("construction" for a set built to have one answer, or the reason for a correction), who said it is right, recorded_by the caller, recorded_at now (UTC), and supersedes set to the current known answer's id or null when there was none. Commit.
  4. Return status success with the new known_id.
  5. The current known answer for a question is read from memory_gt_current_known, which shows only known rows that no later row supersedes, matched on the exact question text. The case recorder (M_9_A_1) uses it to decide whether a run must be recorded, and the scoring view (M_9_A_3) scores rankings against it, so a correction re-scores every recorded ranking with no rewrite.
tools:
- duckdb
- json
- uuid
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_9_A_2
- known_id: string, CORE — the row appended
- supersedes: string, CORE — the known row this one replaces, or null
- right_event_count: integer, CORE — how many events state the answer
- recorded_by: string, CORE — the caller's identity
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- A field missing or of the wrong type -> error reply "Validation Error" with the reason, nothing written
- An event unknown, forgotten, expired, or not visible to the caller -> error reply "Visibility Refused", nothing written
- A second record for the same question -> a new row that supersedes the first; the first row stays and scoring reads only the new one
- The question text differs from a searched question by any character, spacing or letter case -> it is a different question, and no search of the other text is recorded or scored against it
tests:
- type: unit
  description: A known answer is recorded and then corrected
  input: record_known for a question with one visible event, then record_known for the same question with another visible event and basis naming the correction
  expected: two rows; the second supersedes the first; memory_gt_current_known shows only the second
  tolerance: exact
- type: invariant
  description: agent_memory:INV-009 a correction never edits
  input: the correction above
  expected: the first row's answer, events and recorded_at are unchanged
  tolerance: exact
- type: invariant
  description: agent_memory:INV-010 the caller's visibility constrains the record
  input: record_known naming an event produced by an agent the caller may not see
  expected: error reply "Visibility Refused" and no new row
  tolerance: exact
logging_tier: CORE

##### M_9_A_3 — Scoring Views and Table Access
job: Return one ground-truth table or scoring view to a caller, keeping only the rows whose events that caller may see.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- ground_truth_table_request: JSON object — op ground_truth_table, table, limit
- principal: authenticated principal — the caller from the operating system's peer credentials
- memory_kind: string or null — for the held-back scorable set only, the memory kind to limit it to
outputs:
- ground_truth_table_reply: JSON object — status success, table, columns, rows, withheld
- scorable_held_back: list — held-back judged cases, each with case id, memory kind, input, every reader's item order and the correct items
artifacts:
- ground_truth_table_reply.json
- ranking_score_row.json
data_contracts:
- ground_truth_table_request.json
- ground_truth_table_reply.json
- ranking_score_row.json
seam_contracts: []
dependencies:
- M_9_A_1
- M_9_A_2
- M_9_B_1
- M_1_A_2
- M_1_C_3
- M_8_A_1
invariants:
- UMST:INV-001
- agent_memory:INV-006
- agent_memory:INV-009
- agent_memory:INV-010
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. The view memory_gt_ranking_items flattens every output whose JSON is a list into one row per item: case, output key, role, model id, revision, rank (position plus one), item_id, event_id, item_kind, score and cross_score (null when the item has none).
  2. The view memory_gt_ranking_scores joins each case's ranking items to the current known answer whose question text equals the case's input, and gives per case and output: the question, the tier, the output key and role, the known answer's id and who recorded it, first_right_rank (the smallest rank whose item's event is one of the known answer's right events, null when none is), right_at_1 (whether that rank is 1, false when none is) and reciprocal_rank (one over that rank, 0 when none is). Memory computes nothing further from it; callers read it.
  3. On a ground_truth_table request, take table and limit. If table is not one of memory_gt_known, memory_gt_current_known, memory_gt_cases, memory_gt_case_events, memory_gt_outputs, memory_gt_runs, memory_gt_judgments, memory_gt_ranking_items or memory_gt_ranking_scores, or limit is not a positive whole number, raise ValueError naming the allowed tables.
  4. On the owner's worker, read every column and row of the table in the order the database returns them, and every case-to-event pair.
  5. For each row work out its events: the row's right_events when the table has that column; otherwise the events of the row's case when the table has a case_id column; otherwise none.
  6. Resolve the caller's visibility and the live producers of every event involved. Keep a row only when every one of its events is live (captured, not forgotten, not expired) and produced by someone the caller may see; count the others as withheld.
  7. Return status success, the table name, the column names, the first limit kept rows, and withheld (the count of rows left out for visibility; rows beyond the limit are not counted).
  8. For the held-back comparison (M_8_C_3), return the scorable held-back set: every held-back case, optionally of one memory kind, whose source was not removed and whose current judgment is right or pick, each with its case id, memory kind, input, every "model" output as label to item ids, and the chosen items as the correct set. A training case is never returned.
  9. This node never writes. By Shawn's ruling 13.1 (2026-10-27) these tables and views are exposed for a decision model and for automated model search, neither of which is built in memory.
tools:
- duckdb
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_9_A_3
- table: string, CORE — the table or view read
- rows_returned: integer, CORE — rows in the reply
- withheld: integer, CORE — rows left out for visibility
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- An unknown table or a limit that is not a positive whole number -> error reply "Validation Error" naming the allowed tables
- memory_gt_exports is requested -> refused as an unknown table; export manifests are not readable through this operation
- A case names a forgotten, expired or invisible event -> all of that case's rows are left out and counted in withheld
- A case's question has no current known answer -> the case has no row in memory_gt_ranking_scores
- A large table -> every row is read into the service before the limit is applied, on each call (inferred from reading the code, not measured)
- The rows come back in no declared order -> two calls may list rows in different orders
tests:
- type: unit
  description: Ranking scores are read through the socket
  input: a known answer for a question, a recorded case for it with three reader outputs, then ground_truth_table with table memory_gt_ranking_scores and limit 100
  expected: one row per output with first_right_rank, right_at_1 and reciprocal_rank as the known answer's events give them; withheld 0
  tolerance: exact
- type: unit
  description: Unknown table refused
  input: ground_truth_table with table memory_gt_exports
  expected: error reply "Validation Error"
  tolerance: exact
- type: invariant
  description: agent_memory:INV-010 rows follow the caller's visibility
  input: a recorded case whose events another agent produced, read by a caller who may not see that agent
  expected: the case's rows are absent and withheld counts them
  tolerance: exact
- type: invariant
  description: agent_memory:INV-009 a correction re-scores without rewriting
  input: a recorded case scored against a known answer, then a correcting known answer naming another event
  expected: memory_gt_ranking_scores changes to the corrected answer; no output row changed
  tolerance: exact
logging_tier: CORE

#### M_9_B — Review and Training
purpose: Let the owner judge cases beside their original text, and hand a training run elsewhere an exact, repeatable copy of the ground truth. Memory never trains and never runs a trained model; training belongs to agent_uagent_builder, and by Shawn's ruling 13.1 (2026-10-27) no decision model is built until agent_uagent_builder's prior-art research has been done.
inputs:
  - review_list, review_show, review_page, review_judge and review_select requests over the local socket
  - an export request from a Python caller holding the running service: purpose and who asks
outputs:
  - review lists, case views, an escaped HTML page, judgment ids, owner-selected case ids
  - export manifests with their content and fingerprint; export verification results
dependencies:
  - M_9_A_1
  - M_1_A_2
  - M_1_C_3
  - M_1_D_1
  - M_8_A_1
  - M_8_C_1
tests:
  - review_list by the owner after a disagreement is recorded -> the case is listed with disagreement true and judged false
  - review_judge twice on one case -> two judgment rows, the second superseding the first
  - review_show for a case whose event was forgotten -> error reply "Visibility Refused"
  - a training export -> no held-back case in it, and verification matches until an event of one of its cases is forgotten

##### M_9_B_1 — Review Screen and Judgments
job: Serve the review screen through which a principal lists, views, selects, or judges the cases it may see.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- review_request: JSON object — op review_list, review_show, review_page, review_judge or review_select, with the fields that operation needs
- principal: authenticated principal — the caller from the operating system's peer credentials
outputs:
- review_list_reply: JSON object — status success and the cases to review
- review_view_reply: JSON object — status success and the case view
- review_page_reply: JSON object — status success and the whole HTML page as text
- judgment_reply: JSON object — status success and the new judgment_id
- review_select_reply: JSON object — status success and the case_id
artifacts:
- review_list_reply.json
- review_view.json
- review_page_reply.json
- judgment.json
data_contracts:
- review_request.json
- review_list_reply.json
- review_view.json
- review_page_reply.json
- judgment.json
- ground_truth_case.json
seam_contracts: []
dependencies:
- M_9_A_1
- M_1_A_2
- M_1_C_3
- M_1_D_1
- M_8_A_1
- M_8_C_1
invariants:
- UMST:INV-001
- agent_memory:INV-006
- agent_memory:INV-009
- agent_memory:INV-010
tunable_parameters:
  - param_name: ranking.held_back_share
    default_value: "0.2"
    param_type: float
    description: The fraction of cases held back from training; it fixes the split of every case the owner selects with review_select. A stored setting with no code default.
intelligence_pattern: none
logic: |
  1. A case is visible to a principal only when its source was not removed and every event it points to is live (captured, not forgotten, not expired) and produced by someone the principal's resolved visibility permits.
  2. review_list: read every case with its outputs and current judgment. Keep a case only when its readers disagree (more than one distinct first item among its non-empty "model" outputs) or the owner selected it, and only when it is visible. Each entry gives case_id, kind, memory_kind, input_kind, created_at, disagreement, selected_by_owner, judged and the current verdict (null when unjudged). Cases the principal may not see are absent, not blanked.
  3. review_select: take question, events and memory_kind. If question is not non-empty text, events is not a non-empty list of text, or memory_kind is not one of episodic, temporal, relational or personalization, raise ValueError "review_select needs question, events and a memory_kind". If no search settings are declared, raise ValueError saying the held-back share comes from them. If any event is not live or not visible, raise PermissionError. Derive the case id as "case-" followed by the SHA-256 of the JSON of the caller, the memory kind, the question and the sorted unique events, and record the case (M_9_A_1 rules) with kind retrieved_item, input kind question, selected_by_owner true, created_by the caller and its split fixed from ranking.held_back_share. Asking again returns the same case. Return its case_id.
  4. review_show, review_page and review_judge need case_id as non-empty text, else raise ValueError "A review request needs a case_id".
  5. review_show: read the case, its outputs and every judgment (superseded ones included); an unknown case raises ValueError "Unknown case"; a case not visible raises PermissionError. Load the original text of every event the case points to and of every event any ranked output names. Return the view: the case, the original text of each of its events, every answer (output key, role, combiner id, its items each beside the original text of its event, and its class scores when the output is scores rather than a ranking), every judgment, and whether the readers disagree.
  6. review_page: build the same view and render it as one complete HTML page, every value escaped: the original text on the left, each answer beside it, the judgments below. The page is returned as text in the reply's html field over the socket; there is no HTTP route.
  7. review_judge: chosen_items defaults to an empty list and must be a list of text, else ValueError "chosen_items must be a list of item ids". If the case is not visible, raise PermissionError. In one transaction, append the judgment: the verdict must be right, wrong, pick, typed or none_correct; the case must exist; right and pick need at least one chosen item; typed needs the typed answer; when the case has any ranked output, every chosen item must be an item one of them names. The new row has a fresh random judgment_id, judged_by the caller, judged_at now (UTC), the chosen items, the typed answer and the note, and supersedes the case's current judgment (or null). Commit and return the judgment_id; on any error roll back.
  8. The current judgment of a case is the last, in time order, of its judgments that no later judgment supersedes.
  9. This node never edits a case, an output or an earlier judgment, never trains and never runs a model.
tools:
- duckdb
- html
- hashlib
- json
- uuid
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_9_B_1
- operation: string, CORE — the review operation answered
- case_id: string, CORE — the case shown, judged or selected
- judgment_id: string, CORE — the judgment appended, when one was
- verdict: string, CORE — right, wrong, pick, typed or none_correct
- listed: integer, CORE — cases returned by review_list
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- The case's source was removed or an event is not visible to the caller -> error reply "Visibility Refused", nothing shown or written
- An unknown case id -> error reply "Validation Error" with "Unknown case"
- An unknown verdict, a right or pick verdict without chosen items, or a typed verdict without the typed answer -> error reply "Validation Error", nothing written
- A chosen item that no recorded ranked output names -> error reply "Validation Error", nothing written
- An owner-selected case with no recorded outputs -> any chosen item ids are accepted unchecked, because there is no output to check them against
- A case recorded only because its question has a known answer while its readers agreed -> not listed by review_list; it is still shown by review_show with its id and read through ground_truth_table
- review_select while no search settings are declared -> error reply "Validation Error", nothing written
- A stored event whose bytes fail verification while the view is built -> the event loader's error is returned as an error reply and no view is shown
- Many cases -> review_list reads every case with its outputs on each call; it has no limit and no paging (inferred from reading the code, not measured)
tests:
- type: unit
  description: A disagreement is listed and shown beside the original
  input: a recorded case whose two readers rank different first items, review_list then review_show by the owner
  expected: the case listed with disagreement true and judged false; the view gives each event's original text and each answer's items beside their events' text
  tolerance: exact
- type: unit
  description: A second judgment supersedes the first
  input: review_judge with verdict pick and one chosen item, then review_judge with verdict typed and a typed answer
  expected: two judgment rows; the second's supersedes is the first's id; review_list shows verdict typed
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 a forgotten item is never shown
  input: a listed case, then one of its events forgotten, then review_list and review_show
  expected: the case is absent from the list and review_show answers "Visibility Refused"; its rows remain stored
  tolerance: exact
- type: invariant
  description: agent_memory:INV-010 another principal's case is refused
  input: review_judge on a case whose events were produced by an agent the caller may not see
  expected: error reply "Visibility Refused" and no judgment row
  tolerance: exact
logging_tier: CORE

##### M_9_B_2 — Training Exports
job: Produce a fingerprinted export of the ground truth that a training run elsewhere can repeat exactly.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- purpose: string — training or held_back_inputs
- created_by: string — who asks for the export
- export_id: string — for verification, the export to check again
outputs:
- export_manifest: JSON object — export_id, purpose, case_ids, fingerprint, created_at, created_by, content
- export_verification: JSON object — matches, removed, fingerprint_now
artifacts:
- export_manifest.json
- export_verification.json
data_contracts:
- export_manifest.json
- export_verification.json
- ground_truth_case.json
- ground_truth_output.json
- judgment.json
seam_contracts: []
dependencies:
- M_9_A_1
- M_9_B_1
invariants:
- UMST:INV-001
- agent_memory:INV-006
- agent_memory:INV-008
- agent_memory:INV-009
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. A Python caller holding the running service asks for an export with a purpose and who asks; the time is taken as now (UTC) and the whole export runs in one transaction on the owner's worker.
  2. If the purpose is neither training nor held_back_inputs, raise ValueError "Unknown export purpose".
  3. Choose the cases: for training, every training-split case; for held_back_inputs, every held-back case; in both, leave out every case whose source was removed.
  4. Build the content per case, carrying only outputs recorded and judgments made at or before the export time. A training export carries the whole case (with its events, split and source_removed), all its outputs with the combiner current at the time, and every judgment. A held_back_inputs export carries only the case id, memory kind, input kind, input, events and the "model" outputs: no judgment, no combined, keyword or second-stage order, so a builder can merge the held-back inputs without seeing what was judged right.
  5. Fingerprint the content as the SHA-256 of its canonical JSON (keys sorted, no spaces).
  6. Append the manifest row: a fresh random export_id, the purpose, the sorted case ids, the fingerprint, created_at and created_by. Return the manifest with its content. The content is returned to the caller and is not written to any file.
  7. Verification of an earlier export: if the export id is unknown, raise ValueError. Otherwise rebuild the content of its stored case ids as of its stored creation time, and return whether the fingerprint still matches, the case ids whose source has since been removed, and the fingerprint now. A removed source changes the content, so repeating that run needs a new export; nothing is hidden.
  8. Training itself, and every decision model trained from these exports, belong to agent_uagent_builder. Memory never trains and never runs a trained model; a trained combiner comes back as an artifact that is compared on held-back cases (M_8_C_3). By Shawn's ruling 13.1 (2026-10-27), memory collects ground truth now and its decision model is designed only after agent_uagent_builder's prior-art research, so no decision model is built.
tools:
- duckdb
- hashlib
- json
- uuid
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_9_B_2
- export_id: string, TRAINING — the manifest appended
- purpose: string, TRAINING — training or held_back_inputs
- case_count: integer, TRAINING — cases in the export
- fingerprint: string, TRAINING — SHA-256 of the canonical content
- matches: boolean, TRAINING — for a verification, whether the content is unchanged
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- An unknown purpose -> ValueError, nothing written
- An unknown export id on verification -> ValueError
- An event of an exported case is forgotten after the export -> verification returns matches false and names the case in removed; a repeat needs a new export
- A judgment made after the export -> it is not in the rebuilt content, so verification still matches
- A caller asks over the socket -> no socket operation or command reaches exports today, so the socket answers "Unsupported operation"; only a Python caller holding the running service can export, as the 2026-10-20 proof did
- A training run or decision model is wanted -> none exists in memory; by ruling 13.1 the design waits on agent_uagent_builder's prior-art research, and training belongs to agent_uagent_builder
tests:
- type: unit
  description: A training export never carries a held-back case
  input: cases of both splits, some judged, then an export with purpose training
  expected: case_ids lists only training cases whose source was not removed; the content carries their outputs and judgments; one manifest row appended
  tolerance: exact
- type: unit
  description: A held-back export carries no judgment
  input: judged held-back cases, then an export with purpose held_back_inputs
  expected: each content entry has case id, memory kind, input kind, input, events and model outputs only
  tolerance: exact
- type: invariant
  description: agent_memory:INV-008 held-back judgments never reach training
  input: any export of either purpose
  expected: no held-back case's judgment appears in any export content
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 a forgotten source is reported, never hidden
  input: a training export, then one of its cases' events forgotten, then verification
  expected: matches false, the case named in removed, and every stored row still present
  tolerance: exact
logging_tier: TRAINING

### M_10 — Growth Capabilities
description: The capabilities the capability ledger added on 2026-10-24 that none of Letta, Graphiti, Cognee, Mem0 or Zep CE has, read as prior art in EverAlgo (EverMind-AI/EverAlgo at 3ec80a8, EverOS 5b0979b, Apache-2.0): foresight, the case-to-skill pipeline, and language-model boundary detection. Each is to be translated faithfully from its source file at that commit, citing it, with every threshold the source carries declared as a stored setting. Nothing in this module is built: no code, no table, no setting and no socket operation exists for any of its nodes, and the ledger marks all three Open and unproven. Each node below specifies what it must do and states what the service does today when asked.
dependencies:
  - M_0
  - M_1
  - M_2
entry_conditions:
  - M_0, M_1 and M_2 are VERIFIED
  - the source files of the three EverAlgo extractors at 3ec80a8 are read and cited
  - test fixtures are prepared on a throwaway copy of the memory database
exit_conditions:
  - all submodules VERIFIED
  - all artifacts schema-valid
  - all tests passed
  - end-to-end module test passed
module_test: On a throwaway database, capture a chat that changes topic once and one finished agent task, then confirm that boundary detection groups the chat's messages into two cells at the change, that foresight records at least one dated effect for the first episode with the model that answered, and that the agent task becomes a recorded case with a quality score that adds a skill. Today this test cannot pass because nothing is built; every request for these capabilities answers "Unsupported operation".

#### M_10_A — Foresight and Skills
purpose: Carry the three EverAlgo capabilities memory lacks: dated predictions of an episode's future effects, skills learned from recorded agent cases, and chats divided into memory cells by a language model. The 2026-10-24 ledger row for Zep Cloud's closed remainder (its own extraction, reranking and embedding models, its dashboards, its SOC 2 and Konig work, and the Go worker for user summaries, of which only the prompt is public, mirrored in Graphiti) is REA territory by the owner's ruling of 2026-10-24: it is not translated by faithful copy, no node carries it, and what of Zep is open is carried by M_4.
inputs:
  - captured events loaded with their verified bytes
  - the language model named by the language_model settings, reached through the model-step router
outputs:
  - dated foresight records pointing to their episode
  - recorded agent cases with quality scores, and skills with confidence and maturity
  - memory cells listing the events of each cell
dependencies:
  - M_0_B_1
  - M_1_D_1
  - M_2_B_1
tests:
  - a request today for any of the three capabilities over the socket -> error reply "Unsupported operation" naming the operation
  - a stored setting today under a key for any of the three -> set-setting refuses it because the settings validator refuses keys it does not know
  - once built, a chat that changes topic once -> two cells, each a list of whole events

##### M_10_A_1 — Foresight Extractor
job: Predict the dated future effects of one captured episode on the user's behaviour.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- episode: captured event — one episode loaded with its verified bytes, visible under its producer's scope
outputs:
- foresight_effects: list — each predicted effect on the user's behaviour with its start, its end and its duration, pointing to the episode by event id, with the model that actually answered
artifacts: []
data_contracts: []
seam_contracts: []
dependencies:
- M_0_B_1
- M_1_D_1
- M_2_B_1
invariants:
- agent_memory:INV-004
- agent_memory:INV-005
- agent_memory:INV-006
- agent_memory:INV-011
- agent_memory:INV-012
tunable_parameters: []
intelligence_pattern: event_driven
logic: |
  1. Not built. When built, it is translated faithfully from EverAlgo's foresight extractor at commit 3ec80a8, citing the source file, with every threshold or bound the source carries declared as a described stored setting with no code default.
  2. Take one captured episode loaded with its verified bytes (M_1_D_1). If the episode has been forgotten or has expired, produce nothing for it.
  3. Outside any write transaction, ask the language model named by the language_model settings, through the model-step router (M_2_B_1), with the source's foresight prompt, for the episode's future effects on the user's behaviour.
  4. Read each effect with its start, its end and its duration as the source defines them. If the model fails, or its reply cannot be read as effects each carrying all three, record the step as failed with the reason and store no effect.
  5. In one write transaction after the model call, store each effect pointing to its episode by event id, never copying the episode's text, with the model that actually answered.
  6. When the episode is later forgotten or expires, its effects are withheld from every answer; nothing is deleted.
tools:
- duckdb
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_10_A_1
- event_id: string, CORE — the episode read
- effects_recorded: integer, CORE — effects stored for the episode
- model_answered: string, CORE — the model that actually answered
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Nothing is built: no code, table, setting or socket operation exists for foresight -> a socket request naming any foresight operation gets the error reply "Unsupported operation" with the operation's name, and no foresight is ever produced
- A setting for foresight is offered today -> set-setting refuses it, because the settings validator refuses any key it does not know, and nothing is written
- No maintenance-loop step runs foresight -> captured episodes are processed by the four tiers only
- Once built, the model fails or answers with an effect missing its start, end or duration -> the step is recorded as failed with the reason and no effect is stored
tests:
- type: unit
  description: Today a foresight request is refused
  input: a socket request with op foresight
  expected: error reply "Unsupported operation" with details foresight
  tolerance: exact
- type: unit
  description: Once built, an episode yields dated effects
  input: one captured episode describing a planned change in routine, with the language model answering
  expected: at least one stored effect with a start, an end and a duration, pointing to the episode, with the model that answered
  tolerance: semantic
- type: invariant
  description: agent_memory:INV-012 the model call runs outside any write transaction
  input: a foresight step while the model takes longer than one capture
  expected: captures proceed during the model call; the effects commit after it with the answering model recorded
  tolerance: exact
logging_tier: CORE

##### M_10_A_2 — Case-to-Skill Pipeline
job: Maintain a set of agent skills learned from recorded agent cases.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- agent_case: captured events — one finished task an agent carried out, pointed to by event id
outputs:
- recorded_case: record — the agent case with its quality score, or a refusal naming the gate bound it failed
- skill_changes: list — skills added, updated or retired, each with its confidence, its four-dimension maturity score, and whether it is verified or a hypothesis made from a failed case
artifacts: []
data_contracts: []
seam_contracts: []
dependencies:
- M_0_B_1
- M_1_D_1
- M_2_B_1
invariants:
- agent_memory:INV-004
- agent_memory:INV-005
- agent_memory:INV-006
- agent_memory:INV-011
- agent_memory:INV-012
tunable_parameters: []
intelligence_pattern: event_driven
logic: |
  1. Not built. When built, it is translated faithfully from EverAlgo's agent case extractor and agent skill extractor at commit 3ec80a8, citing the source files, with the gate bounds and every score threshold declared as described stored settings with no code default.
  2. Take one finished agent case from its captured events, loaded with their verified bytes (M_1_D_1).
  3. Apply the source's length-aware gate: if the case falls outside the declared length bounds, do not record it, and record the refusal naming the bound.
  4. Otherwise score the case's quality as the source does, with any language-model step run outside any write transaction through the model-step router (M_2_B_1), and record the case with its score and the model that actually answered, pointing to its events by id and copying no text.
  5. For each recorded case, take the source's skill decisions: add a new skill, update an existing one, or retire one, each with its confidence; act only when the confidence passes the declared threshold for that action, and record every decision not acted on with its confidence.
  6. Score each skill's maturity on the source's four dimensions and record the score.
  7. From a failed case, make a skill marked verified or hypothesis by the source's rule.
  8. A retired skill is marked retired by a new row; no row is deleted. A case whose events are forgotten or expire is withheld from every answer.
tools:
- duckdb
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_10_A_2
- case_recorded: boolean, CORE — whether the case passed the gate
- quality_score: float, CORE — the case's quality score
- skill_action: string, CORE — add, update, retire or none
- confidence: float, CORE — the confidence behind the action
- model_answered: string, CORE — the model that actually answered
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Nothing is built: no code, table, setting or socket operation exists for agent cases or skills -> a socket request naming any such operation gets the error reply "Unsupported operation" with the operation's name, and no case or skill is ever recorded
- A setting for the gate or a score threshold is offered today -> set-setting refuses it, because the settings validator refuses any key it does not know, and nothing is written
- Once built, a case outside the gate's bounds -> not recorded; the refusal names the bound
- Once built, a skill decision below its threshold -> not acted on; the decision and its confidence are recorded
- Once built, the model fails -> the step is recorded as failed with the reason and nothing about the case's skills changes
tests:
- type: unit
  description: Today a skill request is refused
  input: a socket request with op skills
  expected: error reply "Unsupported operation" with details skills
  tolerance: exact
- type: unit
  description: Once built, a case below the gate is not recorded
  input: an agent case shorter than the declared lower length bound
  expected: no case recorded; the refusal names the bound
  tolerance: exact
- type: invariant
  description: agent_memory:INV-011 every threshold is a stored setting
  input: the built node's source
  expected: no gate bound, confidence threshold or maturity threshold written in code; each read from a described settings row
  tolerance: exact
logging_tier: CORE

##### M_10_A_3 — Language-Model Boundary Detection
job: Divide a chat's captured messages into memory cells at the topic changes a language model names.
capability_tag: local
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- chat: list of captured events — the messages of one chat in capture order, each loaded with its verified bytes
outputs:
- memory_cells: list — each cell the list of event ids of the consecutive whole messages it holds, with the model that actually answered
artifacts: []
data_contracts: []
seam_contracts: []
dependencies:
- M_0_B_1
- M_1_D_1
- M_2_B_1
invariants:
- agent_memory:INV-004
- agent_memory:INV-005
- agent_memory:INV-006
- agent_memory:INV-011
- agent_memory:INV-012
tunable_parameters: []
intelligence_pattern: event_driven
logic: |
  1. Not built. When built, it is translated faithfully from EverAlgo's boundary detection extractor at commit 3ec80a8, citing the source file, with every threshold the source carries declared as a described stored setting with no code default.
  2. Take a chat's messages as captured events in capture order (M_1_D_1), leaving out any forgotten or expired message.
  3. Outside any write transaction, ask the language model named by the language_model settings, through the model-step router (M_2_B_1), with the source's boundary prompt, where the topic changes.
  4. Place each boundary between two whole messages, never inside one, so no event's text is cut (agent_memory:INV-004).
  5. If the model fails, or names a boundary that is not between two of the chat's messages, record the step as failed with the reason and form no cell.
  6. In one write transaction after the model call, record each cell as the list of its event ids, copying no text, with the model that actually answered.
  7. A cell whose events are all forgotten or expired is withheld from every answer; a forgotten event is withheld from its cell; nothing is deleted.
tools:
- duckdb
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_10_A_3
- messages_read: integer, CORE — messages in the chat
- cells_formed: integer, CORE — memory cells recorded
- model_answered: string, CORE — the model that actually answered
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Nothing is built: no code, table, setting or socket operation exists for boundary detection -> a socket request naming any such operation gets the error reply "Unsupported operation" with the operation's name, and no chat is ever divided into cells
- A setting for boundary detection is offered today -> set-setting refuses it, because the settings validator refuses any key it does not know, and nothing is written
- Once built, the model fails or names a boundary inside a message -> the step is recorded as failed with the reason and no cell is formed
tests:
- type: unit
  description: Today a boundary request is refused
  input: a socket request with op boundaries
  expected: error reply "Unsupported operation" with details boundaries
  tolerance: exact
- type: unit
  description: Once built, a chat that changes topic once gives two cells
  input: six captured messages, three about travel then three about a budget, with the language model answering
  expected: two cells of three event ids each, in capture order, with the model that answered
  tolerance: semantic
- type: invariant
  description: agent_memory:INV-004 memory never cuts text
  input: any chat divided into cells
  expected: every cell is a list of whole events; no stored text is split or copied
  tolerance: exact
logging_tier: CORE

### M_11 — Service Interface
description: Answers every request that reaches the memory service from outside: the nineteen operations on its Unix socket, and the health door over HTTP.
dependencies:
  - M_0
  - M_1
  - M_2
  - M_3
  - M_4
  - M_5
  - M_6
  - M_7
  - M_8
  - M_9
entry_conditions:
  - M_0 to M_9 are VERIFIED
exit_conditions:
  - all submodules VERIFIED
  - all tests passed
module_test: With the service running on a throwaway database, send one framed request for each of the nineteen socket operations and confirm each answers status success or a named error, never silence; then GET /health returns 200 with status ok.

#### M_11_A — Socket and Health
purpose: Route each socket request to its operation with a named answer, and report whether the service is up.
inputs:
  - socket request: framed JSON object with op
  - HTTP GET /health
outputs:
  - socket reply: JSON object with status success or error
  - HTTP 200 JSON health report
dependencies:
  - M_1_A_2
  - M_0_C_1
tests:
  - capture of a valid event -> success with created true
  - an unknown op -> error "Unsupported operation"
  - GET /health while running -> 200 ok

##### M_11_A_1 — Socket Operation Dispatcher
job: Route each socket request to the one operation it names, replying with that operation's answer or a named error.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
language: python
inputs:
- request: JSON object — one decoded framed request from M_1_A_2, holding op and that operation's fields
- payload_content_hex: string — optional; the capture payload's bytes as hexadecimal
- principal: AuthenticatedPrincipal — "os-uid-<uid>" with scope "restricted", from the operating system's peer credentials
outputs:
- reply: JSON object — status "success" with the operation's fields, or status "error" with message and details; written without a length prefix, then the connection closes
artifacts:
- memory_socket_request.json
- memory_socket_reply.json
data_contracts:
- memory_socket_request.json
- memory_socket_reply.json
seam_contracts:
- capture -> M_1_B_2; forget -> M_1_C_1; status and load -> M_1_D_1; record_claim and record_get -> M_1_B_3.
- retrieve and retrieve_batch -> M_8_A_2 with the answer of M_8_B_4; context -> M_8_D_1.
- record_known -> M_9_A_2; ground_truth_table -> M_9_A_3; review_list, review_show, review_page, review_judge and review_select -> M_9_B_1.
- models -> M_7_A_1 and M_7_A_3; model_parts and model -> M_2_B_1.
- Visibility for every check below -> M_8_A_1.
dependencies:
- M_1_A_1
- M_1_A_2
- M_1_B_2
- M_1_B_3
- M_1_C_1
- M_1_D_1
- M_2_B_1
- M_7_A_1
- M_7_A_3
- M_8_A_1
- M_8_A_2
- M_8_B_4
- M_8_D_1
- M_9_A_2
- M_9_A_3
- M_9_B_1
invariants:
- UMST:INV-003
- agent_memory:INV-002
- agent_memory:INV-006
- agent_memory:INV-007
- agent_memory:INV-009
- agent_memory:INV-010
tunable_parameters: []
intelligence_pattern: event_driven
logic: |
  1. Before the caller is identified: op is the request's "op", and a missing op means capture. payload_content_hex, when present, is decoded to bytes; bad hexadecimal replies "Malformed Hex Payload". An op that is not one of the nineteen names replies "Unsupported operation" with the op as details. capture without a non-empty event replies "Missing 'event' in payload".
  2. Identify the caller (M_1_A_2); when that fails its error is already sent and nothing runs. The request body never names the principal. With the stored pool label "shared", a peer-credential caller (scope "restricted") is never in the pool, so every caller sees only the events its own operating-system user captured, and every process of one user is one principal (from reading the code).
  3. Run the named operation (steps 6 to 24). Errors map to replies: PermissionError -> "Visibility Refused"; ValueError (including an unknown event, a malformed field, and no language model configured) -> "Validation Error"; RuntimeError, TimeoutError, ConnectionError or a database error -> logged with its traceback, then "Internal Error" with "<type>: <message>". Every error reply is {status "error", message, details}.
  4. On success write the reply as JSON, wait for it to drain, and close the connection.
  5. Every success reply carries status "success". The fields per operation follow.
  6. capture — in: event (a version 1 or version 2 envelope, M_1_B_1) and optional payload_content_hex. The principal overwrites agent_id (version 1) or actor_agent_id (version 2). The event, its verified bytes and one pending receipt per tier are committed on a connection of its own; a racing identical capture takes the repeat path. Out: event (as stored, with tier_receipts) and created (true for the call that wrote it, false for an identical repeat). Processing then starts in the background.
  7. retrieve — in: query (text) and limit (an integer of at least 1). Out: tiers, one answer per tier (episodic, temporal, relational, personalization). A ranked tier answers status ok or partial, hits (events with score and items, cut to limit), combiner, models {used, ranked_not_combined, left_out with reasons}, settings_used, withheld, decay, words, merged_order, rerank, not_current, rankings per model, ground_truth_case and, when recording failed, ground_truth_error. A plain-list tier answers status ok, hits, withheld, combiner null and a combiner_note. A tier with no engine answers status unavailable with a reason; a tier whose read raised answers status failed with the reason; both with empty hits.
  8. retrieve_batch — in: queries (a non-empty list of text) and limit. Out: answers, one {query, tiers} per query in order, each exactly as retrieve answers; each model is asked for the numbers of all questions at once.
  9. record_known — in: query, answer, basis and said_by (non-empty text) and events (a non-empty list of event ids). Every event must be live and visible, otherwise PermissionError. Out: known_id. A later record for the same question supersedes; nothing is edited.
  10. ground_truth_table — in: table (memory_gt_known, memory_gt_current_known, memory_gt_cases, memory_gt_case_events, memory_gt_outputs, memory_gt_runs, memory_gt_judgments, memory_gt_ranking_items or memory_gt_ranking_scores) and limit (a positive integer). Out: table, columns, rows (in storage order, keeping only rows whose events are all live and visible, then cut to limit) and withheld (how many were left out). It never writes.
  11. forget — in: event_id. Only the event's producer may forget it, otherwise PermissionError. The removal is captured durably and withheld from retrieval at once. Out: event_id (the removal event), removed_by, receipts and complete (true only when every tier's receipt reports a processed removal). Processing of the removal then starts in the background.
  12. status — in: event_id. For an owner-written removal the principal must be allowed to see the removed event. Out: event_id, removed_by, receipts and complete.
  13. models — in: nothing. Out: models, one entry per registered model with model_id, revision, width, kind, supplier_collection, supplier_recipe, registered_at, numbers_table, fresh, reasons, rows, stale_rows, missing_pieces, derived_fresh, derived_reasons, derived_rows and derived_missing; and numbers_owed, the pending number requests without their text. No visibility check is made.
  14. context — in: query and limit. Out: context (Graphiti's context string built from the temporal and episodic hits and a keyword search of the graph groups the principal may see, followed by a WAITING block), waiting (the same lines as a list: facts, entities and communities waiting on a language model when no entity exists, and each tier that is not ok, as waiting on numbers with each left-out model, not configured, or its reason) and episodes (the count).
  15. load — in: event_id. Out: found false for an unknown event; otherwise found true, event (the stored envelope with its current receipts) and payload_text (the original bytes as UTF-8). Not visible -> PermissionError; bytes that are not UTF-8 -> ValueError. A forgotten or expired event must be withheld here as from every answer; today load checks no removal and returns it (stated in failure_modes).
  16. record_claim — in: key, kind, writer and content (non-empty text), optional episode and episode_source (text). Out: created (true only for the first call with that key for this principal) and record (record_key, kind, writer, content, episode, episode_source, created_at, as stored). The record is the router's own note, not a memory.
  17. record_get — in: key. Out: found and record (the principal's record, or null).
  18. model_parts — in: nothing. Out: requested {main, second}, main_model_placement and parts {name: {steps, achieved_model}}. It calls no model.
  19. model — in: part (graphiti, cognee, letta, zep or mem0), step, args (an object) and opinion ("main" by default, or "second"). Every args.event_ids entry must be visible; for graphiti and zep, group_id and graph_group_id must be groups the principal may see; agent_id and foreground_agent_id, and user_id for mem0, must be visible actors. Out: the step's own output plus part, step, opinion, requested_model, achieved_model, placement and seconds. A failure of the model call becomes RuntimeError naming the cause; nothing is stored when a step fails.
  20. review_list — in: nothing. Out: cases, each {case_id, kind, memory_kind, input_kind, created_at, disagreement, selected_by_owner, judged, verdict}, only cases the principal may see.
  21. review_show — in: case_id. Out: view {case, original (event id to text), answers (each key, role, combiner_id, items with their original text, scores), judgments, disagreement}.
  22. review_page — in: case_id. Out: html, the same view as an escaped page.
  23. review_judge — in: case_id, verdict (right, wrong, pick, typed or none_correct), chosen_items (a list of item ids), typed_answer and note. Out: judgment_id; a new row that supersedes the earlier judgment.
  24. review_select — in: question (text), events (a non-empty list) and memory_kind (one of the four tiers). Needs the declared ranking settings for the held-back share, otherwise ValueError. Every event must be live and visible. Out: case_id, "case-" followed by the SHA-256 of the principal, the kind, the question and the sorted events, so asking again queues one case.
  25. Trusted entry points (register_model, sync_numbers, propose_combiner, evaluate_combiner, export_ground_truth, verify_ground_truth_export, process_owed, enforce_expiry and rebuild_projection) are never reachable from the socket.
tools:
- asyncio
- json
- duckdb
- graphiti_core
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_11_A_1
- op: string, CORE — the operation asked for
- principal: string, CORE — os-uid-<uid>
- reply_status: string, CORE — success or error
- error_message: string, CORE — the error's message when status is error
- processing_time_ms: float, CORE — time from the decoded request to the written reply
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- Unknown op -> "Unsupported operation" with the op as details, before the caller is identified
- capture without an event -> "Missing 'event' in payload"
- Bad hexadecimal payload -> "Malformed Hex Payload"
- A visibility refusal -> "Visibility Refused" with the reason
- A malformed field, unknown event, unknown table, no language model configured, or review_select without ranking settings -> "Validation Error" with the reason
- A runtime, timeout, connection or database failure -> logged with traceback, "Internal Error" naming its type
- Any other exception (KeyError, TypeError, AttributeError; for example a request that is valid JSON but not an object fails when op is read) -> no reply, the connection closes, and the service logs nothing itself (gap against UMST:INV-003)
- context while a tier ranks by numbers -> probable KeyError reading the hit's "ranking" key, so no reply (likely defect, from reading the code, not measured; the context block is M_8_D_1)
- models -> any authenticated caller sees every registered model and the owed number requests with their event ids, text removed (visibility gap)
- model -> the visibility checks do not cover Cognee's text steps or Zep's user_id, project_uuid and session_id (visibility gap)
- load of a forgotten or expired event -> still answers found true with its original text to a principal permitted to see its producer, because loading checks no removal; the model operation likewise accepts removed events in event_ids (gap against agent_memory:INV-006, from reading the code, not measured)
- Background processing after capture or forget fails -> logged by the processing task; the reply already sent shows the receipts as pending and status shows the outcome later
tests:
- type: unit
  description: Capture then status
  input: a valid version 2 event with its payload bytes as hex, then status for its id
  expected: capture answers created true with four pending receipts; status answers the same event's receipts and complete false until processed
  tolerance: exact
- type: unit
  description: An unknown operation is named
  input: op "erase"
  expected: {status error, message "Unsupported operation", details "erase"}
  tolerance: exact
- type: unit
  description: A malformed retrieve is refused
  input: op retrieve with limit 0
  expected: {status error, message "Validation Error"} naming query and limit
  tolerance: exact
- type: invariant
  description: agent_memory:INV-010 the principal comes from the transport
  input: a capture whose actor_agent_id claims "someone-else" sent by a process of uid 1000
  expected: the stored event's actor_agent_id is "os-uid-1000"
  tolerance: exact
- type: invariant
  description: agent_memory:INV-007 a search never answers an empty success
  input: retrieve on a service with no number source and one ranked tier
  expected: that tier answers status unavailable with every model in left_out with its reason, never status ok with empty hits
  tolerance: exact
- type: invariant
  description: agent_memory:INV-006 forget withholds without deleting
  input: forget by the producer, then retrieve and load of that event by that producer
  expected: retrieve no longer returns the event, load withholds it (today it still returns the text: the known gap stated in failure_modes), and its memory_payloads row is still present in the file
  tolerance: exact
logging_tier: CORE

##### M_11_A_2 — Health Door
job: Answer GET /health on the claimed address with the service's state in words.
capability_tag: tier_6
gpu_mutex_required: false
always_resident: false
serves_http: true
language: python
inputs:
- request_line: string — the first line of the HTTP request
- service_state: OwnerService — whether its socket server exists, its paths and its language-model parts
- registration_note: string — from M_0_C_2
outputs:
- health_report: JSON object — status, service, socket, database, parts, agent_id and registration
- http_status_code: integer — 200, 400, 404 or 405
artifacts:
- health_report.json
data_contracts:
- health_report.json
seam_contracts:
- Bound on the address claimed by M_0_C_1; the report function is defined by the command router (M_0_A_2) over the built service.
dependencies:
- M_0_C_1
- M_0_A_2
invariants:
- UMST:INV-003
- agent_memory:INV-011
tunable_parameters: []
intelligence_pattern: none
logic: |
  1. Read one request line, waiting at most 5 seconds. The wait must come from a stored setting; no such setting exists today and 5 is a number written in code (known gap against agent_memory:INV-011).
  2. Decode the line as Latin-1 and split it into words. Fewer than two words: reply 400 {"error": "not an HTTP request"}.
  3. A method other than GET: reply 405 {"error": "only GET /health"}.
  4. A path other than /health (anything after "?" is ignored): reply 404 {"error": "only GET /health"}.
  5. Otherwise reply 200 with the report: status "ok" when the owner's socket server exists, else "starting"; service "sovereign_memory"; socket (the socket path); database (the database path); parts (the sorted names of the language-model parts, empty when none); agent_id "agent_sovereign_memory"; registration (the latest registration note).
  6. Every reply is HTTP/1.1 with Content-Type application/json, its Content-Length and Connection: close; headers and body of the request are never read. The connection is closed afterwards in every case.
  7. The door opens only after the socket serves and closes before the owner stops, so in the code's order it answers "ok"; "starting" appears only if asked before the socket server exists.
  8. "ok" means only that the socket server exists: the report says nothing about the database, the maintenance rounds, the number source or the language model.
  9. The agent_id must be agent_memory by the ruling of 2026-10-28; today the report says agent_sovereign_memory.
tools:
- asyncio
- json
shared_imports: []
metadata_fields:
- node_id: string, CORE — M_11_A_2
- http_status_code: integer, CORE — the code returned
- reported_status: string, CORE — ok or starting
memory_routing:
  emits_memory_events: false
  memory_tiers: []
  memory_event_types: []
failure_modes:
- No request line within 5 seconds, or the client disconnects -> warning "A health request ended before it was answered", no reply, connection closed
- Malformed request line -> 400, no crash
- Unknown path -> 404; other method -> 405
- A request line longer than the stream reader's limit -> the reader's error is not caught by the door, so no reply; the connection is closed and the error is left to asyncio's own handler (from reading the code, not measured)
- No health address given -> no door listens, while the registration still advertises GET /health (known gap)
- The 5 second read bound is written in code (known gap)
tests:
- type: unit
  description: Health while running
  input: GET /health HTTP/1.1
  expected: 200 with status ok, service sovereign_memory, the socket and database paths, parts and registration
  tolerance: exact
- type: unit
  description: Other paths and methods are refused
  input: GET /ready and then POST /health
  expected: 404 and then 405, each {"error": "only GET /health"}
  tolerance: exact
- type: invariant
  description: UMST:INV-003 the door never crashes the service
  input: a request line of one word
  expected: 400 returned and the socket still answering
  tolerance: exact
logging_tier: CORE

## BUILD ORDER

Build sequence (topological sort):
1. M_0_B_1 — Settings Loader and Validator (no dependencies)
2. M_0_B_2 — Settings Writer and Page (depends on: M_0_B_1)
3. M_0_C_1 — Port Claim (no dependencies)
4. M_0_C_2 — Registration and Heartbeat (depends on: M_0_C_1)
5. M_0_D_1 — Asynchronous DuckDB Dialect (no dependencies)
6. M_0_D_2 — Core and Search Schema Setup (depends on: M_0_D_1)
7. M_0_B_3 — Engine and Service Builder (depends on: M_0_B_1, M_0_D_2)
8. M_0_A_3 — Service Lifecycle and Maintenance Loop (depends on: M_0_B_3, M_0_C_1, M_0_C_2)
9. M_0_A_2 — Command Router (depends on: M_0_B_1, M_0_B_2, M_0_B_3, M_0_C_1, M_0_A_3)
10. M_0_A_1 — Entry Script and Interpreter Check (depends on: M_0_A_2)
11. M_1_A_1 — Socket Open and Close (depends on: M_0_D_1, M_0_D_2)
12. M_1_A_2 — Request Reader and Caller Identity (depends on: M_1_A_1)
13. M_1_B_1 — Event Contract Validator (no dependencies)
14. M_1_B_2 — Durable Capture (depends on: M_1_B_1, M_1_A_2, M_0_D_2)
15. M_1_B_3 — Router Bookkeeping Records (depends on: M_1_A_2, M_0_D_2)
16. M_1_D_1 — Event Loader and Receipt Status (depends on: M_1_A_2, M_0_D_2)
17. M_1_C_1 — Forget (depends on: M_1_B_2, M_1_D_1)
18. M_1_C_2 — Expiry Enforcement (depends on: M_1_C_1)
19. M_1_C_3 — Live Producers (depends on: M_1_C_2)
20. M_2_A_1 — Processor Registry (depends on: M_0_D_1)
21. M_2_A_2 — Owed Work Processor (depends on: M_2_A_1, M_1_B_2, M_1_D_1, M_1_C_3)
22. M_2_A_3 — Projection Rebuild (depends on: M_2_A_2, M_1_C_2)
23. M_2_B_1 — Model Step Router (depends on: M_0_B_1, M_1_D_1, M_1_A_2)
24. M_3_A_1 — Memory Blocks and Editing Tools (depends on: M_0_D_1)
25. M_3_A_2 — Recall and Conversation Search (depends on: M_0_D_1, M_3_A_1)
26. M_3_A_3 — Archival Passages (depends on: M_0_D_1, M_3_A_1)
27. M_3_A_4 — Git Memory Repository and Shared Repositories (depends on: M_3_A_1)
28. M_3_B_2 — Compaction (depends on: M_2_B_1, M_3_A_1, M_3_A_2)
29. M_3_B_3 — Sleep-Time Consolidation and Dreaming (depends on: M_2_B_1, M_3_A_1, M_3_A_2, M_3_B_2)
30. M_4_A_1 — DuckDB Graph Driver and Operations (depends on: M_0_D_1)
31. M_4_C_1 — MCP Server (13 memory tools) (depends on: M_4_A_1)
32. M_5_A_1 — Graph and Vector Adapters (depends on: M_0_D_1)
33. M_5_A_2 — Relational, Cache and Permissions (depends on: M_0_D_1, M_5_A_1)
34. M_5_B_1 — Cognee Chunk Tier (depends on: M_1_B_2, M_1_C_1, M_2_A_1, M_5_A_1)
35. M_5_B_2 — Graph Extraction and Entity Description Rewrite (depends on: M_2_B_1, M_5_A_1, M_5_A_2)
36. M_5_B_3 — Temporal Events and Query Windows (depends on: M_2_B_1, M_5_A_1, M_5_A_2, M_5_B_2)
37. M_5_B_4 — Search Types, Loaders, Importers and Evaluation (depends on: M_5_A_1, M_5_A_2, M_5_B_1, M_5_B_2, M_5_B_3)
38. M_6_A_1 — DuckDB Vector Store and History (depends on: M_0_D_1)
39. M_6_A_2 — Mem0 Tier (received numbers) (depends on: M_1_B_2, M_1_C_1, M_1_C_2, M_2_A_1, M_6_A_1)
40. M_6_B_1 — Fact Extraction and Action Decisions (depends on: M_2_B_1, M_6_A_1, M_6_A_2)
41. M_6_B_2 — Graph Triplets and Delete Decisions (depends on: M_2_B_1, M_6_A_2)
42. M_6_B_3 — Procedural Memory (depends on: M_2_B_1, M_6_A_2)
43. M_7_A_1 — Model Registry (depends on: M_0_D_2)
44. M_7_A_2 — Numbers Receiver (depends on: M_7_A_1, M_1_B_2)
45. M_7_A_3 — Owed Numbers and Sync (depends on: M_7_A_1, M_7_A_2)
46. M_7_B_1 — Embedding Door Supplier (depends on: M_7_A_3, M_1_D_1)
47. M_7_B_2 — Corpus Supplier (depends on: M_7_A_3, M_1_D_1)
48. M_7_B_3 — Search-Time Numbers (depends on: M_7_A_1, M_7_A_2, M_7_A_3)
49. M_3_B_1 — Letta Recall Tier (depends on: M_0_D_1, M_1_B_1, M_1_C_1, M_2_A_1, M_7_B_3, M_3_A_2)
50. M_4_A_2 — Graphiti Episode Tier (depends on: M_1_B_1, M_1_C_1, M_2_A_1, M_7_B_3, M_4_A_1)
51. M_4_B_1 — Ingest (depends on: M_2_B_1, M_7_A_3, M_4_A_1, M_4_A_2)
52. M_4_A_3 — Keyword Graph Search (depends on: M_4_A_1, M_4_A_2, M_4_B_1)
53. M_4_B_2 — Contradiction and Invalidation (depends on: M_4_B_1)
54. M_4_B_3 — Communities and Saga Summaries (depends on: M_2_B_1, M_4_A_1, M_4_A_2)
55. M_4_D_1 — Zep CE Store (users, sessions, messages, relevant facts) (depends on: M_2_B_1, M_4_A_3)
56. M_4_D_2 — Thread and User Summaries (depends on: M_2_B_1, M_4_A_1, M_4_D_1)
57. M_8_A_1 — Visibility (depends on: M_1_A_2)
58. M_8_A_2 — Tier Retrieval and Withholding (depends on: M_8_A_1, M_1_C_3, M_2_A_3, M_7_B_3, M_3_B_1, M_4_A_2, M_5_B_1, M_6_A_2)
59. M_8_C_1 — Search Settings Record (depends on: M_0_B_1)
60. M_8_C_2 — Combiner Registry and Adoption by Ruling (depends on: M_7_A_1, M_8_C_1)
61. M_8_B_1 — Per-Model Merge (depends on: M_8_A_2, M_8_C_1, M_8_C_2, M_7_B_3)
62. M_8_B_2 — Recency Decay (depends on: M_8_B_1, M_8_C_1)
63. M_6_B_4 — Platform Behaviours (depends on: M_4_B_2, M_4_D_2, M_5_B_3, M_6_B_1, M_8_B_2)
64. M_8_B_3 — Second-Stage Cross-Scorer (depends on: M_8_B_1, M_8_B_2, M_8_C_1, M_7_B_3)
65. M_8_B_4 — Answer Assembly (depends on: M_8_A_2, M_8_B_1, M_8_B_2, M_8_B_3)
66. M_8_C_3 — Held-Back Comparison (depends on: M_8_C_1, M_8_C_2, M_8_B_1)
67. M_8_D_1 — Context Block Assembly (depends on: M_8_A_1, M_8_A_2, M_8_B_4, M_4_A_3)
68. M_9_A_1 — Case Recorder (depends on: M_0_D_2, M_1_B_2, M_1_C_1, M_7_A_1, M_8_C_1)
69. M_9_A_2 — Known Answers (depends on: M_9_A_1, M_1_A_2, M_1_C_3, M_8_A_1)
70. M_9_B_1 — Review Screen and Judgments (depends on: M_9_A_1, M_1_A_2, M_1_C_3, M_1_D_1, M_8_A_1, M_8_C_1)
71. M_9_A_3 — Scoring Views and Table Access (depends on: M_9_A_1, M_9_A_2, M_9_B_1, M_1_A_2, M_1_C_3, M_8_A_1)
72. M_9_B_2 — Training Exports (depends on: M_9_A_1, M_9_B_1)
73. M_10_A_1 — Foresight Extractor (depends on: M_0_B_1, M_1_D_1, M_2_B_1)
74. M_10_A_2 — Case-to-Skill Pipeline (depends on: M_0_B_1, M_1_D_1, M_2_B_1)
75. M_10_A_3 — Language-Model Boundary Detection (depends on: M_0_B_1, M_1_D_1, M_2_B_1)
76. M_11_A_1 — Socket Operation Dispatcher (depends on: M_1_A_1, M_1_A_2, M_1_B_2, M_1_B_3, M_1_C_1, M_1_D_1, M_2_B_1, M_7_A_1, M_7_A_3, M_8_A_1, M_8_A_2, M_8_B_4, M_8_D_1, M_9_A_2, M_9_A_3, M_9_B_1)
77. M_11_A_2 — Health Door (depends on: M_0_C_1, M_0_A_2)

Module completion order:
M_0 -> M_1 -> M_2 -> M_3 -> M_4 -> M_5 -> M_6 -> M_7 -> M_8 -> M_9 -> M_10 -> M_11
