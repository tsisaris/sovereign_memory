The first migration `8057ae7329c2_initial_migration.py` failed immediately with DuckDB's exact message:
`sqlalchemy.exc.NotSupportedError: (_duckdb.NotImplementedException) Not implemented Error: Creating partial indexes is not supported currently
[SQL: CREATE UNIQUE INDEX ux_provenance_sequence_id ON provenance_entries (sequence_id) WHERE sequence_id IS NOT NULL]`

Since the first migration creates base tables and fails, the other 47 migrations after it could not be validly tested.

DuckDB Documentation regarding Partial Indexes:
DuckDB does not currently support `WHERE` clauses in `CREATE INDEX` (partial indexes). The nearest equivalent to enforce uniqueness only on non-null rows relies on DuckDB's standard `UNIQUE` constraint, which in standard SQL automatically ignores `NULL` values. Thus, replacing the partial unique index with a standard `UNIQUE(sequence_id)` constraint (or standard unique index) on the column will provide the same uniqueness guarantee for non-null elements while allowing multiple `NULL` values.

This is a decision for the owner, and Cognee was not patched in this pull request to comply with the rules.
