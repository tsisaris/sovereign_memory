import re

def insert_import(file_path):
    with open(file_path, 'r') as f:
        content = f.read()
    
    if "from typing import Any" not in content:
        content = re.sub(r'from abc import ABC, abstractmethod', 'from abc import ABC, abstractmethod\nfrom typing import Any', content)
        with open(file_path, 'w') as f:
            f.write(content)

insert_import("vendor/graphiti/graphiti_core/driver/operations/saga_node_ops.py")
insert_import("vendor/graphiti/graphiti_core/driver/operations/episode_node_ops.py")
insert_import("vendor/graphiti/graphiti_core/driver/operations/entity_edge_ops.py")
