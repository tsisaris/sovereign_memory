"""Letta memory storage on one native DuckDB handle, through the mapped ORM.

LettaMemoryStore is the public interface the Owner service and other callers
use for Letta's memory: core-memory blocks and their attachment to agents, the
memory-editing tools applied to stored blocks, recall messages and
conversations, and archival and source passages with native vector search.

Every read and write is limited to one organization_id, the caller's
authorization scope. A store opened with open() owns its connection and wraps
each operation in its own transaction. A store built on a borrowed handle runs
inside the caller's transaction and never begins, commits, rolls back or closes
it; the caller must hold a transaction for multi-statement operations to be
atomic. No model work happens here: embeddings arrive already computed.
"""

import threading
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from os import PathLike
from typing import Any, Optional, TypeVar

import duckdb
from duckdb_sqlalchemy_async.native import connect_native, require_native_connection
from sqlalchemy import ARRAY, Float, Select, cast, delete, exists, func, literal, select, update
from sqlalchemy.orm import Session

from letta_memory_model.letta_duckdb_schema import LegacyPolicy, LegacyTable, engine_for
from letta_memory_model.letta_duckdb_schema import ensure_schema as _ensure_schema
from letta_memory_model.orm import (
    ArchivalPassage,
    Block,
    BlocksAgents,
    Conversation,
    Message,
    PassageTag,
    SourcePassage,
)
from letta_memory_model.schemas.agent import AgentState
from letta_memory_model.schemas.block import Block as PydanticBlock
from letta_memory_model.schemas.conversation import Conversation as PydanticConversation
from letta_memory_model.schemas.memory import Memory
from letta_memory_model.schemas.message import Message as PydanticMessage
from letta_memory_model.schemas.passage import Passage as PydanticPassage

T = TypeVar("T")

_BLOCK_CHANGES = frozenset(
    {"value", "label", "limit", "description", "metadata", "read_only", "hidden"}
)


class LettaStoreError(Exception):
    """Base of every refusal raised by LettaMemoryStore; the transaction is not committed."""


class LettaNotFound(LettaStoreError, LookupError):
    """Raised when a row does not exist in the caller's organization."""


class StaleBlockVersion(LettaStoreError):
    """Raised when a block changed after the caller read it; nothing was written."""


class LettaIntegrityError(LettaStoreError):
    """Raised when a write would break a rule the original foreign keys enforced."""


class LettaMemoryStore:
    """Letta's blocks, attachments, recall and passages on one native DuckDB handle.

    It takes a native connection and whether it owns it. It never opens a
    second connection and never changes the handle's default schema.
    """

    def __init__(
        self, connection: duckdb.DuckDBPyConnection, *, owns_connection: bool = False
    ) -> None:
        """Wrap connection; with owns_connection False it is the caller's to finish and close."""
        require_native_connection(connection)
        self._native = connection
        self._owns = owns_connection
        self._engine = engine_for(connection)
        self._lock = threading.RLock()
        self._session: Optional[Session] = None

    @classmethod
    def open(cls, database: str | PathLike[str]) -> "LettaMemoryStore":
        """Open a native DuckDB file (never SQLite) and return a store that owns it."""
        return cls(connect_native(database), owns_connection=True)

    def close(self) -> None:
        """Close the connection only if this store opened it; a borrowed one stays open."""
        self._engine.dispose()
        if self._owns:
            self._native.close()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Yield the session every operation inside the block shares.

        An owning store begins one native transaction, commits it when the block
        ends and rolls it back if the block raises. A borrowing store only
        flushes into the caller's transaction. Nested use joins the outer block.
        """
        with self._lock:
            if self._session is not None:
                yield self._session
                return
            session = Session(self._engine, expire_on_commit=False, autoflush=True)
            self._session = session
            began = False
            try:
                if self._owns:
                    self._native.begin()
                    began = True
                yield session
                session.flush()
                if began:
                    began = False
                    self._native.commit()
            except BaseException:
                if began:
                    self._native.rollback()
                raise
            finally:
                self._session = None
                session.close()

    def ensure_schema(self, legacy: LegacyPolicy = "refuse") -> list[LegacyTable]:
        """Create the reserved letta schema and its tables; see letta_duckdb_schema.ensure_schema."""
        with self.transaction() as session:
            return _ensure_schema(session.connection(), legacy)

    # Blocks -----------------------------------------------------------------

    @staticmethod
    def _block_row(session: Session, organization_id: str, block_id: str) -> Block:
        """Return the stored block, or raise LettaNotFound outside the organization."""
        row = session.scalars(
            select(Block).where(
                Block.id == block_id,
                Block.organization_id == organization_id,
                Block.is_deleted.is_(False),
            )
        ).one_or_none()
        if row is None:
            raise LettaNotFound(f"Block {block_id} not found")
        return row

    def create_block(
        self, organization_id: str, block: PydanticBlock, *, actor_id: Optional[str] = None
    ) -> PydanticBlock:
        """Store a validated block in the organization and return it as stored."""
        if not block.label:
            raise LettaIntegrityError("A stored block needs a label")
        with self.transaction() as session:
            row = Block(
                id=block.id,
                label=block.label,
                value=block.value,
                limit=block.limit,
                description=block.description,
                metadata_=block.metadata or {},
                read_only=block.read_only,
                hidden=block.hidden,
                is_template=block.is_template,
                template_name=block.template_name,
                preserve_on_migration=block.preserve_on_migration,
                organization_id=organization_id,
                version=1,
                _created_by_id=actor_id,
                _last_updated_by_id=actor_id,
            )
            session.add(row)
            session.flush()
            return row.to_pydantic()

    def get_block(self, organization_id: str, block_id: str) -> PydanticBlock:
        """Return one block of the organization, or raise LettaNotFound."""
        with self.transaction() as session:
            return self._block_row(session, organization_id, block_id).to_pydantic()

    def list_blocks(
        self, organization_id: str, *, label: Optional[str] = None
    ) -> list[PydanticBlock]:
        """Return the organization's blocks, optionally of one label, ordered by id."""
        statement = select(Block).where(
            Block.organization_id == organization_id, Block.is_deleted.is_(False)
        )
        if label is not None:
            statement = statement.where(Block.label == label)
        with self.transaction() as session:
            return [row.to_pydantic() for row in session.scalars(statement.order_by(Block.id))]

    def update_block(
        self,
        organization_id: str,
        block_id: str,
        expected_version: int,
        *,
        actor_id: Optional[str] = None,
        **changes: Any,
    ) -> PydanticBlock:
        """Change a block if it is still at expected_version; return the new block.

        changes may name value, label, limit, description, metadata, read_only
        and hidden. The result is validated (a value over its limit is refused)
        before anything is written. A label change moves every attachment to the
        new label in the same transaction. Raises StaleBlockVersion if another
        change came first.
        """
        unknown = changes.keys() - _BLOCK_CHANGES
        if unknown:
            raise ValueError(f"Unknown block fields: {sorted(unknown)}")
        with self.transaction() as session:
            return self._update_block(
                session, organization_id, block_id, expected_version, actor_id, changes
            )

    def _update_block(
        self,
        session: Session,
        organization_id: str,
        block_id: str,
        expected_version: int,
        actor_id: Optional[str],
        changes: dict[str, Any],
    ) -> PydanticBlock:
        """Validate and compare-and-set one block change inside session's transaction."""
        current = self._block_row(session, organization_id, block_id).to_pydantic()
        validated = PydanticBlock.model_validate({**current.model_dump(), **changes})
        values: dict[str, Any] = {
            ("metadata_" if name == "metadata" else name): getattr(validated, name)
            for name in changes
        }
        if "label" in changes and changes["label"] != current.label:
            self._move_attachments(session, block_id, current.label, validated.label)
        stored = session.execute(
            update(Block)
            .where(
                Block.id == block_id,
                Block.organization_id == organization_id,
                Block.version == expected_version,
            )
            .values(
                **values,
                version=Block.version + 1,
                updated_at=func.now(),
                _last_updated_by_id=actor_id,
            )
            .returning(Block.version)
            .execution_options(synchronize_session=False)
        ).all()
        if not stored:
            raise StaleBlockVersion(
                f"Block {block_id} is at version {current.version}, not {expected_version}"
            )
        session.expire_all()
        return self._block_row(session, organization_id, block_id).to_pydantic()

    @staticmethod
    def _move_attachments(
        session: Session, block_id: str, old_label: Optional[str], new_label: Optional[str]
    ) -> None:
        """Relabel a block's attachments, refusing a label another attached block holds."""
        agents = select(BlocksAgents.agent_id).where(BlocksAgents.block_id == block_id)
        clash = session.scalars(
            select(BlocksAgents.agent_id).where(
                BlocksAgents.agent_id.in_(agents),
                BlocksAgents.block_label == new_label,
                BlocksAgents.block_id != block_id,
            )
        ).first()
        if clash is not None:
            raise LettaIntegrityError(f"Agent {clash} already has a block labelled {new_label}")
        session.execute(
            update(BlocksAgents)
            .where(BlocksAgents.block_id == block_id, BlocksAgents.block_label == old_label)
            .values(block_label=new_label)
            .execution_options(synchronize_session=False)
        )

    def delete_block(self, organization_id: str, block_id: str) -> None:
        """Delete a block and every attachment of it in one transaction."""
        with self.transaction() as session:
            self._block_row(session, organization_id, block_id)
            session.execute(delete(BlocksAgents).where(BlocksAgents.block_id == block_id))
            session.execute(
                delete(Block).where(Block.id == block_id, Block.organization_id == organization_id)
            )

    # Attachments --------------------------------------------------------------

    def attach_block(self, organization_id: str, agent_id: str, block_id: str) -> None:
        """Attach a block of the organization to an agent under the block's label.

        Raises LettaIntegrityError when the agent already holds this block or
        another block with the same label.
        """
        with self.transaction() as session:
            block = self._block_row(session, organization_id, block_id)
            taken = session.scalars(
                select(BlocksAgents.block_id).where(
                    BlocksAgents.agent_id == agent_id,
                    (BlocksAgents.block_label == block.label) | (BlocksAgents.block_id == block_id),
                )
            ).first()
            if taken is not None:
                raise LettaIntegrityError(
                    f"Agent {agent_id} already holds block {taken} for label {block.label}"
                )
            session.add(BlocksAgents(agent_id=agent_id, block_id=block_id, block_label=block.label))

    def detach_block(self, organization_id: str, agent_id: str, block_id: str) -> None:
        """Detach a block from an agent; raise LettaNotFound if it was not attached."""
        with self.transaction() as session:
            self._block_row(session, organization_id, block_id)
            removed = session.execute(
                delete(BlocksAgents)
                .where(BlocksAgents.agent_id == agent_id, BlocksAgents.block_id == block_id)
                .returning(BlocksAgents.block_id)
            ).all()
            if not removed:
                raise LettaNotFound(f"Block {block_id} is not attached to agent {agent_id}")

    @staticmethod
    def _agent_block_rows(session: Session, organization_id: str, agent_id: str) -> list[Block]:
        """Return the agent's attached blocks of the organization, ordered by label."""
        return list(
            session.scalars(
                select(Block)
                .join(BlocksAgents, BlocksAgents.block_id == Block.id)
                .where(
                    BlocksAgents.agent_id == agent_id,
                    Block.organization_id == organization_id,
                    Block.is_deleted.is_(False),
                )
                .order_by(Block.label)
            )
        )

    def list_agent_blocks(self, organization_id: str, agent_id: str) -> list[PydanticBlock]:
        """Return the blocks attached to an agent within the organization."""
        with self.transaction() as session:
            return [
                row.to_pydantic()
                for row in self._agent_block_rows(session, organization_id, agent_id)
            ]

    def get_agent_memory(self, organization_id: str, agent_id: str) -> Memory:
        """Return the agent's attached blocks as a core Memory, ready to compile."""
        return Memory(blocks=self.list_agent_blocks(organization_id, agent_id))

    def run_memory_tool(
        self,
        organization_id: str,
        agent_id: str,
        tool: Callable[..., T],
        *args: Any,
        actor_id: Optional[str] = None,
        **kwargs: Any,
    ) -> T:
        """Run a memory tool on the agent's stored blocks and store what it changed.

        tool is one of the functions in letta_memory_model.functions.function_sets.base,
        called as tool(agent_state, *args, **kwargs). Every block whose value or
        label it changed is written with a compare-and-set on the version read,
        a block it removed from the memory is detached from the agent, and a
        block it added is created and attached, all in one transaction; if the
        tool raises (read-only block, missing text, limit exceeded) or a block
        changed concurrently, nothing is written. Returns the tool's result.
        """
        with self.transaction() as session:
            rows = self._agent_block_rows(session, organization_id, agent_id)
            before = {row.id: (row.version, row.value, row.label) for row in rows}
            memory = Memory(blocks=[row.to_pydantic() for row in rows])
            result = tool(AgentState(id=agent_id, memory=memory), *args, **kwargs)
            kept = {block.id for block in memory.get_blocks()}
            for block_id in before.keys() - kept:
                self.detach_block(organization_id, agent_id, block_id)
            for block in memory.get_blocks():
                if block.id not in before:
                    continue
                version, value, label = before[block.id]
                changes: dict[str, Any] = {}
                if block.value != value:
                    changes["value"] = block.value
                if block.label != label:
                    changes["label"] = block.label
                if changes:
                    self._update_block(
                        session, organization_id, block.id, version, actor_id, changes
                    )
            for block in memory.get_blocks():
                if block.id not in before:
                    created = self.create_block(organization_id, block, actor_id=actor_id)
                    self.attach_block(organization_id, agent_id, created.id)
            return result

    # Recall: conversations and messages ----------------------------------------

    def create_conversation(
        self, organization_id: str, conversation: PydanticConversation
    ) -> PydanticConversation:
        """Store a conversation thread in the organization and return it as stored."""
        with self.transaction() as session:
            row = Conversation(
                id=conversation.id,
                agent_id=conversation.agent_id,
                summary=conversation.summary,
                model=conversation.model,
                model_settings=conversation.model_settings,
                last_message_at=conversation.last_message_at,
                organization_id=organization_id,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return row.to_pydantic()

    def delete_conversation(self, organization_id: str, conversation_id: str) -> int:
        """Delete a conversation and clear conversation_id on its messages, atomically.

        This is the original ON DELETE SET NULL rule. Returns how many messages
        were detached; raises LettaNotFound outside the organization.
        """
        with self.transaction() as session:
            found = session.scalars(
                select(Conversation.id).where(
                    Conversation.id == conversation_id,
                    Conversation.organization_id == organization_id,
                )
            ).one_or_none()
            if found is None:
                raise LettaNotFound(f"Conversation {conversation_id} not found")
            detached = session.execute(
                update(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.organization_id == organization_id,
                )
                .values(conversation_id=None, updated_at=func.now())
                .returning(Message.id)
                .execution_options(synchronize_session=False)
            ).all()
            session.execute(delete(Conversation).where(Conversation.id == conversation_id))
            return len(detached)

    def add_messages(
        self,
        organization_id: str,
        messages: Sequence[PydanticMessage],
        *,
        actor_id: Optional[str] = None,
    ) -> list[PydanticMessage]:
        """Append messages to recall in order and return them with their sequence_id.

        Each message needs an agent_id; a conversation_id must name a
        conversation of the same organization and agent, as the original
        foreign key required.
        """
        with self.transaction() as session:
            for message in messages:
                if not message.agent_id:
                    raise LettaIntegrityError(f"Message {message.id} has no agent_id")
                if message.conversation_id is not None:
                    owner = session.scalars(
                        select(Conversation.agent_id).where(
                            Conversation.id == message.conversation_id,
                            Conversation.organization_id == organization_id,
                        )
                    ).one_or_none()
                    if owner != message.agent_id:
                        raise LettaIntegrityError(
                            f"Conversation {message.conversation_id} is not a conversation "
                            f"of agent {message.agent_id}"
                        )
                fields = message.model_dump(exclude={"sequence_id", "created_at"})
                session.add(
                    Message(
                        **fields,
                        organization_id=organization_id,
                        _created_by_id=actor_id,
                        _last_updated_by_id=actor_id,
                    )
                )
                session.flush()
            ids = [message.id for message in messages]
            rows = session.scalars(
                select(Message).where(Message.id.in_(ids)).order_by(Message.sequence_id)
            ).all()
            for row in rows:
                session.refresh(row)
            return [row.to_pydantic() for row in rows]

    def list_messages(
        self,
        organization_id: str,
        agent_id: str,
        *,
        conversation_id: Optional[str] = None,
        after_sequence_id: Optional[int] = None,
        roles: Optional[Sequence[str]] = None,
        query_text: Optional[str] = None,
        otid: Optional[str] = None,
        limit: Optional[int] = None,
        newest_first: bool = False,
    ) -> list[PydanticMessage]:
        """Return the agent's messages in stored order, filtered as asked.

        query_text matches message text case-insensitively as a substring and
        otid matches the message's offline threading id exactly. limit, when
        given, keeps the earliest matching messages, or with newest_first the
        latest ones, returned latest first.
        """
        statement = select(Message).where(
            Message.organization_id == organization_id,
            Message.agent_id == agent_id,
            Message.is_deleted.is_(False),
        )
        if conversation_id is not None:
            statement = statement.where(Message.conversation_id == conversation_id)
        if after_sequence_id is not None:
            statement = statement.where(Message.sequence_id > after_sequence_id)
        if roles is not None:
            statement = statement.where(Message.role.in_(list(roles)))
        if query_text is not None:
            statement = statement.where(Message.text.icontains(query_text, autoescape=True))
        if otid is not None:
            statement = statement.where(Message.otid == otid)
        statement = statement.order_by(
            Message.sequence_id.desc() if newest_first else Message.sequence_id
        )
        if limit is not None:
            statement = statement.limit(limit)
        with self.transaction() as session:
            return [row.to_pydantic() for row in session.scalars(statement)]

    def list_message_agent_ids(self, organization_id: str) -> list[str]:
        """Return, sorted, every agent id that has a current message in the organization."""
        statement = (
            select(Message.agent_id)
            .where(Message.organization_id == organization_id, Message.is_deleted.is_(False))
            .distinct()
            .order_by(Message.agent_id)
        )
        with self.transaction() as session:
            return [agent for agent in session.scalars(statement) if agent is not None]

    def correct_message(
        self,
        organization_id: str,
        message_id: str,
        *,
        text: Optional[str] = None,
        content: Optional[list[dict]] = None,
        actor_id: Optional[str] = None,
    ) -> PydanticMessage:
        """Replace a message's text and content parts in place and return it."""
        with self.transaction() as session:
            changed = session.execute(
                update(Message)
                .where(Message.id == message_id, Message.organization_id == organization_id)
                .values(
                    text=text, content=content, updated_at=func.now(), _last_updated_by_id=actor_id
                )
                .returning(Message.id)
                .execution_options(synchronize_session=False)
            ).all()
            if not changed:
                raise LettaNotFound(f"Message {message_id} not found")
            return (
                session.scalars(select(Message).where(Message.id == message_id)).one().to_pydantic()
            )

    def delete_messages(
        self, organization_id: str, agent_id: str, message_ids: Sequence[str]
    ) -> int:
        """Delete the named messages of the agent; return how many were deleted."""
        with self.transaction() as session:
            return len(
                session.execute(
                    delete(Message)
                    .where(
                        Message.organization_id == organization_id,
                        Message.agent_id == agent_id,
                        Message.id.in_(list(message_ids)),
                    )
                    .returning(Message.id)
                ).all()
            )

    # Passages -------------------------------------------------------------------

    @staticmethod
    def _passage_tags(
        session: Session, organization_id: str, passage: ArchivalPassage, tags: Sequence[str]
    ) -> list[str]:
        """Replace an archival passage's tag rows with the distinct tags given."""
        distinct = list(dict.fromkeys(tags))
        session.execute(delete(PassageTag).where(PassageTag.passage_id == passage.id))
        session.add_all(
            PassageTag(
                id=f"passage_tag-{uuid.uuid4()}",
                tag=tag,
                passage_id=passage.id,
                archive_id=passage.archive_id,
                organization_id=organization_id,
            )
            for tag in distinct
        )
        return distinct

    def insert_archival_passage(
        self,
        organization_id: str,
        archive_id: str,
        passage: PydanticPassage,
        *,
        actor_id: Optional[str] = None,
    ) -> PydanticPassage:
        """Store an archival passage, its embedding and tag rows; return it as stored."""
        with self.transaction() as session:
            row = ArchivalPassage(
                id=passage.id,
                text=passage.text,
                embedding=passage.embedding,
                embedding_config=passage.embedding_config,
                metadata_=passage.metadata or {},
                archive_id=archive_id,
                organization_id=organization_id,
                _created_by_id=actor_id,
                _last_updated_by_id=actor_id,
            )
            row.tags = self._passage_tags(session, organization_id, row, passage.tags or [])
            session.add(row)
            session.flush()
            session.refresh(row)
            return row.to_pydantic()

    @staticmethod
    def _archival_row(session: Session, organization_id: str, passage_id: str) -> ArchivalPassage:
        """Return the stored archival passage, or raise LettaNotFound."""
        row = session.scalars(
            select(ArchivalPassage).where(
                ArchivalPassage.id == passage_id,
                ArchivalPassage.organization_id == organization_id,
                ArchivalPassage.is_deleted.is_(False),
            )
        ).one_or_none()
        if row is None:
            raise LettaNotFound(f"Passage {passage_id} not found")
        return row

    def get_archival_passage(self, organization_id: str, passage_id: str) -> PydanticPassage:
        """Return one archival passage of the organization, or raise LettaNotFound."""
        with self.transaction() as session:
            return self._archival_row(session, organization_id, passage_id).to_pydantic()

    def correct_archival_passage(
        self,
        organization_id: str,
        passage_id: str,
        *,
        text: str,
        embedding: Optional[list[float]],
        tags: Optional[Sequence[str]] = None,
        actor_id: Optional[str] = None,
    ) -> PydanticPassage:
        """Replace a passage's text and its embedding together, and its tags if given.

        The embedding must be the new text's embedding (None if not embedded),
        so search never finds the old text's vector under the new text.
        """
        with self.transaction() as session:
            row = self._archival_row(session, organization_id, passage_id)
            row.text = text
            row.embedding = embedding
            if tags is not None:
                row.tags = self._passage_tags(session, organization_id, row, tags)
            row._last_updated_by_id = actor_id
            session.flush()
            session.execute(
                update(ArchivalPassage)
                .where(ArchivalPassage.id == passage_id)
                .values(updated_at=func.now())
                .execution_options(synchronize_session=False)
            )
            session.refresh(row)
            return row.to_pydantic()

    def delete_archival_passage(self, organization_id: str, passage_id: str) -> None:
        """Delete a passage and its tag rows in one transaction (the original cascade)."""
        with self.transaction() as session:
            self._archival_row(session, organization_id, passage_id)
            session.execute(delete(PassageTag).where(PassageTag.passage_id == passage_id))
            session.execute(delete(ArchivalPassage).where(ArchivalPassage.id == passage_id))

    def delete_archive(self, organization_id: str, archive_id: str) -> int:
        """Delete every passage of an archive and their tags; return how many passages."""
        with self.transaction() as session:
            session.execute(
                delete(PassageTag).where(
                    PassageTag.archive_id == archive_id,
                    PassageTag.organization_id == organization_id,
                )
            )
            return len(
                session.execute(
                    delete(ArchivalPassage)
                    .where(
                        ArchivalPassage.archive_id == archive_id,
                        ArchivalPassage.organization_id == organization_id,
                    )
                    .returning(ArchivalPassage.id)
                ).all()
            )

    @staticmethod
    def _nearest(
        statement: Select, embedding_column: Any, query_embedding: Sequence[float], limit: int
    ) -> Select:
        """Order by cosine distance to the query among vectors of the query's dimension."""
        query = cast(literal(list(query_embedding), ARRAY(Float)), ARRAY(Float))
        distance = func.list_cosine_distance(embedding_column, query)
        return (
            statement.add_columns(distance.label("distance"))
            .where(
                embedding_column.is_not(None),
                func.len(embedding_column) == len(query_embedding),
            )
            .order_by(distance)
            .limit(limit)
        )

    def search_archival_passages(
        self,
        organization_id: str,
        archive_id: str,
        query_embedding: Sequence[float],
        limit: int,
        *,
        tags: Optional[Sequence[str]] = None,
        match_all_tags: bool = False,
    ) -> list[tuple[PydanticPassage, float]]:
        """Return the archive's passages nearest the query vector, with cosine distances.

        tags keeps passages holding any (or, with match_all_tags, every) tag.
        Only current rows are read, so a corrected or deleted passage's old
        text and vector are never returned.
        """
        statement = select(ArchivalPassage).where(
            ArchivalPassage.organization_id == organization_id,
            ArchivalPassage.archive_id == archive_id,
            ArchivalPassage.is_deleted.is_(False),
        )
        wanted = list(dict.fromkeys(tags or []))
        if wanted and match_all_tags:
            for tag in wanted:
                statement = statement.where(
                    exists().where(
                        PassageTag.passage_id == ArchivalPassage.id, PassageTag.tag == tag
                    )
                )
        elif wanted:
            statement = statement.where(
                exists().where(
                    PassageTag.passage_id == ArchivalPassage.id, PassageTag.tag.in_(wanted)
                )
            )
        statement = self._nearest(statement, ArchivalPassage.embedding, query_embedding, limit)
        with self.transaction() as session:
            return [
                (row.to_pydantic(), float(distance)) for row, distance in session.execute(statement)
            ]

    def insert_source_passage(
        self,
        organization_id: str,
        source_id: str,
        file_id: Optional[str],
        file_name: str,
        passage: PydanticPassage,
        *,
        actor_id: Optional[str] = None,
    ) -> PydanticPassage:
        """Store a passage cut from a source file and return it as stored."""
        with self.transaction() as session:
            row = SourcePassage(
                id=passage.id,
                text=passage.text,
                embedding=passage.embedding,
                embedding_config=passage.embedding_config,
                metadata_=passage.metadata or {},
                tags=passage.tags,
                source_id=source_id,
                file_id=file_id,
                file_name=file_name,
                organization_id=organization_id,
                _created_by_id=actor_id,
                _last_updated_by_id=actor_id,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return row.to_pydantic()

    def search_source_passages(
        self,
        organization_id: str,
        source_ids: Sequence[str],
        query_embedding: Sequence[float],
        limit: int,
    ) -> list[tuple[PydanticPassage, float]]:
        """Return passages of the given sources nearest the query vector, with distances."""
        statement = select(SourcePassage).where(
            SourcePassage.organization_id == organization_id,
            SourcePassage.source_id.in_(list(source_ids)),
            SourcePassage.is_deleted.is_(False),
        )
        statement = self._nearest(statement, SourcePassage.embedding, query_embedding, limit)
        with self.transaction() as session:
            return [
                (row.to_pydantic(), float(distance)) for row, distance in session.execute(statement)
            ]

    def delete_source_passages(
        self, organization_id: str, source_id: str, *, file_id: Optional[str] = None
    ) -> int:
        """Delete a source's passages, or one file's; return how many were deleted."""
        statement = delete(SourcePassage).where(
            SourcePassage.organization_id == organization_id, SourcePassage.source_id == source_id
        )
        if file_id is not None:
            statement = statement.where(SourcePassage.file_id == file_id)
        with self.transaction() as session:
            return len(session.execute(statement.returning(SourcePassage.id)).all())
