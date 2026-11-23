"""Repository-level PostgreSQL acceptance with disposable source databases."""

import asyncio
from pathlib import Path
import sqlite3
from unittest.mock import patch

from psycopg import sql
import pytest

from deeptutor.learning.models import LearningModule, LearningProgress, TopicMetadata
from deeptutor.learning.storage import LearningStore
from deeptutor.services.session.sqlite_store import SQLiteSessionStore
from deeptutor.services.storage.postgres_adapter import Connection, connect_raw, schema_name
from deeptutor.services.storage.postgres_migration import migrate


def test_native_learning_topic_upsert_cas_and_no_sqlite_writes(tmp_path):
    store = LearningStore(tmp_path / "learning")
    store.save(LearningProgress(book_id="p"))
    try:
        migrate(store.db_path)
    except OSError:
        pytest.skip("PostgreSQL unavailable")
    try:
        with patch(
            "deeptutor.services.storage.postgres_adapter.enabled",
            side_effect=lambda p: Path(p).resolve() == store.db_path.resolve(),
        ):
            store = LearningStore(tmp_path / "learning")

            def change(tx):
                tx.progress.name = "PG Only"
                tx.put_topic(TopicMetadata(path_id="p", goal="Learn independently"), [])
                tx.emit("test.changed", {})

            store.mutate("p", change)
            assert store.get_topic("p").metadata.goal == "Learn independently"
            assert store.load("p").name == "PG Only"
        with sqlite3.connect(store.db_path) as source:
            assert (
                "PG Only"
                not in source.execute("SELECT state_json FROM mastery_paths").fetchone()[0]
            )
    finally:
        with connect_raw() as c:
            c.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name(store.db_path)))
            )


def test_native_chat_roundtrip_and_json_filters(tmp_path):
    store = SQLiteSessionStore(tmp_path / "chat.db")
    migrate(store.db_path)
    try:
        with patch(
            "deeptutor.services.storage.postgres_adapter.enabled",
            side_effect=lambda p: Path(p).resolve() == store.db_path.resolve(),
        ):
            store = SQLiteSessionStore(store.db_path)

            async def check():
                created = await store.create_session("Postgres")
                message = await store.add_message(created["id"], "user", "Hello PostgreSQL")
                assert message > 0
                assert (await store.get_session_with_messages(created["id"]))["messages"][0][
                    "content"
                ] == "Hello PostgreSQL"
                assert any(r["session_id"] == created["id"] for r in await store.list_sessions()), (
                    repr((await store.get_session(created["id"])).get("preferences"))
                )

            asyncio.run(check())
            with Connection(store.db_path) as c:
                assert (
                    c.execute(
                        "SELECT content FROM messages WHERE content LIKE ?", ("%postgresql",)
                    ).fetchone()[0]
                    == "Hello PostgreSQL"
                )
    finally:
        with connect_raw() as c:
            c.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name(store.db_path)))
            )
