import os
import subprocess
import sys

os.environ["DISABLE_NEO4J"] = "1"
os.environ["DISABLE_FALKORDB"] = "1"
os.environ["ENABLE_DUCKDB"] = "1"
os.environ["UV_NO_EDITABLE"] = "1"

# the specific command mentioned by the user
cmd = [
    "uv",
    "run",
    "python",
    "-m",
    "pytest",
    "-p",
    "tests.helpers_test",
    "tests/test_node_int.py",
    "tests/test_edge_int.py",
    "-q",
]

proc = subprocess.Popen(
    cmd, cwd="packages/graphiti_duckdb", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
)
stdout, stderr = proc.communicate()

print(stdout)
if stderr:
    print("STDERR:")
    print(stderr)

sys.exit(proc.returncode)
