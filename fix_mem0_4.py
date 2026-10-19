import sys


def main():
    content = open("packages/mem0_duckdb/src/mem0/llms/groq.py").read()
    content = content.replace("params.update(kwargs)", "if kwargs: params.update(kwargs)")
    content = content.replace(
        "params = self._get_supported_params(messages=messages, **kwargs)",
        "params = self._get_supported_params(messages=messages)",
    )
    open("packages/mem0_duckdb/src/mem0/llms/groq.py", "w").write(content)

    content = open("packages/mem0_duckdb/src/mem0/llms/litellm.py").read()
    content = content.replace("params.update(kwargs)", "if kwargs: params.update(kwargs)")
    content = content.replace(
        "params = self._get_supported_params(messages=messages, **kwargs)",
        "params = self._get_supported_params(messages=messages)",
    )
    open("packages/mem0_duckdb/src/mem0/llms/litellm.py", "w").write(content)

    content = open("packages/cognee_duckdb/cognee/modules/users/models/Role.py").read()
    content = content.replace('"cognee.modules.users.models.User.User"', '"User"')
    if "from .User import User" not in content:
        content = "from .User import User\n" + content
    open("packages/cognee_duckdb/cognee/modules/users/models/Role.py", "w").write(content)

    content = open("packages/cognee_duckdb/cognee/modules/users/models/Tenant.py").read()
    content = content.replace('"cognee.modules.users.models.User.User"', '"User"')
    if "from .User import User" not in content:
        content = "from .User import User\n" + content
    open("packages/cognee_duckdb/cognee/modules/users/models/Tenant.py", "w").write(content)

    content = open("packages/cognee_duckdb/cognee/modules/data/models/Dataset.py").read()
    content = content.replace('"cognee.modules.data.models.Data.Data"', '"Data"')
    if "from .Data import Data" not in content:
        content = "from .Data import Data\n" + content
    open("packages/cognee_duckdb/cognee/modules/data/models/Dataset.py", "w").write(content)


main()
