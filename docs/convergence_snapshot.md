# Materialized integration source — unfinished

This revision contains the actual runtime source, maintained upstream packages, tests, resources and pinned workspace metadata. Do not apply the historical source patch under docs/late_artifacts: it is archival provenance and would overwrite newer fixes.

This is an integration input, not accepted software, a main merge, a release or a deployment. All seven deliverables and original32 capabilities remain required, including DuckDB-only storage, permissive licensing, identity/permissions, corrections/deletion/expiry, transaction/retry, search freshness, telemetry absence and real local-model quality.

## Source and setup

The source is materialized from DATA parent0037a0c4d8f328ca5baee0e58300dadbfed0cf37 plus the preserved complete3af4 source delta and subsequent local runtime repairs. Original component branches, failed returns and full local binary exports remain preserved. Historical snapshot narrative is retained in docs/late_artifacts/convergence_snapshot_before_source_materialization_20261018.txt.

The declared workspace contains six canonical packages: the async DuckDB dialect, Cognee1.6.5 fork, Graphiti0.31.1 source in graphiti-duckdb, Mem02.2.1 fork with1.0.11 compatibility decisions, Letta memory model, and sovereign-memory-owner. Use Python3.12 and the unchanged setup owner:

    UV_NO_EDITABLE=1 bash jules_setup.sh

The noneditable profile avoids a real editable Letta tests-package collision when collecting Graphiti's upstream tests. Source-aware package cache keys prevent stale local wheels. Use the declared environment; no shared/ad-hoc installs. UV_OFFLINE=1 is suitable only when its required packages are already cached, not for an empty cloud environment.

## What has actually worked

- Foundation: same-handle transactions/rollback/cancellation; native index and foreign-key reflection; owned/borrowed connection behavior. The shared native-open boundary rejects exact SQLite headers before opening and rejects a borrowed current SQLite catalog without committing or closing it. Native unprefixed shared-file interoperability is preserved. This is not whole-source SQLite-excision proof.
- Cognee: all48 maintained upgrades; basic users/dataset grants/revocation; graph/vector dataset separation; cache APIs; maintained BROAD native record-store operations. Metadata-only prune now drops verified Cognee relational tables in one transaction and preserves the shared file, graph/vector/cache, Graphiti, Mem0 history and owner events/receipts. Exact schema/head/constraints are checked first; unknown inbound foreign keys or user views refuse reset. Older/custom schemas and warm SDK reset-cache behavior remain open.
- Graphiti: real public entity/episode and entity/episodic-edge storage, persistence, group scope, rollback and provenance-aware episode removal. Four unchanged upstream node/edge tests pass. Search and other families remain incomplete.
- Mem0: native vector/history storage and rollback. The compatibility flag now selects maintained two-phase fact extraction then scoped ADD/UPDATE/DELETE/NONE execution through current mutation helpers, with custom legacy prompt fields restored. The canonical update prompt is unchanged; the fake replacement prompt is an alias. This decision flow has no real-model end-to-end proof.
- Letta: Git-backed files/history and real outage/retry/synchronization recovery. Actual ORM/Pydantic memory-block foundations remain incomplete.
- Owner: authenticated local capture, immutable envelope conflicts, durable events and four PENDING receipts, restart and unchanged-input retry. There are no functioning four-engine processors or integrated retrieval yet; ContextAssembler formats supplied lists. Pending rows do not prove processing.

## Current evidence and failures

The required root pytest passes ONE driver table test (latest1.76s); it is not whole-system coverage. Root deptry scans TWO files and passes; package dependency checks are separate. Four authentic Graphiti node/edge oracles pass0.36s after the native-open guard. Native-open/bridge focused files pass all seven configured hooks. Scoped reset/owner retry were exercised through ordinary public APIs on throwaway copied databases, including close/reopen and retained other-engine rows.

The retained Mem0 suite reports5failures/4passes/10errors (4.83s before refusal guard). Old SQLite fixtures create empty or SQLite files; the ADD-only fake LLM emits the wrong first-phase shape for the restored fact pipeline. These are not proof of ADD/UPDATE/DELETE/NONE decisions. No tests were deleted, skipped or rewritten to create green results.

Full source checks remain RED: prior all-files Ruff15599 reports and many type errors; authentic intentional negative checker inputs; four inherited Bandit shell findings. Mem0's focused files still report87Ruff findings and13type errors after correcting an introduced redeclaration. Graphiti imports four existing type errors. No checker configuration, diagnostic suppression or main acceptance condition is weakened.

The unfinished source checkpoint publication is authorized as input to cloud integration despite those failures. It is separate from main/release approval and does not waive defects. The original one-time checkpoint hook bypass, if needed to create this source checkpoint, is scoped to that publication only and must be recorded, never installed as policy.

Real local-model runs are deferred because no models were selected. Do not pull arbitrary models, use cloud-model fallback, invent fake completions, or claim telemetry/SQLite absence without observed proof.

## Integration work

The user's updated route is: preserve all available account Jules/GitHub artifacts, reconcile only sovereign_memory-relevant code, publish this actual source, then compare useful broad Jules and Claude Cloud work on the same exact base and reconverge. Preserve unrelated projects without merging them into this suite. Count account-wide active jobs against the15 limit; unused slots are not evidence that duplicate work is useful.

Sol owns root/lock/shared contracts and consolidation. Provider assignments need exclusive files and a verified starting commit. Reuse each session for corrections. The next useful behavior is real public capture → actual engine projection → truthful completion receipt → scoped retrieval → restart/retrieval, expanded to all four engines. Child packages must preserve real borrowed native transaction ownership and current identity/metadata protections.

Do not add custom tests/harnesses, manufacture placeholder data or receipts, replace native operations with a Cypher string recognizer, erase existing tests, adopt another engine's namespace, unlink the shared file, silently convert old SQLite files, weaken gates, deploy or merge main. Use existing deterministic checks, authentic upstream tests and ordinary application operations; report exact failures and missing implementation.
