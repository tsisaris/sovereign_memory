"""Shared behaviour of Letta's mapped classes: conversion to Pydantic schemas.

Licensed under Apache-2.0 from the Letta archive (letta/orm/sqlalchemy_base.py),
reduced to the conversion; storage operations live in letta_duckdb_store.
"""

from typing import Any, ClassVar


class SqlalchemyBase:
    """Mixin giving every mapped row a to_pydantic conversion.

    Each mapped class names its schema in __pydantic_model__. It never opens,
    commits or closes a connection.
    """

    __abstract__ = True
    __pydantic_model__: ClassVar[Any]

    def to_pydantic(self) -> Any:
        """Return the row validated as its Pydantic schema; raise if it does not validate."""
        return self.__pydantic_model__.model_validate(self, from_attributes=True)
