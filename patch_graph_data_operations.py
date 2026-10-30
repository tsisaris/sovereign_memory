import re

with open("vendor/graphiti/graphiti_core/utils/maintenance/graph_data_operations.py", "r") as f:
    content = f.read()

content = content.replace("if driver.graph_operations_interface:", "if True:")
content = content.replace("await driver.graph_operations_interface.clear_data(driver, group_ids)", "await driver.graph_ops.clear_data(driver, group_ids)")

with open("vendor/graphiti/graphiti_core/utils/maintenance/graph_data_operations.py", "w") as f:
    f.write(content)

