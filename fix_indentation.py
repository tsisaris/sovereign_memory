import re
filepath = 'packages/cognee_duckdb/cognee_community_hybrid_adapter_duckdb/duckdb_adapter.py'
with open(filepath, 'r') as f:
    content = f.read()

lines = content.split('\n')
for i, line in enumerate(lines):
    if line.startswith('    async def get_edge_delete_data(') or line.startswith('    async def find_nodes_by_source_ref(') or line.startswith('    async def find_edges_by_source_ref(') or line.startswith('    async def get_graph_metadata(') or line.startswith('    async def set_graph_metadata(') or line.startswith('    async def find_node_source_refs_by_dataset(') or line.startswith('    async def find_edge_source_refs_by_dataset(') or line.startswith('    async def find_edge_source_refs_by_pipeline_run(') or line.startswith('    async def find_node_source_refs_by_pipeline_run(') or line.startswith('    async def get_node_delete_data('):
        pass # The indentation is fine here. Let's fix the inner loops

# Using a generic formatter like `black` that supports Python 3.12 (Wait, the error from black earlier was because of a missing parenthesis somewhere).
# Let's find the missing parenthesis.
# 1255 | …     q = "SELECT id, name, type, properties, source_ref_keys, source_dataset_ids, source_run_ids, source_run_refs FROM graph_node W…
