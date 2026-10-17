Out of 48 Cognee migrations:
47 migrations applied and rolled back successfully.

1 migration failed:
Migration filename: `8057ae7329c2_initial_migration.py`
DuckDB's exact message:
`sqlalchemy.exc.NotSupportedError: (_duckdb.NotImplementedException) Not implemented Error: Creating partial indexes is not supported currently
[SQL: CREATE UNIQUE INDEX ux_provenance_sequence_id ON provenance_entries (sequence_id) WHERE sequence_id IS NOT NULL]`
