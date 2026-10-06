from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.learning.course_governance import CourseReviewPackage, admit_course
from app.learning.pilot_readiness import PilotApproval, PilotProtocol, REQUIRED_SCOPES, pilot_readiness


NOW = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)
ROOT = Path(__file__).parents[1] / "seeds/review"


@pytest.fixture
def protocol():
    return PilotProtocol.load(ROOT / "pilot_protocol_pending_v3_20261006.json")


def approvals(protocol):
    return tuple(PilotApproval(approval_ref=f"test-only-{scope}", approver_ref="external-authority-test-fixture",
        protocol_sha256=protocol.content_hash, scope=scope, decision="approve", approved_at=NOW,
        expires_at=NOW + timedelta(days=30), rationale="Test fixture, not actual institutional or ethics approval.")
        for scope in REQUIRED_SCOPES)


def course_report():
    package = CourseReviewPackage.load(ROOT / "math_g7_linear_equations_pending_v3_20261006.json")
    return admit_course(package, as_of=NOW)


def test_pending_protocol_is_hash_bound_and_all_real_actions_disabled(protocol):
    result = pilot_readiness(protocol, as_of=NOW, course_report=course_report())
    assert not result.preparation_ready
    assert len(result.blockers) == 10
    assert "currently_approved_course_unverified" in result.blockers
    assert protocol.course_package_sha256 == course_report().package_sha256
    assert not result.recruitment_enabled
    assert not result.real_collection_enabled
    assert not result.causal_effect_claim


def test_complete_trusted_fixture_only_unlocks_readiness_not_collection(protocol):
    report = course_report().model_copy(update={"human_pilot_admitted": True})
    approval_records = approvals(protocol)
    result = pilot_readiness(protocol, as_of=NOW, course_report=report, verify_course_report=lambda _: True,
        approvals=approval_records, verify_approval=lambda record: record in approval_records)
    assert result.preparation_ready
    assert len(result.approval_refs) == 9
    assert not result.recruitment_enabled
    assert not result.real_collection_enabled


def test_untrusted_approval_claims_cannot_unlock_readiness(protocol):
    result = pilot_readiness(protocol, as_of=NOW, approvals=approvals(protocol))
    assert not result.preparation_ready
    assert all(f"{scope}:authority_unverified" in result.blockers for scope in REQUIRED_SCOPES)


@pytest.mark.parametrize("field", ["research_question", "retention_policy", "course_package_sha256"])
def test_changed_preregistration_invalidates_previous_approvals(protocol, field):
    records = approvals(protocol)
    value = "0" * 64 if field.endswith("sha256") else getattr(protocol, field) + " changed"
    changed = PilotProtocol.model_validate(protocol.model_dump() | {field: value})
    result = pilot_readiness(changed, as_of=NOW, approvals=records, verify_approval=lambda _: True)
    assert not result.preparation_ready
    assert all(f"{scope}:protocol_hash_mismatch" in result.blockers for scope in REQUIRED_SCOPES)


@pytest.mark.parametrize("change,expected", [
    ({"expires_at": NOW}, "approval_expired_or_time_invalid"),
    ({"decision": "reject"}, "approval_not_granted"),
    ({"decision": "revise"}, "approval_not_granted"),
    ({"approved_at": NOW + timedelta(minutes=1)}, "approval_expired_or_time_invalid"),
])
def test_invalid_approvals_fail_closed(protocol, change, expected):
    record = approvals(protocol)[0].model_copy(update=change)
    result = pilot_readiness(protocol, as_of=NOW, approvals=(record,), verify_approval=lambda _: True)
    assert not result.preparation_ready
    assert f"ethics:{expected}" in result.blockers


def test_duplicate_approval_identity_and_naive_as_of_rejected(protocol):
    record = approvals(protocol)[0]
    result = pilot_readiness(protocol, as_of=NOW, approvals=(record, record), verify_approval=lambda _: True)
    assert "duplicate_approval_identity" in result.blockers
    with pytest.raises(ValueError, match="timezone"):
        pilot_readiness(protocol, as_of=NOW.replace(tzinfo=None))


@pytest.mark.parametrize("change", [
    {"recruitment_authorized": True}, {"real_collection_enabled": True}, {"causal_effect_claim": True},
    {"proposed_sample_min": 40}, {"consent_requirements": ("teaching",)},
])
def test_protocol_cannot_fabricate_permissions_or_invalid_scope(protocol, change):
    with pytest.raises(ValueError):
        PilotProtocol.model_validate(protocol.model_dump() | change)
