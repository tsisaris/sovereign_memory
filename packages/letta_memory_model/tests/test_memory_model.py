"""Test memory model docstring."""

import os
import uuid

from letta_memory_model.orm.block import Block
from letta_memory_model.orm.conversation import Conversation
from letta_memory_model.orm.message import Message
from letta_memory_model.orm.passage import ArchivalPassage, SourcePassage
from letta_memory_model.schemas.block import Block as PydanticBlock


# Test that actual memory elements can be instantiated to establish the data structure compatibility
def test_block_creation():
    """Test test_block_creation."""
    block = Block()
    block.id = f"block-{uuid.uuid4()}"
    block.label = "human"
    block.value = "I am a human."
    block.limit = 2000
    block.organization_id = "org-1"

    assert block.label == "human"
    assert block.value == "I am a human."
    assert block.limit == 2000


def test_source_passage_creation():
    """Test test_source_passage_creation."""
    passage = SourcePassage()
    passage.id = f"passage-{uuid.uuid4()}"
    passage.text = "Sample memory text"
    passage.file_name = "mem.txt"
    passage.source_id = "src-1"
    passage.organization_id = "org-1"
    passage.embedding = None

    assert passage.text == "Sample memory text"
    assert passage.file_name == "mem.txt"


def test_archival_passage_creation():
    """Test test_archival_passage_creation."""
    passage = ArchivalPassage()
    passage.id = f"passage-{uuid.uuid4()}"
    passage.text = "Sample archival memory text"
    passage.archive_id = "arch-1"
    passage.organization_id = "org-1"
    passage.embedding = None

    assert passage.text == "Sample archival memory text"
    assert passage.archive_id == "arch-1"


def test_message_creation():
    """Test test_message_creation."""
    msg = Message()
    msg.id = f"message-{uuid.uuid4()}"
    msg.role = "user"
    msg.text = "Hello agent!"
    msg.sequence_id = 1
    msg.organization_id = "org-1"
    msg.agent_id = "agent-1"

    assert msg.role == "user"
    assert msg.text == "Hello agent!"
    assert msg.agent_id == "agent-1"


def test_conversation_creation():
    """Test test_conversation_creation."""
    conv = Conversation()
    conv.id = f"conv-{uuid.uuid4()}"
    conv.agent_id = "agent-1"
    conv.summary = "A short conversation"
    conv.organization_id = "org-1"

    assert conv.agent_id == "agent-1"
    assert conv.summary == "A short conversation"


# Test git semantics block (Memory Block Operations)
from letta_memory_model.functions.function_sets.base import (
    core_memory_append,
    core_memory_replace,
    memory_insert,
    memory_replace,
    memory_rethink,
)


class MockMemory:
    """Mock Memory."""

    def __init__(self, blocks):
        """Init."""
        self.blocks = {b.label: b for b in blocks}

    def get_block(self, label):
        """Get block."""
        return self.blocks.get(label)

    def set_block(self, block):
        """Set block."""
        self.blocks[block.label] = block

    def update_block_value(self, label, value):
        """Update block value."""
        self.blocks[label].value = value


class MockAgentState:
    """Mock Agent State."""

    def __init__(self, memory):
        """Init."""
        self.memory = memory


def test_core_memory_append():
    """Test test_core_memory_append."""
    block = PydanticBlock()
    block.label = "human"
    block.value = "Alice is nice."
    block.limit = 2000
    agent_state = MockAgentState(MockMemory([block]))

    res = core_memory_append(agent_state, "human", " She likes dogs.")
    assert res == "Alice is nice. She likes dogs."
    assert agent_state.memory.get_block("human").value == "Alice is nice. She likes dogs."


def test_core_memory_replace():
    """Test test_core_memory_replace."""
    block = PydanticBlock()
    block.label = "human"
    block.value = "Alice is nice. She likes dogs."
    block.limit = 2000
    agent_state = MockAgentState(MockMemory([block]))

    res = core_memory_replace(agent_state, "human", "Alice is nice.", "Bob is mean.")
    assert res == "Bob is mean. She likes dogs."
    assert agent_state.memory.get_block("human").value == "Bob is mean. She likes dogs."


def test_memory_insert():
    """Test test_memory_insert."""
    block = PydanticBlock()
    block.label = "human"
    block.value = "Line 1\nLine 2"
    block.limit = 2000
    agent_state = MockAgentState(MockMemory([block]))

    res = memory_insert(agent_state, "human", "Line 1.5", insert_line=1)
    assert res == "Line 1\nLine 1.5\nLine 2"
    assert agent_state.memory.get_block("human").value == "Line 1\nLine 1.5\nLine 2"


def test_memory_replace():
    """Test test_memory_replace."""
    block = PydanticBlock()
    block.label = "human"
    block.value = "I have 2 dogs"
    block.limit = 2000
    agent_state = MockAgentState(MockMemory([block]))

    res = memory_replace(agent_state, "human", "2 dogs", "3 cats")
    assert res == "I have 3 cats"
    assert agent_state.memory.get_block("human").value == "I have 3 cats"


def test_memory_rethink():
    """Test test_memory_rethink."""
    block = PydanticBlock()
    block.label = "human"
    block.value = "Old memory"
    block.limit = 2000
    agent_state = MockAgentState(MockMemory([block]))

    res = memory_rethink(agent_state, "human", "Brand new rewritten memory")
    assert res == "Brand new rewritten memory"
    assert agent_state.memory.get_block("human").value == "Brand new rewritten memory"


# Test git utilities
from letta_memory_model.git_utils.git_backend import BareGitRemote, GitWorktree


def test_git_backend_workflow():
    """Test test_git_backend_workflow."""
    remote = BareGitRemote("agent-123")
    assert os.path.exists(remote.remote_path)
    assert remote.remote_path.endswith(".git")

    worktree1 = GitWorktree(remote.get_remote_url())
    worktree1.write_file("skills/hello.py", "print('hello world')")
    worktree1.commit_and_push("Add hello skill")

    worktree2 = GitWorktree(remote.get_remote_url())
    content = worktree2.read_file("skills/hello.py")
    assert content == "print('hello world')"

    worktree2.write_file("skills/hello.py", "print('hello universe')")
    worktree2.commit_and_push("Update hello skill")

    worktree1.pull()
    content_updated = worktree1.read_file("skills/hello.py")
    assert content_updated == "print('hello universe')"

    worktree1.cleanup()
    worktree2.cleanup()
