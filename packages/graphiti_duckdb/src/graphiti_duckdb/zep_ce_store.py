"""Zep Community Edition's users, sessions, messages and memory store, on one DuckDB handle.

This is a translation of the store layer of Zep CE v1.0.2 (getzep/zep, Apache-2.0), which kept
users, sessions and messages in Postgres through Go. The Go files it follows are
src/store/userstore_common.go, sessionstore_common.go, message_common.go,
memorystore_common.go, memory_common.go, memory_ce.go, userstore_ce.go, sessionstore_ce.go,
purge_common.go, purge_ce.go and metadata_utils.go, the tables in
src/store/migrations/000000000001_database_setup.up.sql, the models in src/models and the error
kinds in src/lib/zerrors/errors.go.

The store keeps each fact of the original: soft deletes with a purge, a session that has ended
refusing updates, a session update that undeletes, metadata merged key by key and a top-level
"system" key only a privileged caller may set, cursor and page listings, and the last-N message
cut. Relevant facts and the graph nodes of users and sessions belong to Graphiti in the
original. Here a graph service is optional: with none attached, the graph steps are written to
an owed-work table and facts come back empty with a stated reason, never as a silent success.
It never calls a language model, never makes an embedding and never reads another schema.
"""

import copy
import json
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol
from uuid import UUID, uuid4

import duckdb

SCHEMA_NAME = "zep_ce"
DEFAULT_LAST_N_MESSAGES = 4
MAX_MESSAGES_FOR_FACT_RETRIEVAL = 4
MEMORY_FACTS_LIMIT = 5
ROLE_TYPES = ("norole", "system", "assistant", "user", "function", "tool")
NO_GRAPH_REASON = "no graph service is attached, so no facts can be retrieved"

_ORDERABLE_COLUMNS = {
    "users": (
        "id",
        "uuid",
        "created_at",
        "updated_at",
        "user_id",
        "email",
        "first_name",
        "last_name",
    ),
    "sessions": ("id", "uuid", "created_at", "updated_at", "ended_at", "session_id", "user_id"),
}


class ZepError(Exception):
    """Base of every refusal the Zep CE store raises; nothing was written when it is raised."""


class NotFoundError(ZepError, LookupError):
    """Raised when the named user, session or message does not exist for this project."""


class BadRequestError(ZepError, ValueError):
    """Raised when a request is empty, duplicated or names something that does not exist."""


class SessionEndedError(ZepError):
    """Raised when a session that has ended is asked to take an update or new messages."""


@dataclass
class Message:
    """One chat message: who said it, what, how many tokens, and free-form metadata."""

    content: str
    role: str
    role_type: str = "norole"
    token_count: int = 0
    metadata: Optional[dict[str, Any]] = None
    uuid: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class Fact:
    """A fact the graph returned for a conversation: its text, id and the time it holds from."""

    fact: str
    uuid: str
    created_at: datetime


@dataclass
class Memory:
    """Recent messages and relevant facts; facts_unavailable says why no facts were returned."""

    messages: list[Message] = field(default_factory=list)
    relevant_facts: list[Fact] = field(default_factory=list)
    metadata: Optional[dict[str, Any]] = None
    facts_unavailable: Optional[str] = None


@dataclass
class User:
    """A stored user of one project, with the number of its live sessions."""

    uuid: UUID
    id: int
    created_at: datetime
    updated_at: Optional[datetime]
    user_id: str
    email: str
    first_name: str
    last_name: str
    project_uuid: UUID
    metadata: Optional[dict[str, Any]]
    session_count: int = 0


@dataclass
class Session:
    """A stored chat session, optionally owned by a user, optionally ended."""

    uuid: UUID
    id: int
    created_at: datetime
    updated_at: datetime
    ended_at: Optional[datetime]
    session_id: str
    metadata: Optional[dict[str, Any]]
    user_id: Optional[str]
    project_uuid: UUID


@dataclass
class UserList:
    """One page of users with the project's total count."""

    users: list[User]
    total_count: int
    row_count: int


@dataclass
class SessionList:
    """One page of sessions with the project's total count."""

    sessions: list[Session]
    total_count: int
    row_count: int


@dataclass
class MessageList:
    """One page of a session's messages with the session's total count."""

    messages: list[Message]
    total_count: int
    row_count: int


@dataclass
class GraphFact:
    """A fact as the graph service returns it, with the time it became valid, when known."""

    fact: str
    uuid: str
    created_at: datetime
    valid_at: Optional[datetime] = None


class ZepGraph(Protocol):
    """The Graphiti-backed service Zep CE called: nodes, groups, ingest, facts and search.

    An implementation takes group ids (a user id or a session id) and returns plain values. The
    store never runs a model itself; whatever extraction or embedding a graph needs is its own.
    """

    def add_node(self, group_id: str, node_uuid: str, name: str, summary: str) -> None:
        """Create the named entity node in a group."""

    def delete_group(self, group_id: str) -> None:
        """Delete everything the graph holds for a group."""

    def put_memory(self, group_id: str, messages: Sequence[Message], skip_notify: bool) -> None:
        """Hand messages to the graph for a group."""

    def get_memory(
        self, group_id: str, max_facts: int, messages: Sequence[Message]
    ) -> list[GraphFact]:
        """Return up to max_facts facts relevant to the messages."""

    def search(self, group_ids: Sequence[str], text: str, max_facts: int) -> list[GraphFact]:
        """Return up to max_facts facts matching the text across the groups."""


def _now() -> datetime:
    """Return the current moment in UTC."""
    return datetime.now(timezone.utc)


def _load(value: Any) -> Optional[dict[str, Any]]:
    """Read a stored JSON metadata cell back into a dictionary, or None when it is empty."""
    return None if value is None else json.loads(value)


def _is_empty(value: Any) -> bool:
    """Say whether a value is Go's zero or empty value: None, an empty list or an empty map."""
    return value is None or (isinstance(value, (list, dict)) and not value)


def merge_metadata(
    stored: dict[str, Any], incoming: dict[str, Any], is_privileged: bool
) -> dict[str, Any]:
    """Merge incoming metadata into stored metadata the way Zep's mergeMetadata does.

    Goes in: the stored map, the incoming map and whether the caller is privileged; comes out:
    a new map. The incoming map's top-level "system" key is dropped unless privileged. A key
    holding a map in both is merged key by key; a non-empty list replaces the stored list, an
    empty list leaves it; any other value, including null, replaces what was stored. It never
    changes either input. Raises ValueError for an empty incoming map, as the original does.
    """
    if not incoming:
        raise ValueError("metadata cannot be empty")
    wanted = {k: v for k, v in incoming.items() if is_privileged or k != "system"}
    return _merge_into(copy.deepcopy(stored), copy.deepcopy(wanted))


def _merge_into(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    """Merge src into dst in place with override semantics and return dst."""
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _merge_into(dst[key], value)
        elif isinstance(value, list) and key in dst and _is_empty(value):
            continue
        else:
            dst[key] = value
    return dst


class ZepCeStore:
    """Zep CE's user, session, message and memory operations for one project.

    It takes a native DuckDB handle, the project's uuid, whether it owns the handle and an
    optional graph service. Every read and write is limited to that project. A store that owns
    its handle wraps each operation in a transaction; a borrowed handle runs inside the caller's
    transaction. It never calls a model and never changes a table outside its own schema.
    """

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        project_uuid: UUID,
        *,
        owns_connection: bool = False,
        graph: Optional[ZepGraph] = None,
    ) -> None:
        """Bind the handle and project, and name the tables with the database spelled out."""
        if project_uuid.int == 0:
            raise BadRequestError("ProjectUUID cannot be empty")
        self._db = connection
        self._owns = owns_connection
        self._project = project_uuid
        self._graph = graph
        self._lock = threading.RLock()
        self._depth = 0
        row = connection.execute("SELECT current_database()").fetchone()
        catalog = row[0] if row else "memory"
        prefix = f'"{catalog}"."{SCHEMA_NAME}"'
        self._users = f"{prefix}.users"
        self._sessions = f"{prefix}.sessions"
        self._messages = f"{prefix}.messages"
        self._owed = f"{prefix}.graph_owed"
        self._prefix = prefix
        self._catalog = catalog

    @classmethod
    def open(
        cls, database: str, project_uuid: UUID, *, graph: Optional[ZepGraph] = None
    ) -> "ZepCeStore":
        """Open a DuckDB file, make the tables, and return a store that owns the handle."""
        store = cls(duckdb.connect(database), project_uuid, owns_connection=True, graph=graph)
        store.ensure_schema()
        return store

    def close(self) -> None:
        """Close the handle only if this store opened it."""
        if self._owns:
            self._db.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Run the block in one transaction; nested use joins the outer one; errors roll back."""
        with self._lock:
            if self._depth > 0 or not self._owns:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            self._db.begin()
            self._depth = 1
            try:
                yield
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise
            finally:
                self._depth = 0

    def ensure_schema(self) -> None:
        """Create the schema, the three tables, their counters and the owed-graph-work table."""
        with self._transaction():
            self._db.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._catalog}"."{SCHEMA_NAME}"')
            for table in ("users", "sessions", "messages"):
                self._db.execute(f"CREATE SEQUENCE IF NOT EXISTS {self._prefix}.{table}_id_seq")
            self._db.execute(
                f"""CREATE TABLE IF NOT EXISTS {self._users} (
                    uuid UUID PRIMARY KEY,
                    id BIGINT DEFAULT nextval('{self._prefix}.users_id_seq'),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now(),
                    deleted_at TIMESTAMPTZ,
                    user_id VARCHAR NOT NULL UNIQUE,
                    email VARCHAR, first_name VARCHAR, last_name VARCHAR,
                    project_uuid UUID NOT NULL,
                    metadata JSON)"""
            )
            self._db.execute(
                f"""CREATE TABLE IF NOT EXISTS {self._sessions} (
                    uuid UUID PRIMARY KEY,
                    id BIGINT DEFAULT nextval('{self._prefix}.sessions_id_seq'),
                    session_id VARCHAR NOT NULL UNIQUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    deleted_at TIMESTAMPTZ,
                    ended_at TIMESTAMPTZ,
                    metadata JSON,
                    user_id VARCHAR,
                    project_uuid UUID NOT NULL)"""
            )
            self._db.execute(
                f"""CREATE TABLE IF NOT EXISTS {self._messages} (
                    uuid UUID PRIMARY KEY,
                    id BIGINT DEFAULT nextval('{self._prefix}.messages_id_seq'),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now(),
                    deleted_at TIMESTAMPTZ,
                    session_id VARCHAR NOT NULL,
                    project_uuid UUID NOT NULL,
                    role VARCHAR NOT NULL,
                    role_type VARCHAR DEFAULT 'norole',
                    content VARCHAR NOT NULL,
                    token_count BIGINT NOT NULL,
                    metadata JSON)"""
            )
            self._db.execute(
                f"""CREATE TABLE IF NOT EXISTS {self._owed} (
                    seq BIGINT DEFAULT nextval('{self._prefix}.users_id_seq'),
                    operation VARCHAR NOT NULL,
                    group_id VARCHAR NOT NULL,
                    detail JSON,
                    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
            )

    # Graph hand-off ---------------------------------------------------------------------

    def _graph_step(self, operation: str, group_id: str, detail: dict[str, Any]) -> None:
        """Call the attached graph for a step, or record the step as owed when none is attached."""
        if self._graph is None:
            self._db.execute(
                f"INSERT INTO {self._owed} (operation, group_id, detail) VALUES (?, ?, ?)",
                [operation, group_id, json.dumps(detail, default=str)],
            )
            return
        if operation == "add_node":
            self._graph.add_node(group_id, detail["uuid"], detail["name"], detail["summary"])
        elif operation == "delete_group":
            self._graph.delete_group(group_id)
        else:
            raise ValueError(f"Unknown graph step {operation!r}")

    def owed_graph_work(self) -> list[dict[str, Any]]:
        """Return, oldest first, the graph steps recorded because no graph service was attached."""
        with self._transaction():
            rows = self._db.execute(
                f"SELECT operation, group_id, detail, recorded_at FROM {self._owed} ORDER BY seq"
            ).fetchall()
        return [
            {"operation": op, "group_id": group, "detail": json.loads(detail), "recorded_at": at}
            for op, group, detail, at in rows
        ]

    # Users --------------------------------------------------------------------------------

    def _user_from_row(self, row: Sequence[Any], session_count: int) -> User:
        """Build a User from a users row in column order and the live session count."""
        return User(
            uuid=row[0],
            id=row[1],
            created_at=row[2],
            updated_at=row[3],
            user_id=row[5],
            email=row[6] or "",
            first_name=row[7] or "",
            last_name=row[8] or "",
            project_uuid=row[9],
            metadata=_load(row[10]),
            session_count=session_count,
        )

    _USER_COLUMNS = (
        "uuid, id, created_at, updated_at, deleted_at, user_id, email, first_name, last_name, "
        "project_uuid, metadata"
    )

    def create_user(
        self,
        user_id: str,
        email: str = "",
        first_name: str = "",
        last_name: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> User:
        """Create a user and queue its graph node; refuse an empty or already used user_id."""
        if not user_id:
            raise BadRequestError("UserID cannot be empty")
        with self._transaction():
            exists = self._db.execute(
                f"SELECT 1 FROM {self._users} WHERE user_id = ?", [user_id]
            ).fetchone()
            if exists:
                raise BadRequestError("user already exists with user_id: " + user_id)
            self._db.execute(
                f"INSERT INTO {self._users} (uuid, user_id, email, first_name, last_name, "
                "project_uuid, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    uuid4(),
                    user_id,
                    email,
                    first_name,
                    last_name,
                    self._project,
                    None if metadata is None else json.dumps(metadata),
                ],
            )
            created = self.get_user(user_id)
            created.session_count = 0
            name = f"User {first_name} {last_name}"
            self._graph_step("add_node", user_id, {"uuid": user_id, "name": name, "summary": name})
            return created

    def get_user(self, user_id: str) -> User:
        """Return one live user of the project with its session count, or raise NotFoundError."""
        with self._transaction():
            row = self._db.execute(
                f"SELECT {self._USER_COLUMNS} FROM {self._users} "
                "WHERE user_id = ? AND project_uuid = ? AND deleted_at IS NULL",
                [user_id, self._project],
            ).fetchone()
            if row is None:
                raise NotFoundError("user " + user_id + " not found")
            return self._user_from_row(row, len(self.sessions_for_user(user_id)))

    def update_user(
        self,
        user_id: str,
        email: str = "",
        first_name: str = "",
        last_name: str = "",
        metadata: Optional[dict[str, Any]] = None,
        *,
        is_privileged: bool = False,
    ) -> User:
        """Change a user's email, names and metadata; empty strings and no metadata change nothing.

        Metadata is merged into what is stored (see merge_metadata). Raises NotFoundError for an
        unknown user and ValueError for an empty user_id.
        """
        if not user_id:
            raise ValueError("UserID cannot be empty")
        with self._transaction():
            merged = metadata
            if metadata:
                stored = self._db.execute(
                    f"SELECT metadata FROM {self._users} WHERE user_id = ?", [user_id]
                ).fetchone()
                if stored is None:
                    raise NotFoundError(f"user_id {user_id} not found")
                merged = merge_metadata(_load(stored[0]) or {}, metadata, is_privileged)
            sets, values = ["updated_at = ?"], [_now()]
            for column, value in (
                ("email", email),
                ("first_name", first_name),
                ("last_name", last_name),
            ):
                if value:
                    sets.append(f"{column} = ?")
                    values.append(value)
            if merged:
                sets.append("metadata = ?")
                values.append(json.dumps(merged))
            changed = self._db.execute(
                f"UPDATE {self._users} SET {', '.join(sets)} "
                "WHERE user_id = ? AND project_uuid = ? AND deleted_at IS NULL RETURNING uuid",
                [*values, user_id, self._project],
            ).fetchall()
            if not changed:
                raise NotFoundError("user " + user_id + " not found")
            return self.get_user(user_id)

    def delete_user(self, user_id: str) -> None:
        """Delete a user and all of its sessions and their messages, then purge what was deleted.

        Raises NotFoundError for an unknown user. The graph groups of the user and of each of its
        sessions are queued for deletion.
        """
        with self._transaction():
            sessions = self.sessions_for_user(user_id)
            for session in sessions:
                self.delete_session(session.session_id)
            removed = self._db.execute(
                f"DELETE FROM {self._users} WHERE project_uuid = ? AND user_id = ? RETURNING uuid",
                [self._project, user_id],
            ).fetchall()
            if not removed:
                raise NotFoundError("user " + user_id + " not found")
            self._graph_step("delete_group", user_id, {})
            for session in sessions:
                self._graph_step("delete_group", session.session_id, {})
            self._purge_soft_deleted()

    def list_users(self, cursor: int, limit: int) -> list[User]:
        """Return up to limit live users of the project whose id is above cursor, by id."""
        with self._transaction():
            rows = self._db.execute(
                f"SELECT {self._USER_COLUMNS} FROM {self._users} "
                "WHERE project_uuid = ? AND deleted_at IS NULL AND id > ? ORDER BY id ASC LIMIT ?",
                [self._project, cursor, limit],
            ).fetchall()
            return [self._user_from_row(r, len(self.sessions_for_user(r[5]))) for r in rows]

    def list_users_ordered(
        self, page_number: int, page_size: int, order_by: str = "", ascending: bool = False
    ) -> UserList:
        """Return one page of users ordered by a column (default id, newest first) and the total."""
        column = self._order_column("users", order_by)
        direction = "ASC" if ascending else "DESC"
        with self._transaction():
            rows = self._db.execute(
                f"SELECT {self._USER_COLUMNS} FROM {self._users} "
                f"WHERE project_uuid = ? AND deleted_at IS NULL ORDER BY {column} {direction} "
                "LIMIT ? OFFSET ?",
                [self._project, page_size, (page_number - 1) * page_size],
            ).fetchall()
            total = self._db.execute(
                f"SELECT count(*) FROM {self._users} WHERE project_uuid = ? AND deleted_at IS NULL",
                [self._project],
            ).fetchone()
            users = [self._user_from_row(r, len(self.sessions_for_user(r[5]))) for r in rows]
            return UserList(users, int(total[0]) if total else 0, len(users))

    def sessions_for_user(self, user_id: str) -> list[Session]:
        """Return the user's live sessions of this project."""
        with self._transaction():
            rows = self._db.execute(
                f"SELECT {self._SESSION_COLUMNS} FROM {self._sessions} s "
                f"JOIN {self._users} u ON u.user_id = s.user_id "
                "WHERE s.project_uuid = ? AND s.deleted_at IS NULL AND u.user_id = ? "
                "ORDER BY s.id",
                [self._project, user_id],
            ).fetchall()
            return [self._session_from_row(r) for r in rows]

    @staticmethod
    def _order_column(table: str, name: str) -> str:
        """Return a column name safe to order by; refuse a name the table does not have."""
        column = name or "id"
        if column not in _ORDERABLE_COLUMNS[table]:
            raise BadRequestError(
                f"cannot order {table} by {column!r}; use one of {list(_ORDERABLE_COLUMNS[table])}"
            )
        return column

    # Sessions -----------------------------------------------------------------------------

    _SESSION_COLUMNS = (
        "s.uuid, s.id, s.created_at, s.updated_at, s.ended_at, s.session_id, s.metadata, "
        "s.user_id, s.project_uuid"
    )

    @staticmethod
    def _session_from_row(row: Sequence[Any]) -> Session:
        """Build a Session from a sessions row in column order."""
        return Session(
            uuid=row[0],
            id=row[1],
            created_at=row[2],
            updated_at=row[3],
            ended_at=row[4],
            session_id=row[5],
            metadata=_load(row[6]),
            user_id=row[7],
            project_uuid=row[8],
        )

    def create_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Session:
        """Create a session, optionally for a user, and queue its graph node.

        Refuses an empty id, an id already used, and a user that does not exist, each as
        BadRequestError with the original's wording.
        """
        if not session_id:
            raise BadRequestError("sessionID cannot be empty")
        with self._transaction():
            if user_id is not None:
                owner = self._db.execute(
                    f"SELECT 1 FROM {self._users} WHERE user_id = ?", [user_id]
                ).fetchone()
                if not owner:
                    raise BadRequestError("user does not exist with user_id: " + user_id)
            taken = self._db.execute(
                f"SELECT 1 FROM {self._sessions} WHERE session_id = ?", [session_id]
            ).fetchone()
            if taken:
                raise BadRequestError("session already exists with session_id: " + session_id)
            self._db.execute(
                f"INSERT INTO {self._sessions} (uuid, session_id, user_id, metadata, project_uuid) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    uuid4(),
                    session_id,
                    user_id,
                    None if metadata is None else json.dumps(metadata),
                    self._project,
                ],
            )
            if user_id is not None:
                user = self.get_user(user_id)
                name = f"User {user.first_name} {user.last_name}"
                self._graph_step(
                    "add_node",
                    session_id,
                    {"uuid": f"{session_id}_{user_id}", "name": name, "summary": name},
                )
            return self._session_row(session_id, include_deleted=False)

    def _session_row(self, session_id: str, *, include_deleted: bool) -> Session:
        """Return a session of this project by id, optionally including a soft-deleted one."""
        deleted = "" if include_deleted else "AND s.deleted_at IS NULL"
        row = self._db.execute(
            f"SELECT {self._SESSION_COLUMNS} FROM {self._sessions} s "
            f"WHERE s.session_id = ? AND s.project_uuid = ? {deleted}",
            [session_id, self._project],
        ).fetchone()
        if row is None:
            raise NotFoundError("session " + session_id + " not found")
        return self._session_from_row(row)

    def get_session(self, session_id: str) -> Session:
        """Return one live session of the project, or raise NotFoundError."""
        with self._transaction():
            return self._session_row(session_id, include_deleted=False)

    def update_session(
        self,
        session_id: str,
        metadata: Optional[dict[str, Any]] = None,
        *,
        is_privileged: bool = False,
    ) -> Session:
        """Merge metadata into a session and undelete it if it was soft-deleted.

        Goes in: the id, optional metadata, and whether the caller is privileged. Raises
        BadRequestError for an empty id, NotFoundError for an unknown one and SessionEndedError
        when the session has ended. Messages are not undeleted. Calling it with no metadata
        only undeletes and touches the session.
        """
        if not session_id:
            raise BadRequestError("sessionID cannot be empty")
        with self._transaction():
            current = self._session_row(session_id, include_deleted=True)
            if current.ended_at is not None:
                raise SessionEndedError("session has ended")
            merged = metadata
            if metadata:
                merged = merge_metadata(current.metadata or {}, metadata, is_privileged)
            sets, values = ["deleted_at = NULL", "updated_at = ?"], [_now()]
            if merged is not None:
                sets.append("metadata = ?")
                values.append(json.dumps(merged))
            self._db.execute(
                f"UPDATE {self._sessions} SET {', '.join(sets)} "
                "WHERE session_id = ? AND project_uuid = ?",
                [*values, session_id, self._project],
            )
            return self._session_row(session_id, include_deleted=False)

    def delete_session(self, session_id: str) -> None:
        """Soft-delete a session and its messages, then purge every soft-deleted row.

        Raises NotFoundError for an unknown session. The original purges right away, so the
        rows are gone when this returns.
        """
        with self._transaction():
            stamp = _now()
            hit = self._db.execute(
                f"UPDATE {self._sessions} SET deleted_at = ? "
                "WHERE session_id = ? AND deleted_at IS NULL RETURNING uuid",
                [stamp, session_id],
            ).fetchall()
            if not hit:
                raise NotFoundError("session " + session_id + " not found")
            self._db.execute(
                f"UPDATE {self._messages} SET deleted_at = ? "
                "WHERE session_id = ? AND deleted_at IS NULL",
                [stamp, session_id],
            )
            self._purge_soft_deleted()

    def list_sessions(self, cursor: int, limit: int) -> list[Session]:
        """Return up to limit live sessions of the project whose id is above cursor, by id."""
        with self._transaction():
            rows = self._db.execute(
                f"SELECT {self._SESSION_COLUMNS} FROM {self._sessions} s "
                "WHERE s.project_uuid = ? AND s.deleted_at IS NULL AND s.id > ? "
                "ORDER BY s.id ASC LIMIT ?",
                [self._project, cursor, limit],
            ).fetchall()
            return [self._session_from_row(r) for r in rows]

    def list_sessions_ordered(
        self, page_number: int, page_size: int, order_by: str = "", ascending: bool = False
    ) -> SessionList:
        """Return one page of sessions ordered by a column (default id, newest first) and the total."""
        column = self._order_column("sessions", order_by)
        direction = "ASC" if ascending else "DESC"
        with self._transaction():
            rows = self._db.execute(
                f"SELECT {self._SESSION_COLUMNS} FROM {self._sessions} s "
                f"WHERE s.project_uuid = ? AND s.deleted_at IS NULL ORDER BY s.{column} {direction} "
                "LIMIT ? OFFSET ?",
                [self._project, page_size, (page_number - 1) * page_size],
            ).fetchall()
            total = self._db.execute(
                f"SELECT count(*) FROM {self._sessions} s "
                "WHERE s.project_uuid = ? AND s.deleted_at IS NULL",
                [self._project],
            ).fetchone()
            sessions = [self._session_from_row(r) for r in rows]
            return SessionList(sessions, int(total[0]) if total else 0, len(sessions))

    # Messages -----------------------------------------------------------------------------

    _MESSAGE_COLUMNS = (
        "uuid, created_at, updated_at, role, role_type, content, token_count, metadata, id"
    )

    @staticmethod
    def _message_from_row(row: Sequence[Any]) -> Message:
        """Build a Message from a messages row in column order."""
        return Message(
            uuid=row[0],
            created_at=row[1],
            updated_at=row[2],
            role=row[3],
            role_type=row[4] or "norole",
            content=row[5],
            token_count=row[6],
            metadata=_load(row[7]),
        )

    def put_messages(self, session_id: str, messages: Sequence[Message]) -> list[Message]:
        """Store messages in a session and return them as stored (empty input returns nothing).

        A message without a uuid gets one. A uuid already stored, an unknown role type or a
        session that does not exist is refused with BadRequestError and nothing is stored.
        """
        if not messages:
            return []
        with self._transaction():
            if not self._db.execute(
                f"SELECT 1 FROM {self._sessions} WHERE session_id = ?", [session_id]
            ).fetchone():
                raise BadRequestError("session does not exist: " + session_id)
            ids = [m.uuid or uuid4() for m in messages]
            if (
                len(set(ids)) != len(ids)
                or self._db.execute(
                    f"SELECT 1 FROM {self._messages} WHERE uuid IN (SELECT unnest(?))", [ids]
                ).fetchone()
            ):
                raise BadRequestError("message_uuid already exists")
            for message in messages:
                if message.role_type not in ROLE_TYPES:
                    raise BadRequestError(f"invalid RoleType: {message.role_type}")
            for message_id, message in zip(ids, messages, strict=True):
                self._db.execute(
                    f"INSERT INTO {self._messages} (uuid, session_id, project_uuid, role, "
                    "role_type, content, token_count, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        message_id,
                        session_id,
                        self._project,
                        message.role,
                        message.role_type,
                        message.content,
                        message.token_count,
                        None if message.metadata is None else json.dumps(message.metadata),
                    ],
                )
            rows = self._db.execute(
                f"SELECT {self._MESSAGE_COLUMNS} FROM {self._messages} "
                "WHERE uuid IN (SELECT unnest(?)) ORDER BY id",
                [ids],
            ).fetchall()
            return [self._message_from_row(r) for r in rows]

    def get_message(self, session_id: str, message_uuid: UUID) -> Message:
        """Return one live message of a session as the original does (no timestamps)."""
        with self._transaction():
            row = self._db.execute(
                f"SELECT {self._MESSAGE_COLUMNS} FROM {self._messages} "
                "WHERE session_id = ? AND project_uuid = ? AND uuid = ? AND deleted_at IS NULL",
                [session_id, self._project, message_uuid],
            ).fetchone()
            if row is None:
                raise NotFoundError(f"message {message_uuid} not found")
            found = self._message_from_row(row)
            found.created_at = None
            found.updated_at = None
            found.role_type = "norole"
            return found

    def get_messages_last_n(
        self, session_id: str, last_n: int, before_uuid: Optional[UUID] = None
    ) -> list[Message]:
        """Return the last last_n messages (all if 0), oldest first.

        With before_uuid, the cut starts at that message and includes it; a before_uuid that is
        not stored counts as position 0, so nothing is returned.
        """
        if last_n < 0:
            raise ValueError("cannot specify negative lastNMessages")
        with self._transaction():
            query = (
                f"SELECT {self._MESSAGE_COLUMNS} FROM {self._messages} "
                "WHERE session_id = ? AND project_uuid = ? AND deleted_at IS NULL"
            )
            args: list[Any] = [session_id, self._project]
            if before_uuid is not None:
                found = self._db.execute(
                    f"SELECT id FROM {self._messages} WHERE session_id = ? AND uuid = ? "
                    "AND project_uuid = ? AND deleted_at IS NULL",
                    [session_id, before_uuid, self._project],
                ).fetchone()
                query += " AND id <= ?"
                args.append(found[0] if found else 0)
            query += " ORDER BY id DESC"
            if last_n > 0:
                query += " LIMIT ?"
                args.append(last_n)
            rows = self._db.execute(query, args).fetchall()
            return [self._message_from_row(r) for r in reversed(rows)]

    def get_messages_by_uuid(self, session_id: str, message_uuids: Sequence[UUID]) -> list[Message]:
        """Return the named live messages of a session, in storage order, not in the order asked."""
        if not message_uuids:
            return []
        with self._transaction():
            rows = self._db.execute(
                f"SELECT {self._MESSAGE_COLUMNS} FROM {self._messages} "
                "WHERE session_id = ? AND project_uuid = ? AND deleted_at IS NULL "
                "AND uuid IN (SELECT unnest(?)) ORDER BY id",
                [session_id, self._project, list(message_uuids)],
            ).fetchall()
            return [self._message_from_row(r) for r in rows]

    def get_message_list(self, session_id: str, page_number: int, page_size: int) -> MessageList:
        """Return one page of a session's messages by id; a page past the end is empty with total 0."""
        with self._transaction():
            rows = self._db.execute(
                f"SELECT {self._MESSAGE_COLUMNS} FROM {self._messages} "
                "WHERE session_id = ? AND project_uuid = ? AND deleted_at IS NULL "
                "ORDER BY id ASC LIMIT ? OFFSET ?",
                [session_id, self._project, page_size, (page_number - 1) * page_size],
            ).fetchall()
            if not rows:
                return MessageList([], 0, 0)
            total = self._db.execute(
                f"SELECT count(*) FROM {self._messages} "
                "WHERE session_id = ? AND project_uuid = ? AND deleted_at IS NULL",
                [session_id, self._project],
            ).fetchone()
            messages = [self._message_from_row(r) for r in rows]
            return MessageList(messages, int(total[0]) if total else 0, len(messages))

    def update_messages(
        self,
        session_id: str,
        messages: Sequence[Message],
        *,
        include_content: bool,
        is_privileged: bool = False,
    ) -> None:
        """Change token counts (and, if asked, role and content) and merge metadata by uuid.

        Each message needs a uuid (ValueError otherwise). Messages not found are left alone, as
        in the original's batch update. Metadata is merged, never replaced.
        """
        with self._transaction():
            for message in messages:
                if message.uuid is None:
                    raise ValueError("message UUID cannot be nil")
            for message in messages:
                sets, values = ["token_count = ?", "updated_at = ?"], [message.token_count, _now()]
                if include_content:
                    sets += ["role = ?", "content = ?"]
                    values += [message.role, message.content]
                self._db.execute(
                    f"UPDATE {self._messages} SET {', '.join(sets)} "
                    "WHERE uuid = ? AND session_id = ? AND project_uuid = ? AND deleted_at IS NULL",
                    [*values, message.uuid, session_id, self._project],
                )
            for message in messages:
                if message.metadata:
                    self._update_message_metadata(
                        session_id, message.uuid, message.metadata, is_privileged
                    )

    def update_message(
        self,
        session_id: str,
        message: Message,
        *,
        include_content: bool,
        is_privileged: bool = False,
    ) -> None:
        """Change one message like update_messages, but raise NotFoundError when it is not found."""
        if message.uuid is None:
            raise ValueError("message UUID cannot be nil")
        with self._transaction():
            sets, values = ["token_count = ?", "updated_at = ?"], [message.token_count, _now()]
            if include_content:
                sets += ["role = ?", "content = ?"]
                values += [message.role, message.content]
            changed = self._db.execute(
                f"UPDATE {self._messages} SET {', '.join(sets)} "
                "WHERE uuid = ? AND session_id = ? AND project_uuid = ? AND deleted_at IS NULL "
                "RETURNING uuid",
                [*values, message.uuid, session_id, self._project],
            ).fetchall()
            if not changed:
                raise NotFoundError(f"message {message.uuid} not found")
            if message.metadata:
                self._update_message_metadata(
                    session_id, message.uuid, message.metadata, is_privileged
                )

    def _update_message_metadata(
        self,
        session_id: str,
        message_uuid: UUID,
        metadata: dict[str, Any],
        is_privileged: bool,
    ) -> None:
        """Merge metadata into one message's stored metadata, serialised by the store's lock."""
        row = self._db.execute(
            f"SELECT metadata FROM {self._messages} WHERE uuid = ?", [message_uuid]
        ).fetchone()
        if row is None:
            raise NotFoundError(f"uuid {message_uuid} not found")
        merged = merge_metadata(_load(row[0]) or {}, metadata, is_privileged)
        self._db.execute(
            f"UPDATE {self._messages} SET metadata = ? "
            "WHERE session_id = ? AND uuid = ? AND project_uuid = ?",
            [json.dumps(merged), session_id, message_uuid, self._project],
        )

    def delete_message(self, session_id: str, message_uuid: UUID) -> None:
        """Soft-delete one message; it stays stored, hidden, until purge_deleted runs."""
        with self._transaction():
            hit = self._db.execute(
                f"UPDATE {self._messages} SET deleted_at = ? "
                "WHERE session_id = ? AND project_uuid = ? AND uuid = ? AND deleted_at IS NULL "
                "RETURNING uuid",
                [_now(), session_id, self._project, message_uuid],
            ).fetchall()
            if not hit:
                raise NotFoundError(f"message {message_uuid} not found")

    # Memory -------------------------------------------------------------------------------

    def put_memory(
        self, session_id: str, memory: Memory, skip_processing: bool = False
    ) -> list[Message]:
        """Store a conversation's messages, creating the session if needed, as PutMemory does.

        The session is first updated (which undeletes it) and created when it does not exist.
        A session that has ended refuses the messages. Unless skip_processing, the messages are
        handed to the graph for the session and for its user; with no graph attached that
        hand-off is recorded as owed work. Returns the messages as stored.
        """
        with self._transaction():
            try:
                session = self.update_session(session_id)
            except NotFoundError:
                session = self.create_session(session_id)
            if session.ended_at is not None:
                raise SessionEndedError("session has ended")
            stored = self.put_messages(session_id, memory.messages)
            if skip_processing:
                return stored
            groups = [session_id] + ([session.user_id] if session.user_id is not None else [])
            for group in groups:
                if self._graph is None:
                    self._graph_step(
                        "put_memory",
                        group,
                        {"messages": [str(m.uuid) for m in stored], "skip_notify": True},
                    )
                else:
                    self._graph.put_memory(group, stored, True)
            return stored

    def get_memory(self, session_id: str, last_n_messages: int) -> Memory:
        """Return the last messages of a session and the graph's relevant facts, as GetMemory does.

        At least four messages are read (to choose facts from the last four); only last_n_messages
        are returned as chat history. A session with no messages returns an empty memory. With no
        graph attached, relevant_facts is empty and facts_unavailable says why.
        """
        if last_n_messages < 0:
            raise ValueError("lastNMessages cannot be negative")
        with self._transaction():
            read = max(last_n_messages, DEFAULT_LAST_N_MESSAGES)
            messages = self.get_messages_last_n(session_id, read)
            if not messages:
                return Memory(messages=[])
            session = self.get_session(session_id)
            history = messages[-last_n_messages:] if len(messages) > last_n_messages else messages
            if last_n_messages == 0:
                history = []
            group = session.user_id if session.user_id is not None else session.session_id
            result = Memory(messages=history)
            if self._graph is None:
                result.facts_unavailable = NO_GRAPH_REASON
                return result
            retrieval = messages[-MAX_MESSAGES_FOR_FACT_RETRIEVAL:]
            for fact in self._graph.get_memory(group, MEMORY_FACTS_LIMIT, retrieval):
                result.relevant_facts.append(
                    Fact(fact.fact, fact.uuid, fact.valid_at or fact.created_at)
                )
            return result

    def search_sessions(
        self,
        text: str,
        limit: int,
        *,
        user_id: str = "",
        session_ids: Sequence[str] = (),
    ) -> tuple[list[Fact], Optional[str]]:
        """Search the graph for facts across a user and sessions, as SearchSessions does.

        Returns the facts and, when no graph is attached, an empty list with the reason.
        """
        groups = ([user_id] if user_id else []) + list(session_ids)
        if self._graph is None:
            return [], NO_GRAPH_REASON
        facts = self._graph.search(groups, text, limit)
        return [Fact(f.fact, f.uuid, f.valid_at or f.created_at) for f in facts], None

    # Purge ---------------------------------------------------------------------------------

    def _purge_soft_deleted(self) -> None:
        """Hard-delete every soft-deleted message and session, as the original's purge does."""
        for table in (self._messages, self._sessions):
            self._db.execute(f"DELETE FROM {table} WHERE deleted_at IS NOT NULL")

    def purge_deleted(self) -> None:
        """Hard-delete all soft-deleted messages, sessions and users in the whole store."""
        with self._transaction():
            self._purge_soft_deleted()
            self._db.execute(f"DELETE FROM {self._users} WHERE deleted_at IS NOT NULL")
