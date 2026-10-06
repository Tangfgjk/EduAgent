from datetime import timedelta

import pytest

from app.learning.memory_review import MemoryAnnotation, MemoryReviewService
from app.storage.db import Store
from tests.test_learning_storage import NOW, evidence, service


@pytest.fixture
def setup():
    learning = service()
    learning.consume(evidence("source"))
    reviews = MemoryReviewService(learning.store)
    view = reviews.memory.rebuild("s1", NOW)
    return learning, reviews, view


def annotation(view, **changes):
    return MemoryAnnotation(**(dict(annotation_ref="human-test-note", learner_ref="s1",
        view_ref=view["view_id"], source_fingerprint=view["fingerprint"], reviewer_ref="teacher-test-fixture",
        reviewed_at=NOW, action="clarify", source_evidence_refs=("source",),
        note="Clarify this single attempt only; not a personality or mastery conclusion.") | changes))


def test_source_bound_append_is_idempotent_and_never_changes_counts_or_mastery(setup):
    learning, reviews, view = setup
    before = learning.masteries("s1")
    record = annotation(view)
    assert reviews.append(record, as_of=NOW, verify_reviewer=lambda _: True)["status"] == "appended"
    assert reviews.append(record, as_of=NOW, verify_reviewer=lambda _: True)["status"] == "existing"
    projected = reviews.projection("s1", view["view_id"], as_of=NOW)
    assert projected["source_memory"] == view
    assert projected["annotations"][0]["annotation_ref"] == record.annotation_ref
    assert not projected["modifies_learning_state"]
    assert not projected["describes_personality"]
    assert learning.masteries("s1") == before
    assert len(learning.evidences("s1")) == 1
    assert "p_mastery" not in projected
    assert learning.store.conn.execute("SELECT count(*) FROM memory_annotations").fetchone()[0] == 1


def test_revise_and_retract_are_append_only_with_asof_projection(setup):
    _, reviews, view = setup
    first = annotation(view)
    second = annotation(view, annotation_ref="revision", reviewed_at=NOW + timedelta(minutes=1),
                        supersedes=first.annotation_ref, note="Revised single-observation clarification based on the cited source.")
    third = annotation(view, annotation_ref="retraction", action="retract", supersedes=second.annotation_ref,
                       reviewed_at=NOW + timedelta(minutes=2))
    for row in (first, second, third):
        reviews.append(row, as_of=NOW + timedelta(minutes=3), verify_reviewer=lambda _: True)
    assert reviews.projection("s1", view["view_id"], as_of=NOW)["annotations"][0]["annotation_ref"] == first.annotation_ref
    assert reviews.projection("s1", view["view_id"], as_of=NOW + timedelta(minutes=1))["annotations"][0]["annotation_ref"] == "revision"
    current = reviews.projection("s1", view["view_id"], as_of=NOW + timedelta(minutes=3))
    assert current["annotations"] == []
    assert len(current["history_refs"]) == 3


def test_current_consent_withdrawal_blocks_read_and_append(setup):
    learning, reviews, view = setup
    reviews.append(annotation(view), as_of=NOW, verify_reviewer=lambda _: True)
    learning.set_consent("s1", [], "withdrawal", "learner", NOW)
    with pytest.raises(PermissionError):
        reviews.projection("s1", view["view_id"], as_of=NOW)
    with pytest.raises(PermissionError):
        reviews.append(annotation(view, annotation_ref="next"), as_of=NOW, verify_reviewer=lambda _: True)


def test_evidence_correction_invalidates_view_and_annotation_until_rebuild(setup):
    learning, reviews, view = setup
    reviews.append(annotation(view), as_of=NOW, verify_reviewer=lambda _: True)
    learning.consume(evidence("fixed", supersedes="source", attempt_id="source", verdict_status="failed", score=0))
    with pytest.raises(ValueError, match="rebuild"):
        reviews.projection("s1", view["view_id"], as_of=NOW)
    rebuilt = reviews.memory.rebuild("s1", NOW)
    assert rebuilt["fingerprint"] != view["fingerprint"]
    assert reviews.projection("s1", rebuilt["view_id"], as_of=NOW)["annotations"] == []


@pytest.mark.parametrize("changes,error", [
    ({"source_fingerprint": "0" * 64}, ValueError),
    ({"source_evidence_refs": ("other-person-evidence",)}, ValueError),
    ({"source_event_refs": ("unknown-event",)}, ValueError),
    ({"learner_ref": "s2"}, PermissionError),
    ({"reviewed_at": NOW + timedelta(minutes=1)}, ValueError),
    ({"reviewed_at": NOW - timedelta(minutes=1)}, ValueError),
    ({"supersedes": "unknown"}, ValueError),
])
def test_forged_and_unknown_source_or_authority_are_denied(setup, changes, error):
    _, reviews, view = setup
    with pytest.raises(error):
        reviews.append(annotation(view, **changes), as_of=NOW, verify_reviewer=lambda _: True)


def test_no_self_claimed_authority_conflicts_or_revision_branches(setup):
    _, reviews, view = setup
    record = annotation(view)
    with pytest.raises(PermissionError):
        reviews.append(record, as_of=NOW)
    reviews.append(record, as_of=NOW, verify_reviewer=lambda _: True)
    with pytest.raises(ValueError, match="conflicting"):
        reviews.append(annotation(view, note="Changed text with reused immutable annotation ID is forbidden."),
                       as_of=NOW, verify_reviewer=lambda _: True)
    revision = annotation(view, annotation_ref="second", supersedes=record.annotation_ref)
    reviews.append(revision, as_of=NOW, verify_reviewer=lambda _: True)
    with pytest.raises(ValueError, match="already superseded"):
        reviews.append(annotation(view, annotation_ref="third", supersedes=record.annotation_ref),
                       as_of=NOW, verify_reviewer=lambda _: True)


@pytest.mark.parametrize("changes", [
    {"source_evidence_refs": (), "source_event_refs": ()},
    {"describes_personality": True}, {"modifies_learning_state": True},
    {"action": "retract"}, {"supersedes": "human-test-note"},
    {"source_evidence_refs": ("source", "source")},
])
def test_contract_rejects_unbound_or_profile_changing_annotations(setup, changes):
    _, _, view = setup
    with pytest.raises(ValueError):
        annotation(view, **changes)


def test_persisted_annotations_reopen_and_cross_learner_view_is_not_readable(tmp_path):
    path = str(tmp_path / "memory.sqlite3")
    learning = service(Store(path))
    learning.consume(evidence("source"))
    reviews = MemoryReviewService(learning.store)
    view = reviews.memory.rebuild("s1", NOW)
    reviews.append(annotation(view), as_of=NOW, verify_reviewer=lambda _: True)
    learning.set_consent("s2", ["teaching"], "c1", "student:local", NOW)
    with pytest.raises(KeyError):
        reviews.projection("s2", view["view_id"], as_of=NOW)
    learning.store.close()
    reopened_store = Store(path)
    try:
        reopened = MemoryReviewService(reopened_store)
        assert reopened.projection("s1", view["view_id"], as_of=NOW)["annotations"][0]["annotation_ref"] == "human-test-note"
    finally:
        reopened_store.close()


def test_audit_failure_rolls_back_annotation_append(setup):
    learning, reviews, view = setup
    learning.store.conn.execute("CREATE TRIGGER fail_memory_audit BEFORE INSERT ON learning_audit BEGIN SELECT RAISE(ABORT, 'audit-fault'); END")
    learning.store.conn.commit()
    with pytest.raises(Exception, match="audit-fault"):
        reviews.append(annotation(view), as_of=NOW, verify_reviewer=lambda _: True)
    assert learning.store.conn.execute("SELECT count(*) FROM memory_annotations").fetchone()[0] == 0
