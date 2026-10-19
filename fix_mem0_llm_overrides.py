import os
import re

llms_dir = "packages/mem0_duckdb/src/mem0/llms"
for filename in os.listdir(llms_dir):
    if filename.endswith(".py") and filename != "base.py":
        path = os.path.join(llms_dir, filename)
        with open(path, "r") as f:
            content = f.read()

        # Fix def generate_response(self, messages: List[Dict[str, str]], response_format=None, tools: Optional[List[Dict]] = None, tool_choice: str = "auto", **kwargs):
        # Base expects: generate_response(self, messages: List[Dict[str, str]], tools: Optional[List[Dict]] = None, tool_choice: str = "auto", **kwargs):

        # We will try to replace 'response_format=None, ' with ''
        # We need to make sure response_format is handled via kwargs if we remove it.
        # This is a bit tricky, but we can do a simple string replace for now.
        content = re.sub(
            r"def generate_response\(\s*self,\s*messages: List\[Dict\[str, str\]\],\s*response_format\s*(:\s*[^,]+)?\s*=\s*None,\s*",
            r"def generate_response(\n        self,\n        messages: List[Dict[str, str]],\n        ",
            content,
        )
        content = re.sub(
            r"def generate_response\(\s*self,\s*messages: List\[Dict\[str, str\]\],\s*response_format=None,\s*",
            r"def generate_response(\n        self,\n        messages: List[Dict[str, str]],\n        ",
            content,
        )

        # Remove response_format assignments if they exist as a param in generate_response, we will extract it from kwargs
        content = re.sub(
            r"(\s+)if response_format:",
            r'\1response_format = kwargs.pop("response_format", None)\n\1if response_format:',
            content,
        )

        with open(path, "w") as f:
            f.write(content)
