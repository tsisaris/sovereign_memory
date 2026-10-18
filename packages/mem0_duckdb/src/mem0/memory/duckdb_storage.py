"""Persist Mem0 history and messages in DuckDB with explicit connection ownership."""

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import duckdb
from duckdb_sqlalchemy_async.native import connect_native, require_native_connection

logger = logging.getLogger(__name__)


class DuckDBManager:
    """DuckDB implementation of the Mem0 history and message storage."""

    def __init__(
        self, db_path: str = ":memory:", connection: Optional[duckdb.DuckDBPyConnection] = None
    ):
        """Initialize the DuckDBManager.

        Args:
            db_path: The database path or ':memory:'.
            connection: An optional caller-owned DuckDB connection. Its caller must
                begin and finish shared transactions; this manager never commits them.
        """
        self.db_path = db_path
        self._lock = threading.RLock()
        self._owns_connection = False
        self.connection = None

        if connection is not None:
            require_native_connection(connection)
            self.connection = connection
        else:
            self.connection = connect_native(db_path)
            self._owns_connection = True

        self._create_history_table()
        self._create_messages_table()
        self._run_migrations()

    def _run_migrations(self) -> None:
        """Migrate legacy schema to current schema if needed."""
        with self._lock:
            try:
                tables_df = self.connection.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_name = 'history'"
                ).fetchall()
                if not tables_df:
                    return

                cols_df = self.connection.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'history'"
                ).fetchall()
                old_cols = {row[0] for row in cols_df}

                expected_cols = {
                    "id",
                    "memory_id",
                    "old_memory",
                    "new_memory",
                    "event",
                    "created_at",
                    "updated_at",
                    "is_deleted",
                    "actor_id",
                    "role",
                }

                if old_cols == expected_cols:
                    return

                owns_transaction = self._owns_connection
                if owns_transaction:
                    self.connection.execute("BEGIN TRANSACTION")

                try:
                    self.connection.execute("ALTER TABLE history RENAME TO history_old")

                    self.connection.execute(
                        """
                        CREATE TABLE history (
                            id           VARCHAR PRIMARY KEY,
                            memory_id    VARCHAR,
                            old_memory   VARCHAR,
                            new_memory   VARCHAR,
                            event        VARCHAR,
                            created_at   TIMESTAMP WITH TIME ZONE,
                            updated_at   TIMESTAMP WITH TIME ZONE,
                            is_deleted   BOOLEAN DEFAULT FALSE,
                            actor_id     VARCHAR,
                            role         VARCHAR
                        )
                        """
                    )

                    intersecting = list(expected_cols & old_cols)
                    if intersecting:
                        cols_csv = ", ".join(intersecting)
                        self.connection.execute(
                            f"INSERT INTO history ({cols_csv}) SELECT {cols_csv} FROM history_old"
                        )

                    self.connection.execute("DROP TABLE history_old")
                    if owns_transaction:
                        self.connection.execute("COMMIT")
                    logger.info("History table migration completed successfully.")
                except Exception as e:
                    if owns_transaction:
                        self.connection.execute("ROLLBACK")
                    raise e
            except Exception as e:
                logger.error(f"History table migration failed: {e}")
                raise

    def _create_history_table(self) -> None:
        with self._lock:
            try:
                self.connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS history (
                        id           VARCHAR PRIMARY KEY,
                        memory_id    VARCHAR,
                        old_memory   VARCHAR,
                        new_memory   VARCHAR,
                        event        VARCHAR,
                        created_at   TIMESTAMP WITH TIME ZONE,
                        updated_at   TIMESTAMP WITH TIME ZONE,
                        is_deleted   BOOLEAN DEFAULT FALSE,
                        actor_id     VARCHAR,
                        role         VARCHAR
                    )
                    """
                )
            except Exception as e:
                logger.error(f"Failed to create history table: {e}")
                raise

    def _create_messages_table(self) -> None:
        with self._lock:
            try:
                self.connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS messages (
                        id VARCHAR PRIMARY KEY,
                        session_scope VARCHAR,
                        role VARCHAR,
                        content VARCHAR,
                        name VARCHAR,
                        created_at TIMESTAMP WITH TIME ZONE
                    )
                    """
                )
            except Exception as e:
                logger.error(f"Failed to create messages table: {e}")
                raise

    def add_history(
        self,
        memory_id: str,
        old_memory: Optional[str],
        new_memory: Optional[str],
        event: str,
        *,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        is_deleted: int = 0,
        actor_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> None:
        """Store one event without committing a caller-owned transaction."""
        with self._lock:
            try:
                self.connection.execute(
                    """
                    INSERT INTO history (
                        id, memory_id, old_memory, new_memory, event,
                        created_at, updated_at, is_deleted, actor_id, role
                    )
                    VALUES (?, ?, ?, ?, ?, CAST(? AS TIMESTAMP WITH TIME ZONE), CAST(? AS TIMESTAMP WITH TIME ZONE), ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        memory_id,
                        old_memory,
                        new_memory,
                        event,
                        created_at,
                        updated_at,
                        bool(is_deleted),
                        actor_id,
                        role,
                    ),
                )
            except Exception as e:
                logger.error(f"Failed to add history record: {e}")
                raise

    def batch_add_history(self, records: List[Dict[str, Any]]) -> None:
        """Store an atomic owned batch or join the supplied caller's transaction."""
        if not records:
            return
        with self._lock:
            try:
                owns_transaction = self._owns_connection
                if owns_transaction:
                    self.connection.execute("BEGIN TRANSACTION")

                try:
                    rows = []
                    for record in records:
                        rows.append(
                            (
                                str(uuid.uuid4()),
                                record.get("memory_id"),
                                record.get("old_memory"),
                                record.get("new_memory"),
                                record.get("event"),
                                record.get("created_at"),
                                record.get("updated_at"),
                                bool(record.get("is_deleted", 0)),
                                record.get("actor_id"),
                                record.get("role"),
                            )
                        )
                    self.connection.executemany(
                        """
                        INSERT INTO history (
                            id, memory_id, old_memory, new_memory, event,
                            created_at, updated_at, is_deleted, actor_id, role
                        )
                        VALUES (?, ?, ?, ?, ?, CAST(? AS TIMESTAMP WITH TIME ZONE), CAST(? AS TIMESTAMP WITH TIME ZONE), ?, ?, ?)
                        """,
                        rows,
                    )
                    if owns_transaction:
                        self.connection.execute("COMMIT")
                except Exception as e:
                    if owns_transaction:
                        self.connection.execute("ROLLBACK")
                    raise e
            except Exception as e:
                logger.error(f"Failed to batch add history records: {e}")
                raise

    def get_history(self, memory_id: str) -> List[Dict[str, Any]]:
        """Return the stored events for one memory in timestamp order."""
        with self._lock:
            cur = self.connection.execute(
                """
                SELECT id, memory_id, old_memory, new_memory, event,
                       created_at, updated_at, is_deleted, actor_id, role
                FROM history
                WHERE memory_id = ?
                ORDER BY created_at ASC NULLS LAST, updated_at ASC NULLS LAST
                """,
                (memory_id,),
            )
            rows = cur.fetchall()

        result = []
        for r in rows:
            created_at_str = (
                r[5].astimezone(timezone.utc).isoformat().replace("+00:00", "") + "+00:00"
                if r[5]
                else None
            )
            updated_at_str = (
                r[6].astimezone(timezone.utc).isoformat().replace("+00:00", "") + "+00:00"
                if r[6]
                else None
            )

            result.append(
                {
                    "id": r[0],
                    "memory_id": r[1],
                    "old_memory": r[2],
                    "new_memory": r[3],
                    "event": r[4],
                    "created_at": created_at_str,
                    "updated_at": updated_at_str,
                    "is_deleted": bool(r[7]),
                    "actor_id": r[8],
                    "role": r[9],
                }
            )
        return result

    def save_messages(self, messages: List[Dict[str, Any]], session_scope: str) -> None:
        """Store messages and retain the latest ten within the supplied session."""
        if not messages:
            return
        with self._lock:
            try:
                now_str = datetime.now(timezone.utc).isoformat()
                for message in messages:
                    self.connection.execute(
                        """
                        INSERT INTO messages (id, session_scope, role, content, name, created_at)
                        VALUES (?, ?, ?, ?, ?, CAST(? AS TIMESTAMP WITH TIME ZONE))
                        """,
                        (
                            str(uuid.uuid4()),
                            session_scope,
                            message.get("role"),
                            message.get("content"),
                            message.get("name"),
                            now_str,
                        ),
                    )
                self.connection.execute(
                    """
                    DELETE FROM messages WHERE session_scope = ? AND id NOT IN (
                        SELECT id FROM (
                            SELECT id FROM messages WHERE session_scope = ? ORDER BY created_at DESC LIMIT 10
                        )
                    )
                    """,
                    (session_scope, session_scope),
                )
            except Exception as e:
                logger.error(f"Failed to save messages: {e}")
                raise

    def get_last_messages(self, session_scope: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Return the requested session's latest stored messages in chronological order."""
        with self._lock:
            cur = self.connection.execute(
                """
                SELECT role, content, name, created_at FROM (
                    SELECT role, content, name, created_at
                    FROM messages
                    WHERE session_scope = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                ) ORDER BY created_at ASC
                """,
                (session_scope, limit),
            )
            rows = cur.fetchall()

        return [
            {
                "role": r[0],
                "content": r[1],
                "name": r[2],
                "created_at": r[3].astimezone(timezone.utc).isoformat().replace("+00:00", "")
                + "+00:00"
                if r[3]
                else None,
            }
            for r in rows
        ]

    def reset(self) -> None:
        """Drop history and messages in this connection's current namespace."""
        if not self.connection:
            raise RuntimeError("Cannot reset a closed DuckDBManager")
        with self._lock:
            try:
                self.connection.execute("DROP TABLE IF EXISTS history")
                self.connection.execute("DROP TABLE IF EXISTS messages")
            except Exception as e:
                logger.error(f"Failed to reset tables: {e}")
                raise

    def close(self) -> None:
        """Close only a connection created by this manager, preserving caller ownership."""
        if self.connection and self._owns_connection:
            self.connection.close()
            self.connection = None

    def __del__(self):
        """Release an owned connection when this manager is collected."""
        self.close()
