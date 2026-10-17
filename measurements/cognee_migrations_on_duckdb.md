# Cognee Migrations on DuckDB

DuckDB 1.5.6 lacks support for partial unique indexes, which Cognee uses.

Specifically, migration `8057ae7329c2_initial_migration.py` contains:

```sql
CREATE UNIQUE INDEX ux_provenance_sequence_id ON provenance_entries (sequence_id) WHERE sequence_id IS NOT NULL
```

DuckDB's exact message:
```
sqlalchemy.exc.NotSupportedError: (_duckdb.NotImplementedException) Not implemented Error: Creating partial indexes is not supported currently
```

This prevents the migration from running out-of-the-box without modifying upstream Cognee schema definitions to avoid partial indexes.
