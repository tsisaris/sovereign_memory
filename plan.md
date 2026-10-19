1. **Understand Initial State & Blocker Analysis:**
   - Investigate components in `packages/`: `cognee_duckdb`, `duckdb_sqlalchemy_async`, `graphiti_duckdb`, `letta_memory_model`, `mem0_duckdb`.
   - Goal indicates:
     - "Fix missing Mem0 manifest, empty Cognee closure, Graphiti maintained fork completeness, Letta real ORM/Pydantic models, and actual cross-provider transaction/deletion/prepared-work interfaces."
     - Letta: `packages/letta_memory_model/pyproject.toml` is needed? Letta classes have not proved real SQLAlchemy ORM/Pydantic behavior. (Failures: plainBlock failed Session.add and plain schemas lack model_validate).
     - Cognee: `NodeDeleteData` constructor uses wrong names (`id/type` instead of `node_id/node_type`). "empty Cognee closure".
     - Mem0: `packages/mem0_duckdb` missing `pyproject.toml` (I just created it!). "missing Mem0 manifest".
     - Graphiti: "maintained fork completeness". "unbounded pins and no full maintained fork".
     - Owner service: `envelope_stub` in expiry is not working expiry. Fails actual processors.

2. **Mem0 Manifest:**
   - Check `packages/mem0_duckdb`. I created a basic `pyproject.toml`. Let's ensure it has exactly the right dependencies for DuckDB vectors (mem0 depends on duckdb/duckdb-engine).
   - "missing Mem0 manifest". Solved initially by creating `packages/mem0_duckdb/pyproject.toml` and linking it in root. Wait, I should double check the mem0 dependencies to see if there are any missing. I must retain `MEM0_TELEMETRY` disabled.

3. **Cognee Closure & NodeDeleteData:**
   - In `packages/cognee_duckdb/pyproject.toml`, fill the actual dependencies used by Cognee (duckdb, sqlalchemy, pydantic, networkx?).
   - Find `NodeDeleteData` and change `id` -> `node_id` and `type` -> `node_type`.

4. **Graphiti Maintenance:**
   - Investigate `packages/graphiti_duckdb/pyproject.toml` and ensure bounded dependencies and fork completeness.

5. **Letta ORM/Pydantic:**
   - Investigate `packages/letta_memory_model` for Pydantic failures (`plainBlock failed Session.add` and `plain schemas lack model_validate`).
   - Add true SQLAlchemy/Pydantic models for Letta. Check Letta ORM files.

6. **Owner Service:**
   - `envelope_stub` in expiry. Investigate `owner` service / `memory_owner_schema.py` or similar. Replace envelope stub with real expiry logic.
   - Processor seams.

7. **Pre-commit Checks:**
   - `uv run pre-commit run --all-files`
   - Address failures. Make sure `ruff`, `mypy`, `pyright`, `pylint` pass.

8. **Submit:**
   - Draft PR for Sol to review.

