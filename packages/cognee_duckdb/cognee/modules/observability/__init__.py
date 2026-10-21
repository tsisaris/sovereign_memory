from contextlib import contextmanager

from .logs import setup_log_bridge, shutdown_log_bridge
from .metrics import (
    MEMORY_DATA_BYTES_STORED,
    MEMORY_GRAPH_EDGES_ADDED,
    MEMORY_GRAPH_NODES_ADDED,
    MEMORY_ITEMS_DELETED,
    MEMORY_ITEMS_RETRIEVED,
    MEMORY_ITEMS_STORED,
    # metric name constants (memory-semconv v0.1.0)
    MEMORY_OPERATION_DURATION,
    MEMORY_OPERATION_ERRORS,
    MEMORY_QUERY_RESULT_COUNT,
    MEMORY_VECTOR_SEARCHES,
    get_meter,
    increment_bytes_stored,
    increment_graph_edges,
    increment_graph_nodes,
    increment_items_deleted,
    increment_items_retrieved,
    increment_items_stored,
    increment_operation_errors,
    increment_vector_searches,
    record_operation_duration,
    record_query_results,
    setup_metrics,
    shutdown_metrics,
)
from .trace_context import (
    clear_traces,
    disable_tracing,
    enable_tracing,
    get_all_traces,
    get_last_trace,
    is_tracing_enabled,
)
from .tracing import (
    COGNEE_DATA_ITEM_COUNT,
    COGNEE_DATA_SIZE_BYTES,
    # V2 attributes
    COGNEE_DATASET_NAME,
    COGNEE_DB_QUERY,
    COGNEE_DB_ROW_COUNT,
    COGNEE_DB_SYSTEM,
    COGNEE_FORGET_TARGET,
    COGNEE_GRAPH_EDGES_SYNCED,
    COGNEE_IMPROVE_STAGES,
    COGNEE_LLM_MODEL,
    COGNEE_LLM_PROVIDER,
    COGNEE_OPERATION_MODE,
    COGNEE_PIPELINE_NAME,
    COGNEE_PIPELINE_TASK_NAME,
    COGNEE_RECALL_ROUTE_RULE,
    COGNEE_RECALL_SCOPE,
    COGNEE_RECALL_SOURCE,
    COGNEE_RESULT_COUNT,
    COGNEE_RESULT_SUMMARY,
    COGNEE_SEARCH_QUERY,
    COGNEE_SEARCH_TYPE,
    COGNEE_SESSION_ENTRY_COUNT,
    COGNEE_SESSION_ID,
    COGNEE_SPAN_CATEGORY,
    COGNEE_VECTOR_COLLECTION,
    COGNEE_VECTOR_RESULT_COUNT,
    CogneeTrace,
    get_tracer,
    redact_secrets,
)

# memory-semconv v0.1.0 span attribute keys
MEMORY_SYSTEM = "memory.system"
MEMORY_OPERATION = "memory.operation"
MEMORY_QUERY_TEXT = "memory.query.text"
MEMORY_QUERY_TYPE = "memory.query.type"
MEMORY_RESULT_COUNT = "memory.result.count"
MEMORY_DATA_SIZE = "memory.data.size"
MEMORY_ITEM_COUNT = "memory.item.count"
MEMORY_COLLECTION = "memory.collection"
MEMORY_STORE_BACKEND = "memory.store.backend"


try:
    from opentelemetry.trace import StatusCode as OtelStatusCode
except ImportError:

    class _StatusCodeFallback:
        ERROR = "ERROR"
        OK = "OK"
        UNSET = "UNSET"

    OtelStatusCode = _StatusCodeFallback  # type: ignore[misc, assignment]


class _NullSpan:
    """No-op span used when tracing is disabled."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def get_tracer_if_enabled() -> object | None:
    """Return the OTEL tracer if tracing is enabled, None otherwise."""
    if is_tracing_enabled():
        return get_tracer()
    return None


@contextmanager
def new_span(name: str):
    """Context manager that creates an OTEL span if tracing is enabled, or yields None."""
    if is_tracing_enabled():
        tracer = get_tracer()
        if tracer is not None:
            with tracer.start_as_current_span(name) as span:
                yield span
                return
    yield _NullSpan()
