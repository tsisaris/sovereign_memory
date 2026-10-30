import os
import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "r") as f:
    content = f.read()

# Fix module imports
content = content.replace("from graphiti_duckdb.operations.", "from graphiti_duckdb.operations.")
content = content.replace("from graphiti_core.driver.kuzu.operations.", "from graphiti_duckdb.operations.")

# Fix operations imports
import glob
for filepath in glob.glob("packages/graphiti_duckdb/src/graphiti_duckdb/operations/*.py"):
    with open(filepath, "r") as f:
        filecontent = f.read()
    filecontent = filecontent.replace("from graphiti_core.driver.kuzu.operations.", "from graphiti_duckdb.operations.")
    with open(filepath, "w") as f:
        f.write(filecontent)

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "w") as f:
    f.write(content)
