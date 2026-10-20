import re

with open("vendor/graphiti/graphiti_core/utils/maintenance/graph_data_operations.py", "r") as f:
    content = f.read()

content = content.replace("""    async with driver.session() as session:

        async def delete_all(tx):
            await tx.run('MATCH (n) DETACH DELETE n')""",
"""    # We should let the hook handle everything
    # The previous patch missed the rest of the function because of the return inside try block
    # Since we patched the if block to be `if True:` and returning, we just need to return.
    """)

with open("vendor/graphiti/graphiti_core/utils/maintenance/graph_data_operations.py", "w") as f:
    # Actually wait, let's just make it simple
    f.write(content)
