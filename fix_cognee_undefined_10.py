import sys


def main():
    content = open("packages/cognee_duckdb/cognee/modules/pipelines/models/Task.py").read()
    content = content.replace(
        'datasets: Mapped[list["Pipeline"]] = relationship(',
        'datasets: Mapped[list["cognee.modules.pipelines.models.Pipeline.Pipeline"]] = relationship(',
    )
    open("packages/cognee_duckdb/cognee/modules/pipelines/models/Task.py", "w").write(content)


main()
