"""Crash boundaries and session/receipt restoration across app processes."""
import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from fastapi.testclient import TestClient

from app.config import Settings
from app.gateway.routes import create_app
from app.learning.service import LearningService, ConsentDenied
from app.core.schema import utcnow
from app.llm.client import FakeLLM
from app.storage.db import Store
from app.orchestration.runtime import SessionRuntime, RuntimeConflict


def prepared(tmp_path):
    path = str(tmp_path / "runtime.sqlite")
    store = Store(path)
    LearningService(store).set_consent("student", ["teaching"], "c1", "learner", utcnow())
    runtime = SessionRuntime(store, FakeLLM())
    initial = runtime.create("student", None, "explore")
    return path, store, runtime, initial["session_id"]


def test_app_restart_restores_item_ladder_and_stable_receipt(tmp_path):
    path, store, runtime, sid = prepared(tmp_path)
    client = TestClient(create_app(Settings(learner_id="student"), llm=FakeLLM(), store=store))
    first = client.post(f"/api/sessions/{sid}/messages", json={"answer": "8", "attempt_id": "a1"})
    assert first.status_code == 200
    client.post(f"/api/sessions/{sid}/messages", json={"text": "提示一下", "attempt_id": "help1"})
    state = runtime.load(sid, "student").export_state()
    store.close()
    second_store = Store(path)
    restarted = TestClient(create_app(Settings(learner_id="student"), llm=FakeLLM(), store=second_store))
    replayed = restarted.post(f"/api/sessions/{sid}/messages", json={"answer": "8", "attempt_id": "a1"})
    assert replayed.json() == first.json()
    assert SessionRuntime(second_store, FakeLLM()).load(sid, "student").export_state() == state
    assert len(LearningService(second_store).evidences("student")) == 1
    assert restarted.get(f"/api/sessions/{sid}").status_code == 200
    public = restarted.get(f"/api/sessions/{sid}").json()
    assert "bank" not in public and "snapshot" not in public and "answer" not in public
    assert restarted.post(f"/api/sessions/{sid}/messages", json={"answer": "9", "attempt_id": "a1"}).status_code == 409


@pytest.mark.parametrize("stage", ["event_clock", "learning_evidence", "events", "runtime_sessions", "gateway_receipts"])
def test_failure_rolls_back_whole_turn_and_retries_once(tmp_path, stage):
    _, store, runtime, sid = prepared(tmp_path)
    before = runtime.load(sid, "student").export_state()
    table_counts = {name: store.conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                    for name in ["event_clock", "verdicts", "learning_evidence", "learning_states", "learning_transitions", "events", "snapshots"]}
    def fault(current):
        if current == stage:
            raise RuntimeError("injected")
    with pytest.raises(RuntimeError, match="injected"):
        runtime.message(sid, "student", answer="8", attempt_id="a1", fault=fault)
    assert runtime.load(sid, "student").export_state() == before
    for name, count in table_counts.items():
        assert store.conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] == count
    first = runtime.message(sid, "student", answer="8", attempt_id="a1")
    assert runtime.message(sid, "student", answer="8", attempt_id="a1") == first
    assert len(LearningService(store).evidences("student")) == 1


def test_llm_work_never_holds_production_write_transaction_and_cas_catches_change(tmp_path, monkeypatch):
    _, store, runtime, sid = prepared(tmp_path)
    from app.orchestration.session import TutorSession
    original = TutorSession.handle_turn
    def interleaved(session, *args, **kwargs):
        assert not store.conn.in_transaction
        store.ensure_learner("another")
        return original(session, *args, **kwargs)
    monkeypatch.setattr(TutorSession, "handle_turn", interleaved)
    with pytest.raises(RuntimeConflict):
        runtime.message(sid, "student", answer="8", attempt_id="a1")
    assert LearningService(store).evidences("student") == []


def test_revoked_consent_blocks_receipt_and_runtime_restore(tmp_path):
    _, store, runtime, sid = prepared(tmp_path)
    runtime.message(sid, "student", answer="8", attempt_id="a1")
    LearningService(store).set_consent("student", [], "withdrawal", "learner", utcnow())
    with pytest.raises(ConsentDenied):
        runtime.message(sid, "student", answer="8", attempt_id="a1")
    with pytest.raises(ConsentDenied):
        runtime.load(sid, "student")


def test_consent_withdrawn_during_model_work_prevents_all_commit(tmp_path, monkeypatch):
    _, store, runtime, sid = prepared(tmp_path)
    from app.orchestration.session import TutorSession
    original = TutorSession.handle_turn
    before = store.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    def withdraw(session, *args, **kwargs):
        assert not store.conn.in_transaction
        LearningService(store).set_consent("student", [], "during-model", "learner", utcnow())
        return original(session, *args, **kwargs)
    monkeypatch.setattr(TutorSession, "handle_turn", withdraw)
    with pytest.raises(ConsentDenied):
        runtime.message(sid, "student", answer="8", attempt_id="a1")
    assert store.conn.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before
    assert store.conn.execute("SELECT COUNT(*) FROM gateway_receipts").fetchone()[0] == 0


def test_two_connections_competing_same_attempt_return_one_receipt(tmp_path, monkeypatch):
    path, store, first_runtime, sid = prepared(tmp_path)
    second_store = Store(path)
    second_runtime = SessionRuntime(second_store, FakeLLM())
    from app.orchestration.session import TutorSession
    original = TutorSession.handle_turn
    barrier = Barrier(2)
    def together(session, *args, **kwargs):
        result = original(session, *args, **kwargs)
        barrier.wait(timeout=5)
        return result
    monkeypatch.setattr(TutorSession, "handle_turn", together)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(runtime.message, sid, "student", answer="8", attempt_id="a1")
                   for runtime in [first_runtime, second_runtime]]
        results = [future.result(timeout=10) for future in futures]
    assert results[0] == results[1]
    assert len(LearningService(store).evidences("student")) == 1
    assert store.conn.execute("SELECT COUNT(*) FROM gateway_receipts").fetchone()[0] == 1
    second_store.close()


def test_concurrent_distinct_sessions_conflict_and_retry_preserves_global_sequence(tmp_path, monkeypatch):
    path, store, runtime, sid1 = prepared(tmp_path)
    sid2 = runtime.create("student", None, "explore")["session_id"]
    second_store = Store(path)
    second_runtime = SessionRuntime(second_store, FakeLLM())
    from app.orchestration.session import TutorSession
    original = TutorSession.handle_turn
    barrier = Barrier(2)
    def together(session, *args, **kwargs):
        result = original(session, *args, **kwargs)
        barrier.wait(timeout=5)
        return result
    monkeypatch.setattr(TutorSession, "handle_turn", together)
    def message(api, sid, key):
        try:
            return api.message(sid, "student", answer="8", attempt_id=key)
        except RuntimeConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(message, runtime, sid1, "one"), pool.submit(message, second_runtime, sid2, "two")]
        results = [future.result(timeout=10) for future in futures]
    assert sum(result == "conflict" for result in results) == 1
    monkeypatch.setattr(TutorSession, "handle_turn", original)
    loser = 0 if results[0] == "conflict" else 1
    (runtime if loser == 0 else second_runtime).message(sid1 if loser == 0 else sid2, "student",
        answer="8", attempt_id="one" if loser == 0 else "two")
    sequences = [row[0] for row in store.conn.execute("SELECT event_seq FROM events UNION ALL SELECT event_seq FROM learning_evidence")]
    assert len(sequences) == len(set(sequences))
    assert len(LearningService(store).evidences("student")) == 2
    second_store.close()


def test_same_client_attempt_key_in_different_sessions_has_independent_identity(tmp_path):
    _, store, runtime, sid1 = prepared(tmp_path)
    sid2 = runtime.create("student", None, "explore")["session_id"]
    first = runtime.message(sid1, "student", answer="8", attempt_id="answer1")
    second = runtime.message(sid2, "student", answer="8", attempt_id="answer1")
    assert first["attempt_id"] == second["attempt_id"] == "answer1"
    evidences = LearningService(store).evidences("student")
    assert len(evidences) == 2 and len({e.attempt_id for e in evidences}) == 2
    assert runtime.message(sid1, "student", answer="8", attempt_id="answer1") == first
