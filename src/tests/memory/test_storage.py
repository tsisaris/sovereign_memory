import sqlite3
import uuid
from datetime import datetime, timezone

import pytest

from mem0_duckdb_history import DuckDBManager as SQLiteManager


@pytest.fixture
def temp_db_path(tmp_path):
    # Just return a path without creating a file. If test needs to pre-create it using sqlite3, it must be handled differently,
    # but the tests just check duckdb creation and usage
    return str(tmp_path / "test_history.duckdb")


@pytest.fixture
def sqlite_manager(temp_db_path):
    manager = SQLiteManager(temp_db_path)
    yield manager
    manager.close()


@pytest.fixture
def sample_data():
    return {
        "memory_id": str(uuid.uuid4()),
        "old_memory": "I like apples",
        "new_memory": "I like apples and bananas",
        "event": "UPDATE",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "actor_id": "user123",
        "role": "user",
    }


class TestSQLiteManager:
    @pytest.mark.parametrize("db_type,path", [("file", "temp_db_path"), ("memory", ":memory:")])
    def test_initialization(self, db_type, path, request):
        if db_type == "file":
            db_path = request.getfixturevalue(path)
        else:
            db_path = path

        manager = SQLiteManager(db_path)
        assert manager.connection is not None
        assert manager.db_path == db_path
        manager.close()

    def test_table_schema_creation(self, sqlite_manager):
        cursor = sqlite_manager.connection.cursor()
        cursor.execute("PRAGMA table_info(history)")
        columns = {row[1] for row in cursor.fetchall()}

        expected_columns = {
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
        assert columns == expected_columns

    def test_add_history_basic(self, sqlite_manager, sample_data):
        sqlite_manager.add_history(
            memory_id=sample_data["memory_id"],
            old_memory=sample_data["old_memory"],
            new_memory=sample_data["new_memory"],
            event=sample_data["event"],
            created_at=sample_data["created_at"],
            actor_id=sample_data["actor_id"],
            role=sample_data["role"],
        )

        cursor = sqlite_manager.connection.cursor()
        cursor.execute("SELECT * FROM history WHERE memory_id = ?", (sample_data["memory_id"],))
        result = cursor.fetchone()

        assert result is not None
        assert result[1] == sample_data["memory_id"]
        assert result[2] == sample_data["old_memory"]
        assert result[3] == sample_data["new_memory"]
        assert result[4] == sample_data["event"]
        assert result[8] == sample_data["actor_id"]
        assert result[9] == sample_data["role"]

    @pytest.mark.parametrize(
        "old_memory,new_memory,is_deleted",
        [(None, "New memory", 0), ("Old memory", None, 1), (None, None, 1)],
    )
    def test_add_history_optional_params(
        self, sqlite_manager, sample_data, old_memory, new_memory, is_deleted
    ):
        sqlite_manager.add_history(
            memory_id=sample_data["memory_id"],
            old_memory=old_memory,
            new_memory=new_memory,
            event="UPDATE",
            updated_at=sample_data["updated_at"],
            is_deleted=is_deleted,
            actor_id=sample_data["actor_id"],
            role=sample_data["role"],
        )

        cursor = sqlite_manager.connection.cursor()
        cursor.execute("SELECT * FROM history WHERE memory_id = ?", (sample_data["memory_id"],))
        result = cursor.fetchone()

        assert result[2] == old_memory
        assert result[3] == new_memory
        assert result[6].isoformat() == sample_data["updated_at"]
        assert result[7] == bool(is_deleted)

    def test_add_history_generates_unique_ids(self, sqlite_manager, sample_data):
        for i in range(3):
            sqlite_manager.add_history(
                memory_id=sample_data["memory_id"],
                old_memory=f"Memory {i}",
                new_memory=f"Updated Memory {i}",
                event="ADD" if i == 0 else "UPDATE",
            )

        cursor = sqlite_manager.connection.cursor()
        cursor.execute("SELECT id FROM history WHERE memory_id = ?", (sample_data["memory_id"],))
        ids = [row[0] for row in cursor.fetchall()]

        assert len(ids) == 3
        assert len(set(ids)) == 3

    def test_get_history_empty(self, sqlite_manager):
        result = sqlite_manager.get_history("non-existent-id")
        assert result == []

    def test_get_history_single_record(self, sqlite_manager, sample_data):
        sqlite_manager.add_history(
            memory_id=sample_data["memory_id"],
            old_memory=sample_data["old_memory"],
            new_memory=sample_data["new_memory"],
            event=sample_data["event"],
            created_at=sample_data["created_at"],
            actor_id=sample_data["actor_id"],
            role=sample_data["role"],
        )

        result = sqlite_manager.get_history(sample_data["memory_id"])

        assert len(result) == 1
        record = result[0]
        assert record["memory_id"] == sample_data["memory_id"]
        assert record["old_memory"] == sample_data["old_memory"]
        assert record["new_memory"] == sample_data["new_memory"]
        assert record["event"] == sample_data["event"]
        assert record["created_at"] == sample_data["created_at"]
        assert record["actor_id"] == sample_data["actor_id"]
        assert record["role"] == sample_data["role"]
        assert record["is_deleted"] is False

    def test_get_history_chronological_ordering(self, sqlite_manager, sample_data):
        import time

        timestamps = []
        for i in range(3):
            ts = datetime.now(timezone.utc).isoformat()
            timestamps.append(ts)
            sqlite_manager.add_history(
                memory_id=sample_data["memory_id"],
                old_memory=f"Memory {i}",
                new_memory=f"Memory {i + 1}",
                event="ADD" if i == 0 else "UPDATE",
                created_at=ts,
                updated_at=ts if i > 0 else None,
            )
            time.sleep(0.01)

        result = sqlite_manager.get_history(sample_data["memory_id"])
        result_timestamps = [r["created_at"] for r in result]
        assert result_timestamps == sorted(timestamps)

    def test_migration_preserves_data(self, temp_db_path, sample_data):
        manager1 = SQLiteManager(temp_db_path)
        manager1.add_history(
            memory_id=sample_data["memory_id"],
            old_memory=sample_data["old_memory"],
            new_memory=sample_data["new_memory"],
            event=sample_data["event"],
            created_at=sample_data["created_at"],
        )
        original_data = manager1.get_history(sample_data["memory_id"])
        manager1.close()

        manager2 = SQLiteManager(temp_db_path)
        migrated_data = manager2.get_history(sample_data["memory_id"])
        manager2.close()

        assert len(migrated_data) == len(original_data)
        assert migrated_data[0]["memory_id"] == original_data[0]["memory_id"]
        assert migrated_data[0]["new_memory"] == original_data[0]["new_memory"]

    def test_large_batch_operations(self, sqlite_manager):
        batch_size = 1000
        memory_ids = [str(uuid.uuid4()) for _ in range(batch_size)]

        records = []
        for i, memory_id in enumerate(memory_ids):
            records.append(
                {
                    "memory_id": memory_id,
                    "old_memory": None,
                    "new_memory": f"Batch memory {i}",
                    "event": "ADD",
                }
            )

        sqlite_manager.batch_add_history(records)

        cursor = sqlite_manager.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM history")
        count = cursor.fetchone()[0]
        assert count == batch_size

        for memory_id in memory_ids[:10]:
            result = sqlite_manager.get_history(memory_id)
            assert len(result) == 1

    def test_explicit_old_schema_migration(self, temp_db_path):
        import duckdb

        legacy_conn = duckdb.connect(temp_db_path)
        legacy_conn.execute("""
            CREATE TABLE history (
                id VARCHAR PRIMARY KEY,
                memory_id VARCHAR,
                old_memory VARCHAR,
                new_memory VARCHAR,
                event VARCHAR,
                created_at TIMESTAMP
            )
        """)
        legacy_id = str(uuid.uuid4())
        legacy_conn.execute(
            "INSERT INTO history (id, memory_id, old_memory, new_memory, event, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (legacy_id, "m1", "o", "n", "ADD", datetime.now().isoformat()),
        )
        legacy_conn.commit()
        legacy_conn.close()

        mgr = SQLiteManager(temp_db_path)
        history = mgr.get_history("m1")
        assert len(history) == 1
        assert history[0]["id"] == legacy_id
        assert history[0]["actor_id"] is None
        assert history[0]["is_deleted"] is False
        mgr.close()

    def test_reset_drops_tables(self, temp_db_path):
        mgr = SQLiteManager(temp_db_path)
        mgr.add_history(memory_id="m1", old_memory=None, new_memory="new", event="ADD")
        mgr.save_messages([{"role": "user", "content": "hello", "name": None}], "sess1")
        mgr.reset()
        tables = mgr.connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name IN ('history','messages')"
        ).fetchall()
        assert tables == [], "both tables should be dropped after reset"
        mgr.close()

        mgr2 = SQLiteManager(temp_db_path)
        msg_count = mgr2.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        hist_count = mgr2.connection.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        assert msg_count == 0
        assert hist_count == 0
        mgr2.close()
