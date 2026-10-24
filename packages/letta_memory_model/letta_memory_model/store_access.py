"""How the translated Letta steps reach the store without holding it across a model call.

It is for the compaction, sleep-time and dreaming code, which must read what it needs, call
the model, and only then write. What goes in is a plain function that takes a LettaMemoryStore;
what comes out is that function's result, run either as a read or as one committed write. It
never holds a connection between calls and never runs a model.
"""

from collections.abc import Callable
from typing import Protocol, TypeVar

from letta_memory_model.letta_duckdb_store import LettaMemoryStore

T = TypeVar("T")


class StoreAccess(Protocol):
    """The two ways a step touches the store: a short read, or one transaction of writes.

    The caller supplies it. read runs the function on a store over the owner's own handle and
    returns what it returns. write runs it in one transaction that is committed when the
    function returns and rolled back when it raises. A step never calls either across a model
    call.
    """

    async def read(self, work: Callable[[LettaMemoryStore], T]) -> T:
        """Run work(store) as a read and return its result."""

    async def write(self, work: Callable[[LettaMemoryStore], T]) -> T:
        """Run work(store) in one committed transaction and return its result."""
