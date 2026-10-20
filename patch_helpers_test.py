import re
import os

filepath = "vendor/graphiti/tests/helpers_test.py"
with open(filepath, 'r') as f:
    content = f.read()

content = content.replace("from graphiti_core.driver.driver import GraphDriver, GraphProvider", "from graphiti_core.driver.driver import GraphDriver, GraphProvider\nfrom graphiti_duckdb.duckdb_driver import DuckDBDriver")
content = content.replace("drivers.append(GraphProvider.NEO4J)", "drivers.append(GraphProvider.NEO4J)\n        drivers.append(GraphProvider.DUCKDB)")

start_idx = content.find("def get_driver(provider: GraphProvider) -> GraphDriver:")
end_idx = content.find("@pytest.fixture(params=drivers)")

sub = content[start_idx:end_idx]
sub = sub.replace("if provider == GraphProvider.NEO4J:", "if provider == GraphProvider.DUCKDB:\n        return DuckDBDriver(db=':memory:')\n    elif provider == GraphProvider.NEO4J:")

content = content[:start_idx] + sub + content[end_idx:]

with open(filepath, 'w') as f:
    f.write(content)

