#!/bin/bash
export DISABLE_NEO4J=1
export DISABLE_FALKORDB=1
export ENABLE_DUCKDB=1
export UV_NO_EDITABLE=1
uv run python -m pytest -p tests.helpers_test tests/test_node_int.py tests/test_edge_int.py -v
