import re

def append_to_class(file_path, class_name, methods_str):
    with open(file_path, 'r') as f:
        content = f.read()
    
    # We will just append the methods to the end of the class.
    # The files usually just have one class and end there.
    
    with open(file_path, 'w') as f:
        f.write(content.rstrip() + "\n\n" + methods_str + "\n")

# saga_node_ops.py
saga_methods = """
    @abstractmethod
    async def get_or_create_saga(
        self,
        executor: QueryExecutor,
        saga_name: str,
        group_id: str,
        created_at: Any,
    ) -> SagaNode | None: ...

    @abstractmethod
    async def saga_get_previous_episode_uuid(
        self,
        executor: QueryExecutor,
        saga_uuid: str,
        current_episode_uuid: str,
    ) -> str | None: ...

    @abstractmethod
    async def summarize_saga_episodes(
        self,
        executor: QueryExecutor,
        saga_id: str,
        since: Any,
        limit: int,
    ) -> list[tuple[str, Any, Any]]: ...
"""

append_to_class("vendor/graphiti/graphiti_core/driver/operations/saga_node_ops.py", "SagaNodeOperations", saga_methods)

# episode_node_ops.py
episode_methods = """
    @abstractmethod
    async def remove_episode_and_singletons(
        self,
        executor: QueryExecutor,
        episode_uuid: str,
    ) -> None: ...
"""

append_to_class("vendor/graphiti/graphiti_core/driver/operations/episode_node_ops.py", "EpisodeNodeOperations", episode_methods)


# search_ops.py
search_methods = """
    @abstractmethod
    async def get_relevant_nodes(
        self,
        executor: QueryExecutor,
        nodes: list[Any], # list[dict]
        search_filter: Any,
        group_id: str,
        min_score: float,
        limit: int,
    ) -> list[Any]: ...

    @abstractmethod
    async def get_relevant_edges(
        self,
        executor: QueryExecutor,
        edges: list[Any], # list[dict]
        search_filter: Any,
        min_score: float,
        limit: int,
    ) -> list[Any]: ...

    @abstractmethod
    async def get_edge_invalidation_candidates(
        self,
        executor: QueryExecutor,
        edges: list[Any], # list[dict]
        search_filter: Any,
        min_score: float,
        limit: int,
    ) -> list[Any]: ...
"""

append_to_class("vendor/graphiti/graphiti_core/driver/operations/search_ops.py", "SearchOperations", search_methods)

# entity_edge_ops.py
edge_methods = """
    @abstractmethod
    async def filter_existing_duplicate_of_edges(
        self,
        executor: QueryExecutor,
        duplicates_node_uuids: list[tuple[str, str]],
    ) -> list[tuple[str, str]]: ...
"""

append_to_class("vendor/graphiti/graphiti_core/driver/operations/entity_edge_ops.py", "EntityEdgeOperations", edge_methods)

print("Protocols patched.")
