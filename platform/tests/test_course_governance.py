from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.learning.assets import VersionedRef
from app.learning.course_draft import build_equation_course_draft
from app.learning.course_governance import CourseReview, CourseReviewPackage, admit_course, structural_issues


NOW = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)
SEED = Path(__file__).parents[1] / "seeds/review/math_g7_linear_equations_pending_v3_20261006.json"


@pytest.fixture
def package():
    return CourseReviewPackage.load(SEED)


def classroom_candidate(package):
    payload = package.model_dump(mode="json")
    payload["source"]["usage_scope"] = "classroom_candidate"
    return CourseReviewPackage.model_validate(payload)


def review(package, **changes):
    payload = dict(review_ref="externally-verified-review-fixture", reviewer_ref="teacher-test-fixture",
        reviewed_at=NOW, package_sha256=package.content_hash, decision="approve",
        reviewed_assessment_refs=tuple(asset.ref for asset in package.catalog.assessments),
        comments="Test approval fixture only, not a real teacher signature.")
    payload.update(changes)
    return CourseReview(**payload)


def test_reproducible_seed_is_pending_complete_and_simulation_only(package):
    assert package == build_equation_course_draft()
    result = admit_course(package, as_of=NOW)
    assert result.structural_issues == ()
    assert result.simulation_admitted
    assert not result.human_pilot_admitted
    assert result.human_pilot_blockers == ("human_review_missing", "source_rights_unverified", "source_simulation_only")
    assert result.assessment_count == 24
    assert result.kc_count == 2
    assert package.review_status == "pending"
    assert package.source.source_kind == "synthetic_ai_generated"
    assert not package.real_learner_data_included
    assert not package.educational_effect_claim


def test_reviewed_label_and_request_boolean_cannot_approve(package):
    package = classroom_candidate(package).model_copy(update={"review_status": "reviewed"})
    result = admit_course(package, as_of=NOW, review=review(package))
    assert not result.human_pilot_admitted
    assert "review_authority_unverified" in result.human_pilot_blockers
    assert "source_rights_unverified" in result.human_pilot_blockers
    with pytest.raises(ValueError):
        CourseReview.model_validate({**review(package).model_dump(), "teacher_verified": True})


def test_approval_requires_trusted_ports_exact_version_and_complete_teacher_coverage(package):
    package = classroom_candidate(package)
    record = review(package)
    result = admit_course(package, as_of=NOW, review=record,
        verify_review=lambda supplied: supplied == record,
        verify_source_rights=lambda supplied: supplied == package.source)
    assert result.human_pilot_admitted
    assert result.simulation_admitted
    assert package.source.source_kind == "synthetic_ai_generated"


@pytest.mark.parametrize("field", ["title", "hint", "answer", "source", "objective", "misconception", "window"])
def test_every_frozen_course_component_invalidates_previous_approval(package, field):
    package = classroom_candidate(package)
    approval = review(package)
    payload = package.model_dump(mode="json")
    if field == "title":
        payload["title"] += " changed"
    elif field == "hint":
        payload["task_reviews"][0]["hints"][0]["text"] += " changed"
    elif field == "answer":
        payload["catalog"]["assessments"][0]["answer"]["value"] = "99"
    elif field == "source":
        payload["source"]["license_ref"] += " changed"
    elif field == "objective":
        payload["knowledge_reviews"][0]["observable_objective"] += " changed"
    elif field == "misconception":
        payload["misconceptions"][0]["description"] += " changed"
    else:
        payload["measurement_windows"][-1]["end_offset_minutes"] += 1
    changed = CourseReviewPackage.model_validate(payload)
    assert changed.content_hash != package.content_hash
    result = admit_course(changed, as_of=NOW, review=approval,
                          verify_review=lambda _: True, verify_source_rights=lambda _: True)
    assert not result.human_pilot_admitted
    assert "review_version_hash_mismatch" in result.human_pilot_blockers


@pytest.mark.parametrize("corruption,expected", [
    ("missing_kc", "incomplete_kc_review_coverage"),
    ("missing_task", "incomplete_task_review_coverage"),
    ("missing_rubric", "incomplete_rubric_review_coverage"),
    ("duplicate_task", "duplicate_task_review"),
    ("misconception", "unknown_task_misconception"),
    ("misconception_kc", "misconception_kc_mismatch"),
    ("transfer", "transfer_context_missing"),
    ("answer", "verifiable_answer_missing"),
    ("repeated_stem", "repeated_measurement_stem"),
    ("repeated_id", "repeated_measurement_item"),
    ("comparison", "incomparable_blueprint"),
    ("duplicate_window", "incomplete_measurement_windows"),
    ("overlap", "pre_post_windows_overlap"),
    ("no_delay", "delayed_window_not_delayed"),
])
def test_structural_corruptions_block_simulation_and_human(package, corruption, expected):
    payload = package.model_dump(mode="json")
    if corruption == "missing_kc":
        payload["knowledge_reviews"].pop()
    elif corruption == "missing_task":
        payload["task_reviews"].pop()
    elif corruption == "missing_rubric":
        payload["rubric_reviews"] = [dict(payload["rubric_reviews"][0], rubric_ref={"asset_id": "unknown", "version": "1"})]
    elif corruption == "duplicate_task":
        payload["task_reviews"].append(payload["task_reviews"][0])
    elif corruption == "misconception":
        payload["task_reviews"][0]["misconception_refs"] = ["unknown"]
    elif corruption == "misconception_kc":
        payload["task_reviews"][0]["misconception_refs"] = [payload["misconceptions"][-1]["misconception_id"]]
    elif corruption == "transfer":
        index = next(i for i, asset in enumerate(payload["catalog"]["assessments"]) if asset["kind"] == "transfer")
        payload["task_reviews"][index]["transfer_difference"] = None
    elif corruption == "answer":
        payload["catalog"]["assessments"][0]["answer"] = {}
    elif corruption == "repeated_stem":
        items = [asset for asset in payload["catalog"]["assessments"] if asset["kind"] == "pretest"]
        items[1]["stem"] = items[0]["stem"]
    elif corruption == "repeated_id":
        items = [asset for asset in payload["catalog"]["assessments"] if asset["kind"] == "pretest"]
        items[1]["ref"]["asset_id"] = items[0]["ref"]["asset_id"]
        items[1]["ref"]["version"] += "-changed"
    elif corruption == "comparison":
        asset = next(asset for asset in payload["catalog"]["assessments"] if asset["kind"] == "posttest")
        asset["comparison_group"] = "unreviewed-comparison"
    elif corruption == "duplicate_window":
        payload["measurement_windows"][1] = payload["measurement_windows"][0]
    elif corruption == "overlap":
        payload["measurement_windows"][0]["end_offset_minutes"] = 5
    else:
        payload["measurement_windows"][-1]["start_offset_minutes"] = 30
    changed = CourseReviewPackage.model_validate(payload)
    issues = structural_issues(changed)
    assert any(expected in issue for issue in issues)
    result = admit_course(changed, as_of=NOW, review=review(changed),
                          verify_review=lambda _: True, verify_source_rights=lambda _: True)
    assert not result.simulation_admitted
    assert not result.human_pilot_admitted


@pytest.mark.parametrize("corruption", ["answer_hint", "noncontiguous_hint", "invalid_source_hash", "naive_date", "real_data", "claim", "unknown_field", "empty_window"])
def test_invalid_contracts_rejected_before_admission(package, corruption):
    payload = package.model_dump(mode="json")
    if corruption == "answer_hint":
        payload["task_reviews"][0]["hints"][0]["answer_exposed"] = True
    elif corruption == "noncontiguous_hint":
        payload["task_reviews"][0]["hints"][0]["level"] = 2
    elif corruption == "invalid_source_hash":
        payload["source"]["source_sha256"] = "not-a-hash"
    elif corruption == "naive_date":
        payload["created_at"] = "2026-10-06T10:00:00"
    elif corruption == "real_data":
        payload["real_learner_data_included"] = True
    elif corruption == "claim":
        payload["educational_effect_claim"] = True
    elif corruption == "unknown_field":
        payload["approved_by_ai"] = True
    else:
        payload["measurement_windows"][0]["end_offset_minutes"] = -60
    with pytest.raises(ValueError):
        CourseReviewPackage.model_validate(payload)


@pytest.mark.parametrize("changes,reason", [
    ({"decision": "revise"}, "human_approval_missing"),
    ({"decision": "reject"}, "human_approval_missing"),
    ({"reviewed_at": NOW + timedelta(seconds=1)}, "review_time_invalid"),
    ({"reviewed_at": NOW - timedelta(days=1)}, "review_time_invalid"),
    ({"reviewed_assessment_refs": (VersionedRef(asset_id="invented", version="1"),)}, "incomplete_teacher_task_coverage"),
])
def test_incomplete_or_invalid_teacher_reviews_remain_blocked(package, changes, reason):
    package = classroom_candidate(package)
    result = admit_course(package, as_of=NOW, review=review(package, **changes),
                          verify_review=lambda _: True, verify_source_rights=lambda _: True)
    assert not result.human_pilot_admitted
    assert reason in result.human_pilot_blockers


def test_future_or_rejected_course_and_naive_as_of(package):
    future = package.model_copy(update={"created_at": NOW + timedelta(seconds=1)})
    assert not admit_course(future, as_of=NOW).simulation_admitted
    rejected = package.model_copy(update={"review_status": "rejected"})
    assert not admit_course(rejected, as_of=NOW).simulation_admitted
    with pytest.raises(ValueError, match="timezone"):
        admit_course(package, as_of=NOW.replace(tzinfo=None))


def test_hash_is_canonical_json_and_read_only(package):
    restored = CourseReviewPackage.model_validate_json(package.model_dump_json())
    assert package.content_hash == restored.content_hash
    before = package.model_dump_json()
    admit_course(package, as_of=NOW)
    assert package.model_dump_json() == before


def test_answer_exposure_is_checked_even_if_label_claims_false(package):
    payload = package.model_dump(mode="json")
    asset = payload["catalog"]["assessments"][0]
    payload["task_reviews"][0]["hints"][1]["text"] = f"最终结果是 {asset['answer']['var']} = {asset['answer']['value']}，请抄写。"
    changed = CourseReviewPackage.model_validate(payload)
    assert any("unmarked_direct_answer_hint" in issue for issue in structural_issues(changed))
    assert not admit_course(changed, as_of=NOW).simulation_admitted
