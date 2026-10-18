import re

filepath = "packages/cognee_duckdb/cognee_community_hybrid_adapter_duckdb/duckdb_adapter.py"
with open(filepath, "r") as f:
    content = f.read()

# We need to make sure `NodeDeleteData` expects `node_id` and `node_type`, which is verified by earlier traceback:
# "TypeError: NodeDeleteData.__init__() got an unexpected keyword argument 'id'"
# And EdgeDeleteData requires `edge`, `edge_text`, `edge_properties` etc.

# Let's fix the parameter names properly without messing up indentation
content = content.replace(
    "res[nid] = NodeDeleteData(\n                    id=row[0],\n                    type=row[2],",
    "res[nid] = NodeDeleteData(\n                    node_id=row[0],\n                    node_type=row[2],",
)

with open(filepath, "w") as f:
    f.write(content)
