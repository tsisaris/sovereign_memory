import re

def patch_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # The replace I did before replaced driver.graph_operations_interface instead of driver.episode_node_ops. Let's fix that.
    content = re.sub(
        r"await self\.driver\.graph_operations_interface\.remove_episode_and_singletons\(self\.driver, episode_uuid\)",
        r"await self.driver.episode_node_ops.remove_episode_and_singletons(self.driver, episode_uuid)",
        content
    )

    with open(filepath, 'w') as f:
        f.write(content)

patch_file("vendor/graphiti/graphiti_core/graphiti.py")
