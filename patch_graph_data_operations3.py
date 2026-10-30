import re

with open("vendor/graphiti/graphiti_core/utils/maintenance/graph_data_operations.py", "r") as f:
    content = f.read()

# Replace the whole clear_data implementation to just use the hook
start_idx = content.find("async def clear_data")
end_idx = content.find("async def retrieve_episodes")

sub = """async def clear_data(driver: GraphDriver, group_ids: list[str] | None = None):
    return await driver.graph_ops.clear_data(driver, group_ids)

"""

content = content[:start_idx] + sub + content[end_idx:]

with open("vendor/graphiti/graphiti_core/utils/maintenance/graph_data_operations.py", "w") as f:
    f.write(content)
