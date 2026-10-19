import sys


def main():
    content = open("packages/mem0_duckdb/src/mem0/llms/ollama.py").read()
    content = content.replace(
        'if response_format and response_format.get("type") == "json_object":',
        'response_format = kwargs.pop("response_format", None)\n        if response_format and response_format.get("type") == "json_object":',
    )
    open("packages/mem0_duckdb/src/mem0/llms/ollama.py", "w").write(content)


main()
