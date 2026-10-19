import sys

def main():
    for f in ["packages/mem0_duckdb/src/mem0/llms/litellm.py", "packages/mem0_duckdb/src/mem0/llms/azure_openai_structured.py", "packages/mem0_duckdb/src/mem0/llms/groq.py", "packages/mem0_duckdb/src/mem0/llms/lmstudio.py", "packages/mem0_duckdb/src/mem0/llms/openai_structured.py", "packages/mem0_duckdb/src/mem0/llms/xai.py"]:
        content = open(f).read()
        content = content.replace('        tools: Optional[List[Dict]] = None,\n        tool_choice: str = "auto",\n    ):', '        tools: Optional[List[Dict]] = None,\n        tool_choice: str = "auto",\n        **kwargs,\n    ):')
        content = content.replace('        tools: Optional[List[Dict]] = None,\n        tool_choice: str = "auto",\n    ) -> str:', '        tools: Optional[List[Dict]] = None,\n        tool_choice: str = "auto",\n        **kwargs,\n    ) -> str:')
        open(f, "w").write(content)
main()
