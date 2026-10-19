import sys

def main():
    content = open("packages/mem0_duckdb/src/mem0/llms/litellm.py").read()
    content = content.replace("params.update(kwargs)", "if kwargs: params.update(kwargs)")
    content = content.replace("params = self._get_supported_params(messages=messages, **kwargs)", "params = self._get_supported_params(messages=messages)")
    open("packages/mem0_duckdb/src/mem0/llms/litellm.py", "w").write(content)
    
    content = open("packages/mem0_duckdb/src/mem0/llms/azure_openai_structured.py").read()
    content = content.replace("params.update(kwargs)", "if kwargs: params.update(kwargs)")
    content = content.replace("params = self._get_supported_params(messages=messages, **kwargs)", "params = self._get_supported_params(messages=messages)")
    open("packages/mem0_duckdb/src/mem0/llms/azure_openai_structured.py", "w").write(content)
main()
