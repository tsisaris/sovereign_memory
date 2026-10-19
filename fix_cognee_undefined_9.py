import sys


def main():
    content = open(
        "packages/cognee_duckdb/cognee/infrastructure/databases/relational/with_async_session.py"
    ).read()
    if "from typing import Optional" not in content:
        content = "from typing import Optional\n" + content
    open(
        "packages/cognee_duckdb/cognee/infrastructure/databases/relational/with_async_session.py",
        "w",
    ).write(content)

    content = open("packages/cognee_duckdb/cognee/modules/pipelines/models/Task.py").read()
    content = content.replace(
        'datasets: Mapped[list["Pipeline.Pipeline"]] = relationship(',
        'datasets: Mapped[list["cognee.modules.pipelines.models.Pipeline.Pipeline"]] = relationship(',
    )
    open("packages/cognee_duckdb/cognee/modules/pipelines/models/Task.py", "w").write(content)


main()
