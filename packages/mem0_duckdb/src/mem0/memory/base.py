"""Declare the shared public memory API implemented by sync and async clients."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class MemoryBase(ABC):
    """Describe memory operations without choosing providers or performing work."""

    @abstractmethod
    def get(self, memory_id):
        """
        Retrieve a memory by ID.

        Args:
            memory_id (str): ID of the memory to retrieve.

        Returns:
            dict: Retrieved memory.
        """
        pass

    @abstractmethod
    def get_all(self):
        """
        List all memories.

        Returns:
            list: List of all memories.
        """
        pass

    @abstractmethod
    def update(
        self,
        memory_id,
        text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        expiration_date: Any = ...,
        data: Optional[str] = None,
    ):
        """
        Update a memory by ID.

        Args:
            memory_id (str): ID of the memory to update.
            text (str, optional): Replacement text.
            metadata (dict, optional): Metadata changes; identity fields remain immutable.
            expiration_date (Any, optional): Replacement expiration, or None to clear it.
                Concrete clients own the sentinel that preserves omitted expiration.
            data (str, optional): Deprecated alias for text, retained for compatibility.

        Returns:
            dict: Success message indicating the memory was updated.
        """
        pass

    @abstractmethod
    def delete(self, memory_id):
        """
        Delete a memory by ID.

        Args:
            memory_id (str): ID of the memory to delete.
        """
        pass

    @abstractmethod
    def history(self, memory_id):
        """
        Get the history of changes for a memory by ID.

        Args:
            memory_id (str): ID of the memory to get history for.

        Returns:
            list: List of changes for the memory.
        """
        pass
