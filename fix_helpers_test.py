import re

with open("vendor/graphiti/tests/helpers_test.py", "r") as f:
    content = f.read()

# I appended DUCKDB under the NEO4J flag, which means when DISABLE_NEO4J=1 it disables DUCKDB too.
content = content.replace("drivers.append(GraphProvider.NEO4J)\n        drivers.append(GraphProvider.DUCKDB)", "drivers.append(GraphProvider.NEO4J)")
content = content.replace("drivers: list[GraphProvider] = []", "drivers: list[GraphProvider] = [GraphProvider.DUCKDB]")

with open("vendor/graphiti/tests/helpers_test.py", "w") as f:
    f.write(content)
