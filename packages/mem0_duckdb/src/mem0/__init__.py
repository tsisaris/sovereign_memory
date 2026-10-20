"""The Mem0 package root.

It exposes the version string and the four public classes Memory, AsyncMemory, MemoryClient and
AsyncMemoryClient for 'from mem0 import ...'. It defines no behaviour of its own and opens no
connection when imported.
"""

__version__ = "2.2.1"

from mem0.client.main import AsyncMemoryClient, MemoryClient  # noqa
from mem0.memory.main import AsyncMemory, Memory  # noqa
