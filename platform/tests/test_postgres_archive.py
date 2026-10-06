"""Real PostgreSQL integration; opt in with DSN or ignored local configuration."""
import os
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.schema import utcnow
from app.learning.schema import LearningEvidence
from app.learning.service import LearningService, ConsentDenied
from app.storage.db import Store
from app.storage.postgres import PostgresArchive


def configured_dsn():
    dsn = os.environ.get("EDUAGENT_POSTGRES_DSN")
    config = Path(__file__).resolve().parents[1] / "data" / "postgres" / ".env.local"
    if not dsn and config.exists():
        for line in config.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key == "EDUAGENT_POSTGRES_DSN":
                dsn = value
    return dsn


@pytest.fixture
def archive_source():
    dsn = configured_dsn()
    if not dsn:
        pytest.skip("PostgreSQL integration requires EDUAGENT_POSTGRES_DSN")
    archive = PostgresArchive(dsn)
    archive.bootstrap()
    learner_id = "pg-test:" + uuid4().hex
    store = Store()
    service = LearningService(store)
    service.set_consent(learner_id, ["teaching"], "test-c1", "learner:test", utcnow())
    for number in range(2):
        evidence = LearningEvidence(evidence_id=f"{learner_id}:e{number}", learner_id=learner_id,
            session_id="pg-test", kc_refs=["MATH.G7.EQ.SOLVE"], attempt_id=f"a{number}", artifact_ref=f"artifact{number}",
            verdict_ref=f"verdict{number}", verdict_status="passed", verifier_version="sympy-v1", confidence=1,
            occurred_at=utcnow(), consent_scope=["teaching"], consent_version="test-c1", authorization_source="learner:test",
            assessment_kind="review", assessment_id=f"q{number}", score=1)
        service.consume(evidence)
    yield archive, store, service, learner_id
    # Delete only records uniquely created by this fixture; never touch a user database/schema.
    with archive.connection() as conn:
        for table in ["eduagent_archive_receipts", "eduagent_transitions", "eduagent_learning_states", "eduagent_evidence"]:
            conn.execute(f"DELETE FROM {table} WHERE learner_id=%s", (learner_id,))
    store.close()


def test_actual_postgres_export_restore_and_idempotence(archive_source):
    archive, store, service, learner = archive_source
    assert "PostgreSQL 16" in archive.health()["server_version"]
    before = service.evidences(learner)
    receipt = archive.export_from_sqlite(store, learner)
    assert receipt["evidence_count"] == 2 and receipt["state_count"] == 2 and receipt["transition_count"] == 4
    assert archive.export_from_sqlite(store, learner) == receipt
    assert archive.evidence(learner) == before
    restored = Store()
    first = archive.restore_to_sqlite(restored, learner, operation_id="test-restore")
    assert first["inserted_evidence"] == 2
    assert archive.restore_to_sqlite(restored, learner, operation_id="same-restore")["inserted_evidence"] == 0
    stored = [LearningEvidence.model_validate_json(r[0]) for r in restored.conn.execute("SELECT payload FROM learning_evidence ORDER BY event_seq")]
    assert stored == before
    assert restored.conn.execute("SELECT MAX(seq) FROM event_clock").fetchone()[0] == max(e.event_seq for e in before)
    restored.close()


@pytest.mark.parametrize("stage", ["evidence", "state", "transition", "receipt"])
def test_postgres_export_fault_is_atomic(archive_source, stage):
    archive, store, _, learner = archive_source
    def fault(current):
        if current == stage:
            raise RuntimeError("injected")
    with pytest.raises(RuntimeError, match="injected"):
        archive.export_from_sqlite(store, learner, fault=fault)
    with archive.connection() as conn:
        for table in ["eduagent_evidence", "eduagent_learning_states", "eduagent_transitions", "eduagent_archive_receipts"]:
            assert conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE learner_id=%s", (learner,)).fetchone()["n"] == 0
    assert archive.export_from_sqlite(store, learner)["evidence_count"] == 2


def test_postgres_rejects_conflicting_fact_without_rewriting_archive(archive_source):
    archive, store, service, learner = archive_source
    archive.export_from_sqlite(store, learner)
    original = service.evidences(learner)[0]
    changed = original.model_copy(update={"score": .25})
    store.conn.execute("UPDATE learning_evidence SET payload=? WHERE evidence_id=?", (changed.model_dump_json(), original.evidence_id))
    store.conn.commit()
    with pytest.raises(ValueError, match="identity conflict"):
        archive.export_from_sqlite(store, learner)
    assert archive.evidence(learner)[0] == original


def test_restore_detects_archive_checksum_tampering_and_has_no_partial_insert(archive_source):
    archive, store, _, learner = archive_source
    archive.export_from_sqlite(store, learner)
    with archive.connection() as conn:
        conn.execute("UPDATE eduagent_evidence SET payload=jsonb_set(payload,'{score}','0.2'::jsonb) WHERE learner_id=%s", (learner,))
    restored = Store()
    with pytest.raises(ValueError, match="checksum mismatch"):
        archive.restore_to_sqlite(restored, learner, operation_id="corrupt")
    assert restored.conn.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0] == 0
    restored.close()


def test_export_respects_current_teaching_authorization(archive_source):
    archive, store, service, learner = archive_source
    service.set_consent(learner, [], "withdraw", "learner:test", utcnow())
    with pytest.raises(ConsentDenied):
        archive.export_from_sqlite(store, learner)


def test_archive_rejects_partial_historical_snapshot(archive_source):
    archive, store, _, learner = archive_source
    with pytest.raises(ValueError, match="Historical archive"):
        archive.export_from_sqlite(store, learner, as_of=utcnow())


def test_concurrent_archive_export_is_idempotent(archive_source):
    archive, store, _, learner = archive_source
    with ThreadPoolExecutor(max_workers=4) as executor:
        receipts = list(executor.map(lambda _: archive.export_from_sqlite(store, learner), range(4)))
    assert all(item == receipts[0] for item in receipts)
    with archive.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM eduagent_archive_receipts WHERE learner_id=%s", (learner,)).fetchone()["n"] == 1


def test_archive_state_same_version_conflict_is_not_overwritten(archive_source):
    archive, store, _, learner = archive_source
    archive.export_from_sqlite(store, learner)
    with archive.connection() as conn:
        conn.execute("UPDATE eduagent_learning_states SET payload=jsonb_set(payload,'{state_version}','99'::jsonb) WHERE learner_id=%s", (learner,))
    # Force a distinct operation without modifying immutable evidence facts.
    with store.lock:
        row = store.conn.execute("SELECT payload FROM learning_states WHERE learner_id=? LIMIT 1", (learner,)).fetchone()
        import json
        state = json.loads(row[0])
        state["audit_marker"] = "changed"
        store.conn.execute("UPDATE learning_states SET payload=? WHERE learner_id=?", (json.dumps(state), learner))
        store.conn.commit()
    with pytest.raises(ValueError, match="state version identity conflict"):
        archive.export_from_sqlite(store, learner)


def test_withdrawal_during_export_rolls_back_archive(archive_source):
    archive, store, service, learner = archive_source
    def withdraw(stage):
        if stage == "receipt":
            service.set_consent(learner, [], "withdraw-during", "learner:test", utcnow())
    with pytest.raises(ConsentDenied):
        archive.export_from_sqlite(store, learner, fault=withdraw)
    with archive.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM eduagent_evidence WHERE learner_id=%s", (learner,)).fetchone()["n"] == 0
