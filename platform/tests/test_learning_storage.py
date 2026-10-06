from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from app.learning.schema import LearningEvidence
from app.learning.service import LearningService, EvidenceConflict, ConsentDenied
from app.storage.db import Store

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)


def evidence(evidence_id="e1", **updates):
    fields = dict(evidence_id=evidence_id, learner_id="s1", session_id="session",
                  kc_refs=["MATH.G7.EQ.SOLVE"], attempt_id=evidence_id,
                  artifact_ref=evidence_id, verdict_ref=evidence_id,
                  verdict_status="passed", verifier_version="symbolic-v1",
                  confidence=1, occurred_at=NOW, consent_scope=["teaching"],
                  consent_version="c1", authorization_source="student:local",
                  assessment_kind="review", score=1)
    return LearningEvidence(**(fields | updates))


def service(store=None):
    result = LearningService(store or Store())
    result.set_consent("s1", ["teaching"], "c1", "student:local", NOW)
    return result


def test_duplicate_and_conflicting_evidence():
    s = service()
    first = s.consume(evidence())
    assert s.consume(evidence()) == first
    assert s.masteries("s1")[0].state_version == 1
    with pytest.raises(EvidenceConflict):
        s.consume(evidence(score=0.5))
    assert len(s.evidences("s1")) == 1


@pytest.mark.parametrize("stage", ["evidence", "mastery", "retention", "transition", "snapshot"])
def test_faults_roll_back_all_outputs(stage):
    s = service()
    def fault(at):
        if at == stage:
            raise RuntimeError(stage)
    with pytest.raises(RuntimeError):
        s.consume(evidence(), fault=fault)
    assert s.evidences("s1") == []
    assert s.masteries("s1") == []
    assert s.transitions("s1") == []
    assert s.store.latest_snapshot("s1") is None
    assert s.consume(evidence())["event_seq"] == 1


def test_concurrent_connections_consume_once(tmp_path):
    db = str(tmp_path / "concurrent.sqlite3")
    service(Store(db))
    def run(_):
        s = LearningService(Store(db))
        try:
            return s.consume(evidence())
        finally:
            s.store.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(8)))
    assert all(r == results[0] for r in results)
    s = LearningService(Store(db))
    assert s.masteries("s1")[0].state_version == 1
    assert len(s.transitions("s1")) == 2


def test_withdrawal_blocks_reads_and_consumption():
    s = service()
    s.consume(evidence())
    with pytest.raises(ConsentDenied):
        s.evidences("s1", purpose="research")
    s.set_consent("s1", [], "c2", "student:local", NOW)
    for action in [lambda: s.evidences("s1"), lambda: s.masteries("s1"),
                   lambda: s.consume(evidence("e2"))]:
        with pytest.raises(ConsentDenied):
            action()


def test_correction_and_late_evidence_rebuild_new_versions():
    s = service()
    s.consume(evidence())
    first = s.masteries("s1")[0]
    s.consume(evidence("e2", attempt_id="e1", supersedes="e1", verdict_status="failed", score=0))
    second = s.masteries("s1")[0]
    assert second.state_version > first.state_version
    assert second.evidence_refs == ["e2"]
    assert second.p_mastery < first.p_mastery
    assert len(s.evidences("s1")) == 2
    assert s.rebuild("s1", as_of=NOW)["mastery"][0]["p_mastery"] == second.p_mastery


def test_unknown_learner_and_cross_learner_correction():
    s = service()
    with pytest.raises(ConsentDenied):
        s.consume(evidence(learner_id="s2"))
    s.consume(evidence())
    s.set_consent("s2", ["teaching"], "c1", "student:local", NOW)
    with pytest.raises(EvidenceConflict):
        s.consume(evidence("e2", learner_id="s2", supersedes="e1"))


def test_cas_conflict_keeps_prior_state():
    s = service()
    s.consume(evidence())
    with pytest.raises(EvidenceConflict):
        s.consume(evidence("e2"), expected_versions={"MATH.G7.EQ.SOLVE:mastery": 0})
    assert len(s.evidences("s1")) == 1


def test_attempt_cannot_be_reidentified_and_corrections_are_linear():
    s = service()
    s.consume(evidence())
    with pytest.raises(EvidenceConflict):
        s.consume(evidence("new-id",attempt_id="e1"))
    s.consume(evidence("fix1",attempt_id="e1",supersedes="e1",verdict_status="failed"))
    with pytest.raises(EvidenceConflict):
        s.consume(evidence("fix2",attempt_id="e1",supersedes="e1"))
    s.consume(evidence("fix3",attempt_id="e1",supersedes="fix1"))
    assert s.masteries("s1")[0].evidence_refs == ["fix3"]


def test_receipt_failure_rolls_back_learning_state():
    s = service()
    def fault(stage):
        if stage == "receipt":
            raise RuntimeError("receipt unavailable")
    with pytest.raises(RuntimeError):
        s.consume(evidence(),receipt=("e1","hash",lambda result:result),fault=fault)
    assert s.evidences("s1") == []
    assert s.store.conn.execute("SELECT COUNT(*) FROM learning_api_receipts").fetchone()[0] == 0


@pytest.mark.parametrize("refs", [["math"],["MATH.G7.EQ.SOLVE","MATH.G7.EQ.SOLVE"]])
def test_kc_scope_is_validated_before_storage(refs):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        evidence(kc_refs=refs)
