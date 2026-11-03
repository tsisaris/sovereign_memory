# How an agent sends memory, and how it reads it back

Shawn (2026-10-22 17:39:48): "Memory, everything that should be remembered is kept by memory, and memory has to have access to everything that should be remembered in order to remember it." This page is the one way in. Every example below was run against the service; the output is saved in `~/portable/projects/architect/reports/memory_done_proof_20261102/`.

## The doorway

One Unix socket, named by the service setting `service.socket_path` (live: `~/portable/projects/sovereign_memory/local/service/sovereign_memory.sock`). One request per connection: 4 bytes of length (big-endian), then that many bytes of JSON with an `"op"`. The reply is one JSON object with `"status"` `"success"` or `"error"`. Who you are comes from the socket (your operating-system user), never from a field you send: the service writes your identity into the event itself. By default you can read only what you sent; the shared pool and grants are the service's visibility settings.

The ecosystem's own client is `ServiceDoor` (and `MemoryRouter.shared_save`, `shared_retrieve`, `shared_search`) in `~/sovereign/shared/shared_memory_router.py`. Use it. The raw form is shown last, for programs that cannot import it.

## Sending something: one event

Call (through the client):

    router = MemoryRouter()   # settings from the parameter owner, as every agent already does
    router.shared_save(
        actor_agent_id="agent_incubator",      # who produced it (recorded as provenance)
        event_id="incubator-ruling-2026-11-02-0001",   # your own stable id; see rule 1
        content="Ruling: the Bergen note outranks the Oslo note for the manager question.",
        evidence="ruling file reports/ruling_0001.md, line 12",   # where the content comes from
        source="agent_incubator/M_2_A_2",      # the program that made it
        kind="correction",                     # event type, free text (see list below)
        entities=["Ada Lovelace", "Bo Eriksen"],
        valid_from=when_it_became_true, known_at=when_you_learned_it,   # datetimes
        confidence=0.9,
    )

What the service stores for that call (the event): `event_id` (yours, with the actor folded in), `event_type` (your `kind`), `created_at` (your `known_at`), the payload `{"messages": [{"role": "user", "content": <content>}], "claim": {writer, evidence, source, kind, entities, valid_from, known_at, confidence}}` and the hash of those exact bytes. The reply says `created: true` the first time and `created: false` when the identical event was sent again.

Rules:

1. **The id is yours and must be stable.** Sending the same id with the same bytes again is harmless (it answers `created: false`). The same id with different bytes is refused. Derive the id from what you are recording (a ruling number, a file and line), never from the clock.
2. **Say when it happened.** `created_at` is the time the thing happened or was learned, in ISO 8601 with a zone. Searches that carry `since`, `until` or `when` use it.
3. **Send the original text.** Memory keeps your bytes as the permanent record and cuts, summarises and numbers them itself. Send no vectors; a vector with a save is refused.
4. **Event types** (free text; these are the ones the project documents use): `fact`, `observation`, `correction`, `preference`, `inference`, `calibration_measured`, `validator_judgment_accepted`. A type starting `system.` is refused (it is the service's own).
5. **Plain conversations** go as `{"messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}` in the payload; plain text is also accepted but the tier that extracts personal facts (Mem0) needs the messages form.

## Sending something to the archival memory (Letta)

Event type `archival_memory`, payload `{"archival_memory": {"text": "...", "tags": ["a", "b"]}}`. The text is stored as a tagged passage and found by meaning and by tag. Example (`archival_tags_kelp.txt`): tags `["prize", "kelp"]` found with `tags: ["kelp"]`.

## Taking it back out

    router.shared_retrieve(query="Where does Ada work now?", limit=5)
    router.shared_retrieve(query="...", limit=5, when="last week", tags=["kelp"], tag_match="any")
    router.shared_search(search_type="RAG_COMPLETION", query="...", limit=3)       # an answer, written by the model
    router.shared_search(search_type="CHUNKS_LEXICAL", query="...", limit=5)       # by words, no model

Narrowing words (all optional, on retrieve, context and search): `since`, `until` (ISO 8601), `when` (a phrase: "last week", "yesterday", "3 days ago") with `reference_date`, `tags` with `tag_match` ("any" or "all"). Search types: CHUNKS, CHUNKS_LEXICAL, SUMMARIES, RAG_COMPLETION, GRAPH_COMPLETION, TEMPORAL and the others listed in the service's reply to an unknown type. Each tier answers separately; a tier that could not answer says why and is never an empty success.

To remove something you sent: `{"op": "forget", "event_id": "<the stored id>"}` (only the sender may). To see whether every memory kind has finished with an event: `{"op": "status", "event_id": "..."}`; a kind waiting on numbers says so.

## The raw form (no import)

    import hashlib, json, socket
    payload = json.dumps({"messages": [{"role": "user", "content": "Ada drinks coffee now."}]}).encode()
    receipt = {"event_id": ID, "processor_id": "unknown", "processor_version": "0.0.0",
               "projection_version": "0.0.0", "status": "pending", "attempt_id": None,
               "attempted_at": None, "completed_at": None, "error_ref": None}
    event = {"schema_version": "2.0.0", "event_id": ID, "actor_agent_id": "ignored-replaced-by-the-socket",
             "event_type": "fact", "created_at": "2026-11-02T20:00:00+00:00", "source_artifact_id": None,
             "work_id": None, "parent_event_id": None, "causation_event_id": None,
             "source_revision": "my_program/1", "recipe_version": None, "model_occupant_id": None,
             "program_version": None, "payload_ref": "inline", "payload_sha256": hashlib.sha256(payload).hexdigest(),
             "outcome": "recorded", "exactness": "observed",
             "tier_receipts": {t: dict(receipt) for t in ("episodic", "temporal", "relational", "personalization")}}
    body = json.dumps({"op": "capture", "event": event, "payload_content_hex": payload.hex()}).encode()
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(SOCKET_PATH); s.sendall(len(body).to_bytes(4, "big") + body); s.shutdown(socket.SHUT_WR)
        reply = json.loads(b"".join(iter(lambda: s.recv(1 << 20), b"")))

The required envelope fields and their types are in `packages/sovereign_memory_owner/service/schemas/memory_event_schema.json`; all four receipts are required and must be `pending`.

## What happens after you send

The reply returns at once with the event stored. In the background each memory kind processes it (recall messages, an episode, a chunk, personal facts); the supplier's numbers for it are fetched from the Embedding door by the service's own maintenance, one model at a time. Until the numbers arrive the event is found by words only and a search says which models it left out and why. Nothing you send needs a model of yours.
