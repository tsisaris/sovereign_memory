import sys


def main():
    content = open("packages/cognee_duckdb/cognee/modules/pipelines/models/Task.py").read()
    if "import cognee" not in content:
        content = "import cognee\n" + content
    open("packages/cognee_duckdb/cognee/modules/pipelines/models/Task.py", "w").write(content)


main()
