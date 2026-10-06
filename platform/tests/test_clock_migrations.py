from datetime import datetime, timezone
import sqlite3
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.clock import CallableClock, aware_utc
from app.storage.db import Store
from app.storage.db import _SCHEMA
from app.storage.migrations import migrate
from app.storage.trigger_state import PersistentTriggerState


def test_clock_rejects_naive_and_normalizes():
    with pytest.raises(ValueError):
        aware_utc(datetime(2026, 1, 1))
    assert aware_utc(datetime(2026, 1, 1, tzinfo=timezone.utc)).tzinfo == timezone.utc
    assert CallableClock(lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)).now().year == 2026


def test_foundation_migration_is_idempotent_and_versioned():
    store = Store(":memory:")
    assert store.schema_version == 2
    assert store.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2
    assert migrate(store.conn, _SCHEMA) == 2
    store.conn.close()


def test_trigger_claim_isolated_and_atomic():
    store = Store(":memory:")
    first = PersistentTriggerState(store, "learner", "session-a")
    second = PersistentTriggerState(store, "learner", "session-b")
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert first.claim("TR_STUCK", stamp, 60)
    assert not first.claim("TR_STUCK", stamp, 60)
    assert second.claim("TR_STUCK", stamp, 60)


def test_migration_adopts_legacy_without_changing_fact_payload(tmp_path):
    path = str(tmp_path / "old.sqlite3")
    legacy = sqlite3.connect(path)
    legacy.execute("CREATE TABLE events(event_id TEXT PRIMARY KEY,learner_pseudo_id TEXT,session_id TEXT,ts TEXT,payload TEXT)")
    legacy.execute("INSERT INTO events VALUES ('e1','s','session','2026-01-01','original')")
    legacy.commit()
    legacy.close()
    store = Store(path)
    row = store.conn.execute("SELECT payload,event_seq FROM events WHERE event_id='e1'").fetchone()
    assert row[0] == "original" and row[1] == 1
    store.close()
    reopened = Store(path)
    assert reopened.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2
    assert reopened.conn.execute("SELECT COUNT(*) FROM event_clock").fetchone()[0] == 1
    reopened.close()


def test_migration_refuses_downgrade_and_rolls_back_failure(tmp_path):
    conn = sqlite3.connect(tmp_path / "future.sqlite")
    conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT)")
    conn.execute("INSERT INTO schema_migrations VALUES (999,'future')")
    conn.commit()
    with pytest.raises(ValueError, match="newer"):
        migrate(conn, _SCHEMA)
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 999
    conn.close()
    invalid = sqlite3.connect(":memory:")
    with pytest.raises(sqlite3.OperationalError):
        migrate(invalid, "CREATE TABLE first_table(a TEXT); invalid statement;")
    assert invalid.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='first_table'").fetchone()[0] == 0
    invalid.close()


def test_persistent_trigger_survives_restart_concurrent_claim_and_legacy_time(tmp_path):
    path = str(tmp_path / "claims.sqlite")
    first, second = Store(path), Store(path)
    states = [PersistentTriggerState(store, "s", "one") for store in (first, second)]
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda state: state.claim("TR_STUCK", stamp, 60), states))
    assert sorted(claims) == [False, True]
    first.close()
    restarted = Store(path)
    state = PersistentTriggerState(restarted, "s", "one")
    assert not state.claim("TR_STUCK", stamp, 60)
    assert not state.claim("TR_OLD", stamp, 60, datetime(2026, 1, 1))
    assert state.claim("TR_STUCK", datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc), 60)
    assert PersistentTriggerState(restarted, "other", "one").claim("TR_STUCK", stamp, 60)
    with pytest.raises(ValueError):
        state.claim("TR_STUCK", datetime(2026, 1, 1), 60)
    second.close()
    restarted.close()


def test_runtime_claim_rolls_back_with_turn_and_recovers(tmp_path):
    from app.core.schema import utcnow
    from app.learning.service import LearningService
    from app.llm.client import FakeLLM
    from app.orchestration.runtime import SessionRuntime

    path = str(tmp_path / "turn.sqlite")
    store = Store(path)
    LearningService(store).set_consent("s", ["teaching"], "c", "learner", utcnow())
    runtime = SessionRuntime(store, FakeLLM())
    sid = runtime.create("s")["session_id"]
    row = store.conn.execute("SELECT payload FROM runtime_sessions WHERE session_id=?", (sid,)).fetchone()
    state = json.loads(row[0])
    state["snapshot"]["affect_motivation"]["frustration"] = 0.9
    store.conn.execute("UPDATE runtime_sessions SET payload=? WHERE session_id=?", (json.dumps(state), sid))
    store.conn.commit()

    def fail(stage):
        if stage == "before_commit":
            raise RuntimeError("turn-failure")

    with pytest.raises(RuntimeError, match="turn-failure"):
        runtime.message(sid, "s", text="我尝试想一想", attempt_id="one", fault=fail)
    assert store.conn.execute("SELECT COUNT(*) FROM trigger_state").fetchone()[0] == 0
    runtime.message(sid, "s", text="我尝试想一想", attempt_id="one")
    assert store.conn.execute("SELECT COUNT(*) FROM trigger_state").fetchone()[0] > 0
    store.close()
    restarted = Store(path)
    assert SessionRuntime(restarted, FakeLLM()).load(sid, "s").trigger.state is not None
    restarted.close()
