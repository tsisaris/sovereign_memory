"""Reset only verified Cognee relational objects in a shared DuckDB database."""

from importlib import import_module
from importlib.resources import files

from alembic.script import ScriptDirectory
from sqlalchemy import (
    JSON,
    UUID,
    Boolean,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    text,
)

from .ModelBase import Base


def _owned_metadata():
    """Load maintained models plus the two explicitly documented migration-only tables."""
    for module in (
        "cognee.modules.migrations.models",
        "cognee.modules.provenance.edge_evidence.models",
        "cognee.modules.provenance.models",
        "cognee.modules.session_lifecycle.models",
        "cognee.modules.integrations.models",
        "cognee.modules.sync.models",
    ):
        import_module(module)
    metadata = MetaData(schema="main")
    for table in Base.metadata.tables.values():
        if table.schema not in (None, "main"):
            raise ValueError(f"Unrecognized Cognee relational schema: {table.schema}")
        table.to_metadata(metadata, schema="main")
    # 45957f0a9849 creates this historical table; 1a58b986e6e1 only updates rows.
    # There is no maintained runtime Notebook model to register.
    Table(
        "notebooks",
        metadata,
        Column("id", UUID, primary_key=True),
        Column("owner_id", UUID),
        Column("name", String, nullable=False),
        Column("cells", JSON, nullable=False),
        Column("deletable", Boolean),
        Column("created_at", DateTime),
    )
    Table("alembic_version", metadata, Column("version_num", String(32), primary_key=True))
    return metadata


def reset_relational_tables(connection):
    """Validate ownership before atomically dropping declared tables on the caller transaction.

    Only the packaged current migration head is supported. Unknown shapes, inbound
    foreign keys and user views refuse reset. Database files, other engines, enums
    and unattached sequences remain untouched; no constraint or cascade bypass runs.
    """
    metadata = _owned_metadata()
    native = set(
        connection.execute(
            text(
                "SELECT table_name FROM duckdb_tables() "
                "WHERE database_name = current_database() AND schema_name = 'main'"
            )
        ).scalars()
    )
    owned = {table.name: table for table in metadata.tables.values() if table.name in native}
    if not owned:
        return
    if "alembic_version" not in owned:
        raise ValueError("Cannot verify Cognee relational ownership without its migration ledger")
    columns = connection.execute(
        text(
            "SELECT table_name, column_name, data_type, is_nullable "
            "FROM information_schema.columns WHERE table_catalog = current_database() "
            "AND table_schema = 'main'"
        )
    ).all()
    actual = {}
    for table, column, data_type, nullable in columns:
        actual.setdefault(table, {})[column] = (data_type, nullable)
    if actual["alembic_version"] != {"version_num": ("VARCHAR", "NO")}:
        raise ValueError("Unrecognized Cognee migration ledger shape")
    revisions = set(
        connection.execute(text("SELECT version_num FROM main.alembic_version")).scalars()
    )
    scripts = ScriptDirectory(str(files("cognee").joinpath("alembic")))
    if revisions != set(scripts.get_heads()):
        raise ValueError("Cognee relational reset requires the packaged current migration head")
    if connection.execute(text("SELECT count(*) FROM duckdb_views() WHERE NOT internal")).scalar():
        raise ValueError("Cannot verify relational reset while user-defined views are present")
    canonical_types = {}
    for table in owned.values():
        expected = {}
        for column in table.columns:
            compiled = str(column.type.compile(dialect=connection.dialect))
            if compiled not in canonical_types:
                canonical_types[compiled] = connection.exec_driver_sql(
                    "SELECT typeof(CAST(NULL AS " + compiled + "))"
                ).scalar()
            expected[column.name] = (
                canonical_types[compiled],
                "YES" if column.nullable else "NO",
            )
        if actual[table.name] != expected:
            raise ValueError(f"Unrecognized Cognee relational table shape: main.{table.name}")
    constraints = connection.execute(
        text(
            "SELECT schema_name, table_name, constraint_type, constraint_column_names, "
            "referenced_table, referenced_column_names FROM duckdb_constraints() "
            "WHERE database_name = current_database()"
        )
    ).all()
    observed_constraints = {name: set() for name in owned}
    for schema, table, kind, columns, referred, referred_columns in constraints:
        if kind == "FOREIGN KEY" and referred in owned and (schema != "main" or table not in owned):
            raise ValueError(
                f"External foreign key prevents Cognee relational reset: {schema}.{table}"
            )
        if schema != "main" or table not in owned or kind == "NOT NULL":
            continue
        observed_constraints[table].add(
            (kind, tuple(columns), referred, tuple(referred_columns or []))
        )
    for name, model in owned.items():
        expected_constraints = set()
        if len(model.primary_key):
            expected_constraints.add(
                ("PRIMARY KEY", tuple(column.name for column in model.primary_key), None, ())
            )
        for constraint in model.foreign_key_constraints:
            expected_constraints.add(
                (
                    "FOREIGN KEY",
                    tuple(element.parent.name for element in constraint.elements),
                    constraint.referred_table.name,
                    tuple(element.column.name for element in constraint.elements),
                )
            )
        for constraint in model.constraints:
            if isinstance(constraint, UniqueConstraint):
                expected_constraints.add(
                    ("UNIQUE", tuple(column.name for column in constraint.columns), None, ())
                )
        if observed_constraints[name] != expected_constraints:
            raise ValueError(f"Unrecognized constraints prevent relational reset: main.{name}")
    metadata.drop_all(connection, tables=list(owned.values()), checkfirst=False)
