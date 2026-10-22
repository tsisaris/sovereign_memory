"""
Copyright 2024, Zep Software, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import json
import logging
import typing

logger = logging.getLogger(__name__)

CACHE_SCHEMA = "graphiti"
CACHE_TABLE = "llm_cache"


class OwnerConnection(typing.Protocol):
    """The memory service's own connection: one worker thread runs every job on its handle."""

    async def run(self, func: typing.Callable[..., typing.Any], *args: typing.Any) -> typing.Any:
        """Run func on the connection's worker thread and return its result."""
        ...

    @property
    def native(self) -> typing.Any:
        """The worker-owned DuckDB handle; use it only inside a job given to run."""
        ...


class LLMCache:
    """Store of past AI answers kept in the memory system's own DuckDB file.

    Replaces diskcache to avoid unsafe pickle deserialization (CVE in diskcache <= 5.6.3)
    and replaces the former SQLite file: the answers live in the table
    graphiti.llm_cache of the one memory file, written only through the memory
    service's own connection (one file, one owner, one writer). Only
    JSON-serializable data is stored. The cache never opens a file, never opens a
    second connection, and never closes or commits the connection it is lent.
    """

    def __init__(self, connection: OwnerConnection | None):
        """Take the service's connection; refuse plainly when none is supplied."""
        if connection is None:
            raise ValueError(
                "LLM answer caching is on but no memory-service connection was supplied "
                "(cache_connection); the cache lives in the memory system's DuckDB file "
                "and is written only through the service's own connection"
            )
        self._connection = connection
        self._ready = False

    async def _prepare(self) -> None:
        """Create the schema and table on first use, through the service's connection."""
        if self._ready:
            return

        def create() -> None:
            """Create the cache schema and table if they are missing; runs on the worker."""
            handle = self._connection.native
            handle.execute(f"CREATE SCHEMA IF NOT EXISTS {CACHE_SCHEMA}")
            handle.execute(
                f"CREATE TABLE IF NOT EXISTS {CACHE_SCHEMA}.{CACHE_TABLE} "
                "(key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL)"
            )

        await self._connection.run(create)
        self._ready = True

    async def get(self, key: str) -> dict[str, typing.Any] | None:
        """Return the stored answer for key, or None when absent or corrupted."""
        await self._prepare()

        def read() -> list:
            """Return the rows stored under key; runs on the worker."""
            return self._connection.native.execute(
                f"SELECT value FROM {CACHE_SCHEMA}.{CACHE_TABLE} WHERE key = ?", [key]
            ).fetchall()

        rows = await self._connection.run(read)
        if not rows:
            return None
        try:
            return json.loads(rows[0][0])
        except json.JSONDecodeError:
            logger.warning(f"Corrupted cache entry for key {key}, ignoring")
            return None

    async def set(self, key: str, value: dict[str, typing.Any]) -> None:
        """Store value under key, replacing an earlier answer; skip a value JSON cannot hold."""
        try:
            serialized = json.dumps(value)
        except TypeError:
            logger.warning(f"Non-JSON-serializable cache value for key {key}, skipping")
            return
        await self._prepare()

        def write() -> None:
            """Insert or replace the answer under key; runs on the worker."""
            self._connection.native.execute(
                f"INSERT OR REPLACE INTO {CACHE_SCHEMA}.{CACHE_TABLE} (key, value) VALUES (?, ?)",
                [key, serialized],
            )

        await self._connection.run(write)
