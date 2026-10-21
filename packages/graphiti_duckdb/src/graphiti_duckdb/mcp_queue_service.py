"""Per-group episode queue for the Graphiti MCP server.

Ported from Graphiti's mcp_server/src/services/queue_service.py. Goes in: episode jobs.
Comes out: each group's jobs run one at a time, in order, in the background. It never runs
two jobs of the same group at once and never drops a job silently.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class QueueService:
    """Run each group's episode jobs one after another, so they cannot race."""

    def __init__(self) -> None:
        """Start with no queues, no workers and no Graphiti client."""
        self._episode_queues: dict[str, asyncio.Queue[Callable[[], Awaitable[None]]]] = {}
        self._queue_workers: dict[str, bool] = {}
        self._worker_tasks: set[asyncio.Task[None]] = set()
        self._graphiti_client: Any = None

    async def add_episode_task(
        self, group_id: str, process_func: Callable[[], Awaitable[None]]
    ) -> int:
        """Queue one job for a group and start that group's worker if none is running.

        Goes in: the group id and the async job. Comes out: the queue length after adding.
        It never runs the job itself.
        """
        if group_id not in self._episode_queues:
            self._episode_queues[group_id] = asyncio.Queue()
        await self._episode_queues[group_id].put(process_func)
        if not self._queue_workers.get(group_id, False):
            task = asyncio.create_task(self._process_episode_queue(group_id))
            self._worker_tasks.add(task)
            task.add_done_callback(self._worker_tasks.discard)
        return self._episode_queues[group_id].qsize()

    async def _process_episode_queue(self, group_id: str) -> None:
        """Run a group's jobs one at a time until the worker is cancelled.

        Goes in: the group id. Comes out: nothing. A job that fails is logged and the next
        job still runs.
        """
        logger.info("Starting episode queue worker for group_id: %s", group_id)
        self._queue_workers[group_id] = True
        try:
            while True:
                process_func = await self._episode_queues[group_id].get()
                try:
                    await process_func()
                except Exception as error:
                    logger.error(
                        "Error processing queued episode for group_id %s: %s", group_id, error
                    )
                finally:
                    self._episode_queues[group_id].task_done()
        except asyncio.CancelledError:
            logger.info("Episode queue worker for group_id %s was cancelled", group_id)
        finally:
            self._queue_workers[group_id] = False
            logger.info("Stopped episode queue worker for group_id: %s", group_id)

    def get_queue_size(self, group_id: str) -> int:
        """Return how many jobs a group has waiting; zero for an unknown group."""
        if group_id not in self._episode_queues:
            return 0
        return self._episode_queues[group_id].qsize()

    def is_worker_running(self, group_id: str) -> bool:
        """Say whether a worker is running for the group."""
        return self._queue_workers.get(group_id, False)

    async def initialize(self, graphiti_client: Any) -> None:
        """Give the queue the Graphiti client its jobs will use; it never creates one."""
        self._graphiti_client = graphiti_client
        logger.info("Queue service initialized with graphiti client")

    async def wait_until_idle(self, group_id: str) -> None:
        """Wait until every queued job of a group has finished; returns at once if none."""
        if group_id in self._episode_queues:
            await self._episode_queues[group_id].join()

    async def stop(self) -> None:
        """Cancel every worker and wait for it to stop; queued jobs not yet started are dropped."""
        for task in list(self._worker_tasks):
            task.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)

    async def add_episode(
        self,
        group_id: str,
        name: str,
        content: str,
        source_description: str,
        episode_type: Any,
        entity_types: Any,
        uuid: str | None,
        reference_time: datetime | None = None,
        edge_types: Any = None,
        edge_type_map: Any = None,
        excluded_entity_types: list[str] | None = None,
        previous_episode_uuids: list[str] | None = None,
        custom_extraction_instructions: str | None = None,
        update_communities: bool = False,
        saga: str | None = None,
        saga_previous_episode_uuid: str | None = None,
    ) -> int:
        """Queue one episode for processing by the Graphiti client.

        Goes in: the episode's text, type, optional types and ordering hints. Comes out: the
        queue length after adding. The processing itself is the Graphiti client's and needs a
        language model; this method never calls one.
        """
        if self._graphiti_client is None:
            raise RuntimeError("Queue service not initialized. Call initialize() first.")
        client = self._graphiti_client

        async def process_episode() -> None:
            """Hand the episode to the Graphiti client and log how it went."""
            try:
                logger.info("Processing episode %s for group %s", uuid, group_id)
                await client.add_episode(
                    name=name,
                    episode_body=content,
                    source_description=source_description,
                    source=episode_type,
                    group_id=group_id,
                    reference_time=reference_time or datetime.now(timezone.utc),
                    entity_types=entity_types,
                    edge_types=edge_types,
                    edge_type_map=edge_type_map,
                    excluded_entity_types=excluded_entity_types,
                    previous_episode_uuids=previous_episode_uuids,
                    custom_extraction_instructions=custom_extraction_instructions,
                    update_communities=update_communities,
                    saga=saga,
                    saga_previous_episode_uuid=saga_previous_episode_uuid,
                    uuid=uuid,
                )
                logger.info("Successfully processed episode %s for group %s", uuid, group_id)
            except Exception as error:
                logger.error("Failed to process episode %s for group %s: %s", uuid, group_id, error)
                raise

        return await self.add_episode_task(group_id, process_episode)
