"""Module docstring."""

# Licensed under Apache-2.0 from Letta archive
from sqlalchemy.types import BINARY, TypeDecorator


class CommonVector(TypeDecorator):
    """Custom SQLAlchemy column type for storing vectors natively via blob/bytes."""

    impl = BINARY
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Process bind param."""
        if value is None:
            return None
        import json

        return json.dumps(value).encode("utf-8")

    def process_result_value(self, value, dialect):
        """Process result value."""
        if value is None:
            return None
        import json

        return json.loads(value.decode("utf-8"))
