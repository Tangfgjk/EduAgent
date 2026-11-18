"""Run existing storage behavioral contracts against real PostgreSQL."""

import asyncio, inspect, runpy
from pathlib import Path
import pytest
from psycopg import sql
from deeptutor.services.session.sqlite_store import SQLiteSessionStore
from deeptutor.services.storage import postgres_adapter as pg
from deeptutor.services.storage.postgres_migration import migrate

source = Path(__file__).resolve().parents[1] / "services/session/test_sqlite_store.py"
contracts = runpy.run_path(str(source))
cases = [
    (name, value)
    for name, value in contracts.items()
    if name.startswith("test_")
    and callable(value)
    and set(inspect.signature(value).parameters) == {"store"}
]


@pytest.fixture
def native_store(tmp_path, monkeypatch):
    store = SQLiteSessionStore(tmp_path / "contract.db")
    migrate(store.db_path)
    original = pg.enabled
    monkeypatch.setattr(
        pg,
        "enabled",
        lambda path: Path(path).resolve() == store.db_path.resolve() or original(path),
    )
    try:
        yield SQLiteSessionStore(store.db_path)
    finally:
        with pg.connect_raw() as c:
            c.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(
                    sql.Identifier(pg.schema_name(store.db_path))
                )
            )


@pytest.mark.parametrize("name,contract", cases, ids=[name for name, _ in cases])
def test_postgres_session_behavior(name, contract, native_store):
    if inspect.iscoroutinefunction(contract):
        asyncio.run(contract(native_store))
    else:
        contract(native_store)


@pytest.mark.parametrize(
    "contract_name",
    [
        "test_submission_reservation_and_admission_are_atomic_across_workers",
        "test_resend_does_not_recreate_a_recycled_conversation",
    ],
)
def test_postgres_submission_concurrency(contract_name, tmp_path, monkeypatch):
    original_init = SQLiteSessionStore.__init__
    previous_enabled = pg.enabled
    migrated = set()

    def initialize(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        key = str(self.db_path.resolve())
        if key not in migrated:
            migrate(self.db_path)
            migrated.add(key)

    monkeypatch.setattr(
        pg, "enabled", lambda path: str(Path(path).resolve()) in migrated or previous_enabled(path)
    )
    monkeypatch.setattr(SQLiteSessionStore, "__init__", initialize)
    try:
        asyncio.run(contracts[contract_name](tmp_path))
    finally:
        with pg.connect_raw() as c:
            for path in migrated:
                c.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(
                        sql.Identifier(pg.schema_name(Path(path)))
                    )
                )
