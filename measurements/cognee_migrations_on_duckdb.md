The first migration `8057ae7329c2_initial_migration.py` failed immediately with DuckDB's exact message:
`sqlalchemy.exc.NotSupportedError: (_duckdb.NotImplementedException) Not implemented Error: Creating partial indexes is not supported currently
[SQL: CREATE UNIQUE INDEX ux_provenance_sequence_id ON provenance_entries (sequence_id) WHERE sequence_id IS NOT NULL]`
Since the first migration creates base tables and fails, the other 47 migrations cannot be validly tested as they depend on it.
