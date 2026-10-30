import re
import os
import glob

def rename_kuzu_to_duckdb(content):
    content = content.replace("KuzuDriver", "DuckDBDriver")
    content = content.replace("KuzuSagaNodeOperations", "DuckDBSagaNodeOperations")
    content = content.replace("KuzuSearchOperations", "DuckDBSearchOperations")
    content = content.replace("GraphProvider.KUZU", "GraphProvider.DUCKDB")
    # Replace other operations
    for op in ["CommunityEdgeOperations", "CommunityNodeOperations", "EntityEdgeOperations", "EntityNodeOperations", "EpisodeNodeOperations", "EpisodicEdgeOperations", "GraphMaintenanceOperations", "HasEpisodeEdgeOperations", "NextEpisodeEdgeOperations", "SearchOperations"]:
        content = content.replace(f"Kuzu{op}", f"DuckDB{op}")
    return content

for filepath in glob.glob("packages/graphiti_duckdb/graphiti_duckdb/**/*.py", recursive=True):
    with open(filepath, 'r') as f:
        content = f.read()
    
    content = rename_kuzu_to_duckdb(content)
    
    with open(filepath, 'w') as f:
        f.write(content)

