"""Pydantic agent-state schema: the part of an agent the memory tools act on.

Licensed under Apache-2.0 from the Letta archive (letta/schemas/agent.py),
reduced to the agent identity and its in-context memory.
"""

from pydantic import BaseModel, ConfigDict, Field

from letta_memory_model.schemas.memory import Memory


class AgentState(BaseModel):
    """An agent's identity and its core memory, handed to the memory tools.

    It takes the agent id and a Memory holding the agent's attached blocks. It
    never reads or writes storage; a store persists what the tools change.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(..., description="The id of the agent.")
    memory: Memory = Field(..., description="The agent's in-context memory blocks.")
