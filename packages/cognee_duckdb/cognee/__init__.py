"""Public Python API for Cognee.

This module exposes the main functions and objects intended for direct use
through ``import cognee``. It groups the stable V1 API, memory-oriented V2 API,
visualization helpers, tracing utilities, migration helpers, and session models
behind a single package-level entrypoint.

Common entrypoints include:
    add: Add data to a Cognee dataset.
    cognify: Process ingested data into Cognee's knowledge representation.
    search: Query processed data using the configured search type.
    remember: Store memory-oriented entries using the V2 API.
    recall: Retrieve information from memory-oriented entries.
    delete: Remove data from Cognee-managed storage.
"""

from importlib import import_module

# Preserve Cognee 1.6.5 initialization order as executable imports.
# Import sorting must not move registration ahead of its prerequisites.

get_cognee_version = import_module("cognee.version", __name__).get_cognee_version

# NOTE: __version__ extraction must be at the top of the __init__.py otherwise
#       there will be circular import issues
__version__ = get_cognee_version()

# The .env must be loaded before logging is configured, because LOG_LEVEL comes
# from it. One resolver for the whole process — see cognee.shared.env_file for
# the search order (working directory first, then the package's own tree).
load_env_file = import_module("cognee.shared.env_file", __name__).load_env_file
describe_resolution = import_module("cognee.shared.env_file", __name__).describe_resolution

_env_file = load_env_file()

# NOTE: Log level can be set with the LOG_LEVEL env variable
setup_logging = import_module("cognee.shared.logging_utils", __name__).setup_logging

logger = setup_logging()
logger.info(describe_resolution(_env_file))

# ---------------------------------------------------------------------------
# V1 API
# ---------------------------------------------------------------------------
add = import_module(".api.v1.add", __name__).add
delete = import_module(".api.v1.delete", __name__).delete
cognify = import_module(".api.v1.cognify", __name__).cognify
memify = import_module(".api.v1.memify", __name__).memify
run_custom_pipeline = import_module(".modules.run_custom_pipeline", __name__).run_custom_pipeline
update = import_module(".api.v1.update", __name__).update
config = import_module(".api.v1.config.config", __name__).config
datasets = import_module(".api.v1.datasets.datasets", __name__).datasets
agents = import_module(".api.v1.agents.agents", __name__).agents
prune = import_module(".api.v1.prune", __name__).prune
SearchType = import_module(".api.v1.search", __name__).SearchType
search = import_module(".api.v1.search", __name__).search
report = import_module(".api.v1.report", __name__).report
validate = import_module(".api.v1.validate", __name__).validate
ValidationReport = import_module(".api.v1.validate", __name__).ValidationReport
ValidationIssue = import_module(".api.v1.validate", __name__).ValidationIssue
ValidationStatus = import_module(".api.v1.validate", __name__).ValidationStatus
visualize_graph = import_module(".api.v1.visualize", __name__).visualize_graph
start_visualization_server = import_module(".api.v1.visualize", __name__).start_visualization_server
get_schema_inventory = import_module(".api.v1.visualize", __name__).get_schema_inventory
get_memory_provenance_graph = import_module(
    ".api.v1.visualize", __name__
).get_memory_provenance_graph
visualize_memory_provenance = import_module(
    ".api.v1.visualize", __name__
).visualize_memory_provenance
cognee_network_visualization = import_module(
    "cognee.modules.visualization.cognee_network_visualization", __name__
).cognee_network_visualization
start_ui = import_module(".api.v1.ui", __name__).start_ui
session = import_module(".api.v1.session", __name__).session

# Pipelines
pipelines = import_module(".modules.pipelines", __name__)
Drop = import_module(".pipelines", __name__).Drop

# Migrations
run_migrations = import_module("cognee.run_migrations", __name__).run_migrations

# ---------------------------------------------------------------------------
# V2 memory-oriented API
# ---------------------------------------------------------------------------
remember = import_module(".api.v1", __name__).remember
RememberResult = import_module(".api.v1", __name__).RememberResult
recall = import_module(".api.v1", __name__).recall
improve = import_module(".api.v1", __name__).improve
forget = import_module(".api.v1", __name__).forget
serve = import_module(".api.v1", __name__).serve
disconnect = import_module(".api.v1", __name__).disconnect
visualize = import_module(".api.v1", __name__).visualize
push = import_module(".api.v1", __name__).push
PushResult = import_module(".api.v1", __name__).PushResult
export = import_module(".api.v1", __name__).export
ExportResult = import_module(".api.v1", __name__).ExportResult
MemoryEntry = import_module(".memory", __name__).MemoryEntry
QAEntry = import_module(".memory", __name__).QAEntry
TraceEntry = import_module(".memory", __name__).TraceEntry
FeedbackEntry = import_module(".memory", __name__).FeedbackEntry
SessionNodeSetConflictError = import_module(
    ".infrastructure.session.session_node_set", __name__
).SessionNodeSetConflictError

# Background work: wait for fire-and-forget tasks (background remember, the
# session-to-graph improve bridge) before the process exits.
wait_for_background_tasks = import_module(
    "cognee.infrastructure.background_tasks", __name__
).wait_for_background_tasks

# Memory migration (cognee.migration has the provider sources:
# Mem0Source, ZepSource/GraphitiSource, LettaSource, COGXArchiveSource)
migration = import_module(".migration", __name__)

# Tracing / Observability
enable_tracing = import_module(
    "cognee.modules.observability.trace_context", __name__
).enable_tracing
disable_tracing = import_module(
    "cognee.modules.observability.trace_context", __name__
).disable_tracing
get_last_trace = import_module(
    "cognee.modules.observability.trace_context", __name__
).get_last_trace
get_all_traces = import_module(
    "cognee.modules.observability.trace_context", __name__
).get_all_traces
clear_traces = import_module("cognee.modules.observability.trace_context", __name__).clear_traces

# Agent memory
agent_memory = import_module("cognee.modules.agent_memory", __name__).agent_memory

# Tool connections (authorized external databases for recall's "tools" scope)
tools = import_module(".api.v1.tools", __name__).tools

# Relational DB models
SessionModelUsage = import_module(
    "cognee.modules.session_lifecycle.models", __name__
).SessionModelUsage
SessionRecord = import_module("cognee.modules.session_lifecycle.models", __name__).SessionRecord
import_module("cognee.modules.migrations.models")
import_module("cognee.modules.tools.models")
import_module("cognee.modules.provenance.edge_evidence.models")
