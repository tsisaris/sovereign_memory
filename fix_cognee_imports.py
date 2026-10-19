import re
import sys


def main():
    content = open(
        "packages/cognee_duckdb/cognee/modules/users/tenants/methods/remove_user_from_tenant.py"
    ).read()
    if "from fastapi_users" not in content:
        content = "from fastapi_users import schemas, models\n" + content
    open(
        "packages/cognee_duckdb/cognee/modules/users/tenants/methods/remove_user_from_tenant.py",
        "w",
    ).write(content)

    content = open(
        "packages/cognee_duckdb/cognee/modules/visualization/exceptions/GraphStreamCapacityError.py"
    ).read()
    if "from fastapi import" not in content:
        content = "from fastapi import HTTPException\n" + content
    open(
        "packages/cognee_duckdb/cognee/modules/visualization/exceptions/GraphStreamCapacityError.py",
        "w",
    ).write(content)


main()
