# Task Board

1. **Cognee graph on DuckDB**
   - Upstream interface: `GraphDBInterface` (53 defs, 21 abstract) in `cognee/infrastructure/databases/graph/graph_db_interface.py`
   - Template: `graph/turso/adapter.py` (1,052 lines, 41 methods)
   - Oracle test suite: `test_graph_provenance_adapter_contract.py`
   - Proof statement: 20 contract tests pass with `GRAPH_DATABASE_PROVIDER=duckdb` and `VECTOR_DB_PROVIDER=duckdb`, run inside one process with no subprocess workers.

2. **Asyncio SQLAlchemy dialect for DuckDB**
   - Upstream interface: SQLAlchemy's asyncio dialect contract
   - Template: Cognee's `infrastructure/databases/turso/` package (892 lines, 7 files), `aioduckdb` 0.1.0 bridge
   - Oracle test suite: `test_SqlAlchemyAdapter.py` and 48 Alembic migrations
   - Proof statement: all 48 migrations apply and roll back on a DuckDB file, and the adapter tests pass.

3. **Cognee relational, cache and the BROAD record store onto DuckDB**
   - Upstream interface: branch in `create_relational_engine.py`, 32 dialect-specific lines in `SqlAlchemyAdapter.py`, `SqlCacheAdapter.py`, BROAD record store
   - Template: `sqlite3` driver patterns for the record store
   - Oracle test suite: `test_shared_schema_isolation.py` and earlier suites
   - Proof statement: a full `remember → recall → improve → forget` cycle passes with no `.db` SQLite file created.

4. **Graphiti DuckDB backend as a maintained fork**
   - Upstream interface: `GraphDriver` and `GraphDriverSession` (9 abstract methods) + 11 operations protocols (97 methods)
   - Template: Kuzu backend (3,169 lines), Neo4j/FalkorDB routing tests, PR #1777 PostGraph driver
   - Oracle test suite: `tests/test_graphiti_int.py`, `test_node_int.py`, `test_edge_int.py`, `test_entity_exclusion_int.py`, `tests/utils/maintenance/*`, `tests/utils/search/*`
   - Proof statement: the integration suite passes; `GRAPHITI_TELEMETRY_ENABLED=false` is verified; LongMemEval runs for quality.

5. **Mem0 on DuckDB, with the removed capabilities restored**
   - Upstream interface: `VectorStoreBase` (11 abstract methods), `SQLiteManager` (12 defs)
   - Template: `pgvector.py` (563 lines), `storage.py`
   - Oracle test suite: `tests/memory/test_storage.py`
   - Proof statement: the 12 real-file tests pass for the history store. `MEM0_TELEMETRY=false` verified.

6. **Letta V1 memory model on the dialect, and git for files**
   - Upstream interface: Letta V1 ORM tables for blocks, agent-block attachments, passages, messages and conversations
   - Template: let SQLite fallback pattern in `db.py`
   - Oracle test suite: Letta V1 tests (to be mapped)
   - Proof statement: the Letta memory blocks and conversation tools work on DuckDB.

7. **Owner service, rebuilds and the "exceed" layer**
   - Upstream interface: `memory_event` contract
   - Template: Anatid `anatid-server` pattern
   - Oracle test suite: Four upstream suites against the service over socket
   - Proof statement: the owner service passes the four suites in one process, refusing second process direct file opens.
