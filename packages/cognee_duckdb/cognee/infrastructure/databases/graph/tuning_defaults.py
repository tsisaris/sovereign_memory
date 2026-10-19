"""Keep upstream compatibility defaults independent of optional database engines."""

# Cognee 1.6.5 Ladybug/Kuzu defaults, moved without changing their values.
DEFAULT_KUZU_BUFFER_POOL_SIZE = 1 << 35
DEFAULT_KUZU_MAX_DB_SIZE = 1 << 35
