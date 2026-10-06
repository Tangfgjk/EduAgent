from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.learning.course_governance import CourseReviewPackage
from app.learning.measurement_admission import (
    MeasurementRecord, OfflinePartition, admit_measurements, offline_partition,
)


ANCHOR = datetime(2026, 10, 7, tzinfo=timezone.utc)
AS_OF = ANCHOR + timedelta(days=15)
SEED = Path(__file__).parents[1] / "seeds/review/math_g7_linear_equations_pending_v3_20261006.json"


@pytest.fixture
def package():
    return CourseReviewPackage.load(SEED)


def records(package, learner="synthetic-test-learner", **changes):
    offsets = {window.phase: window.start_offset_minutes + 1 for window in package.measurement_windows}
    return tuple(MeasurementRecord(**(dict(evidence_ref=f"{learner}-{asset.ref.key}", learner_ref=learner,
        assessment_ref=asset.ref, package_sha256=package.content_hash, phase=asset.kind,
        source_kind="synthetic_ai_generated", purpose="simulation_precalibration",
        occurred_at=ANCHOR + timedelta(minutes=offsets[asset.kind]), attempt=1, score=0.5) | changes))
        for asset in package.catalog.assessments if asset.kind in offsets)


def admission(package, rows, **kwargs):
    return admit_measurements(rows, package, purpose="simulation_precalibration",
                              lesson_anchor=ANCHOR, as_of=AS_OF, **kwargs)


def test_complete_synthetic_measurement_has_all_cells_no_real_or_effect_claim(package):
    rows = records(package)
    result = admission(package, rows)
    assert len(result.admitted_records) == 16
    assert result.missing_blueprint_cells == ()
    assert result.excluded_counts == {}
    assert not result.educational_effect_claim
    assert not result.automatic_parameter_promotion
    assert result.purpose == "simulation_precalibration"
    assert all(row.source_kind == "synthetic_ai_generated" for row in result.admitted_records)


@pytest.mark.parametrize("changes,reason", [
    ({"hint_level": 1}, "assisted_observation"),
    ({"assistance_mode": "hint"}, "assisted_observation"),
    ({"answer_exposed": True}, "assisted_observation"),
    ({"verified": False}, "unverified_observation"),
    ({"attempt": 2}, "repeated_or_nonfirst_attempt"),
    ({"package_sha256": "0" * 64}, "course_version_mismatch"),
    ({"occurred_at": AS_OF + timedelta(seconds=1)}, "future_observation"),
    ({"occurred_at": ANCHOR - timedelta(days=3)}, "outside_measurement_window"),
    ({"source_kind": "real_learning_data"}, "source_or_purpose_mismatch"),
    ({"purpose": "research"}, "source_or_purpose_mismatch"),
])
def test_ineligible_measurements_never_count(package, changes, reason):
    result = admission(package, records(package, **changes))
    assert result.admitted_records == ()
    assert result.excluded_counts == {reason: 16}
    assert len(result.missing_blueprint_cells) == 8


def test_research_default_denies_and_current_authority_and_history_are_required(package):
    rows = records(package, source_kind="real_learning_data", purpose="research", research_consent_ref="research-test-fixture")
    params = dict(purpose="research", lesson_anchor=ANCHOR, as_of=AS_OF)
    result = admit_measurements(rows, package, **params)
    assert result.excluded_counts == {"approved_course_unverified": 16}
    result = admit_measurements(rows, package, **params, verify_course_admission=lambda _: True)
    assert result.excluded_counts == {"current_research_authorization_missing": 16}
    result = admit_measurements(rows, package, **params, verify_course_admission=lambda _: True, authorize_research=lambda _: True)
    assert result.excluded_counts == {"evidence_or_first_exposure_history_unverified": 16}
    result = admit_measurements(rows, package, **params, verify_course_admission=lambda _: True,
        authorize_research=lambda _: True, verify_record_history=lambda _: True)
    assert len(result.admitted_records) == 16
    withdrawn = admit_measurements(rows, package, **params, verify_course_admission=lambda _: True,
        authorize_research=lambda _: False, verify_record_history=lambda _: True)
    assert withdrawn.admitted_records == ()


def test_synthetic_samples_cannot_be_admitted_to_research_even_if_callbacks_accept(package):
    result = admit_measurements(records(package), package, purpose="research", lesson_anchor=ANCHOR,
        as_of=AS_OF, verify_course_admission=lambda _: True, authorize_research=lambda _: True,
        verify_record_history=lambda _: True)
    assert not result.admitted_records
    assert result.excluded_counts == {"source_or_purpose_mismatch": 16}


def test_first_failed_or_assisted_exposure_cannot_be_retried_as_independent(package):
    first = records(package)[0].model_copy(update={"hint_level": 1})
    retry = first.model_copy(update={"evidence_ref": "second-attempt", "hint_level": 0,
                                    "occurred_at": first.occurred_at + timedelta(minutes=1)})
    result = admission(package, (retry, first))
    assert result.admitted_records == ()
    assert result.excluded_counts == {"assisted_observation": 1, "repeated_or_nonfirst_attempt": 1}


def test_record_identity_deduplication_and_conflict(package):
    first = records(package)[0]
    result = admission(package, (first, first))
    assert result.admitted_records == (first,)
    assert result.excluded_counts == {}
    conflicting = first.model_copy(update={"score": 1})
    with pytest.raises(ValueError, match="conflicting"):
        admission(package, (first, conflicting))


@pytest.mark.parametrize("phase", ["pretest", "posttest", "transfer", "delayed"])
def test_half_open_window_start_inclusive_end_exclusive(package, phase):
    row = next(row for row in records(package) if row.phase == phase)
    window = next(window for window in package.measurement_windows if window.phase == phase)
    start = row.model_copy(update={"occurred_at": ANCHOR + timedelta(minutes=window.start_offset_minutes)})
    end = row.model_copy(update={"occurred_at": ANCHOR + timedelta(minutes=window.end_offset_minutes)})
    assert len(admission(package, (start,)).admitted_records) == 1
    assert admission(package, (end,)).excluded_counts == {"outside_measurement_window": 1}


def test_wrong_phase_and_invalid_timezone_block(package):
    first = records(package)[0]
    wrong = first.model_copy(update={"phase": "posttest"})
    assert admission(package, (wrong,)).excluded_counts == {"assessment_phase_mismatch": 1}
    with pytest.raises(ValueError, match="timezone"):
        admit_measurements((first,), package, purpose="simulation_precalibration",
            lesson_anchor=ANCHOR.replace(tzinfo=None), as_of=AS_OF)
    with pytest.raises(ValueError, match="unsupported"):
        admit_measurements((first,), package, purpose="teaching", lesson_anchor=ANCHOR, as_of=AS_OF)


def test_deterministic_two_axis_holdout_prevents_same_learner_and_item_leakage(package):
    rows = records(package, learner="training-learner") + records(package, learner="heldout-learner")
    result = admission(package, rows)
    heldout_items = tuple(row.assessment_ref.asset_id for row in rows[:8])
    split = offline_partition(result, heldout_learner_refs=("heldout-learner",), heldout_assessment_ids=heldout_items)
    assert len(split.training) == len(split.validation) == 8
    assert split.dropped_cross_partition_count == 16
    assert not ({row.learner_ref for row in split.training} & {row.learner_ref for row in split.validation})
    assert not ({row.assessment_ref.asset_id for row in split.training} & {row.assessment_ref.asset_id for row in split.validation})
    reverse = admission(package, tuple(reversed(rows)))
    assert split == offline_partition(reverse, heldout_learner_refs=("heldout-learner",), heldout_assessment_ids=heldout_items)


def test_holdout_explicit_and_leakage_rejected(package):
    result = admission(package, records(package))
    with pytest.raises(ValueError, match="explicit"):
        offline_partition(result, heldout_learner_refs=(), heldout_assessment_ids=("id",))
    row = result.admitted_records[0]
    with pytest.raises(ValueError, match="learner leakage"):
        OfflinePartition(training=(row,), validation=(row,), heldout_learner_refs=(row.learner_ref,),
                         heldout_assessment_ids=(row.assessment_ref.asset_id,), dropped_cross_partition_count=0)


def test_empty_training_or_validation_is_not_a_calibration_ready_split(package):
    result = admission(package, records(package))
    with pytest.raises(ValueError, match="nonempty"):
        offline_partition(result, heldout_learner_refs=("unknown",), heldout_assessment_ids=("unknown",))
