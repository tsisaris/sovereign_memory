import re

def patch_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    start_idx = content.find("async def summarize_saga")
    end_idx = content.find("async def add_episode")
    
    sub = content[start_idx:end_idx]
    
    sub = re.sub(
        r"episodes_data = await self\.driver\.saga_node_ops\.summarize_saga_episodes\([\s\S]*?if content and created_at is not None:\s*episodes_data\.append\(\(content, parse_db_date\(r\.get\('valid_at'\)\), created_at\)\)",
        """episodes_data = await self.driver.saga_node_ops.summarize_saga_episodes(
            self.driver, saga_id, since, max_episodes
        )""",
        sub
    )

    content = content[:start_idx] + sub + content[end_idx:]

    with open(filepath, 'w') as f:
        f.write(content)

patch_file("vendor/graphiti/graphiti_core/graphiti.py")
