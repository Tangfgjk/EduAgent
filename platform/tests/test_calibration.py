"""Calibration governance cannot turn synthetic fixtures into effect evidence."""
from datetime import datetime, timezone

import pytest

from app.learning.calibration import (
    CalibrationObservation, ManualReview, ParameterCandidate,
    evaluate_calibration, human_sampling_plan, promotion_gate as _promotion_gate, wilson_interval,
)

NOW = datetime(2026,10,6,7,tzinfo=timezone.utc)


def promotion_gate(*args,**kwargs):
    kwargs.setdefault("verify_report",lambda report:True)
    return _promotion_gate(*args,**kwargs)


def observations(count=100, errors=0, **updates):
    items=[]
    for i in range(count):
        fields=dict(observation_id=f"o{i}",evidence_ref=f"e{i}",learner_ref=f"private:{i}",
            source_kind="real_learning_data",collection_research_authorized=True,
            consent_version="research-v1",model_version="candidate-v1",parameter_set_id="p-v1",
            predicted_passed=i>=errors,reference_passed=True,reference_source="authorized_human",
            reference_ref=f"review:{i}",group="group-a",occurred_at=NOW)
        fields.update(updates)
        items.append(CalibrationObservation(**fields))
    return items


def authorized(item):
    return item.collection_research_authorized


def candidate():
    return ParameterCandidate(candidate_id="p-v1",model_name="BKT",algorithm_version="bkt-v1",
        candidate_version="1.1.0",baseline_version="1.0.0",parameters={"p_learn":.15},
        evaluation_model_version="candidate-v1",evaluation_parameter_set_id="p-v1")


def reviewed():
    return ManualReview(review_id="manual-1",reviewer_ref="teacher:authorized",reviewed_at=NOW,
        decision="approve_candidate",report_ref="report-1",rationale="checked references and error sampling")


def test_wilson_zero_and_all_error_boundaries():
    assert wilson_interval(0,0) is None
    low,high=wilson_interval(0,100)
    assert low==0 and .03<high<.04
    low,high=wilson_interval(100,100)
    assert .96<low<.97 and high==1
    with pytest.raises(ValueError):
        wilson_interval(101,100)


def test_real_authorized_report_has_error_rate_and_no_learner_identifiers():
    report=evaluate_calibration(observations(errors=10),report_id="report-1",as_of=NOW,authorize=authorized)
    assert report.real_sample_count==100 and report.error_count==10
    assert report.error_rate==.1 and report.error_interval[0]<.1<report.error_interval[1]
    assert report.synthetic_sample_count==0 and not report.educational_effect_claim
    assert "private:" not in report.model_dump_json()
    assert report.groups[0].group=="group-a"


def test_synthetic_and_assisted_or_unreviewed_are_excluded_from_real_calibration():
    synthetic=observations(10,source_kind="synthetic_ai_generated")
    assisted=[item.model_copy(update={"evidence_ref":"assisted:"+item.evidence_ref,"observation_id":"assisted:"+item.observation_id})
              for item in observations(10,assistance_mode="hint",hint_level=1)]
    unreviewed=[item.model_copy(update={"evidence_ref":"unreviewed:"+item.evidence_ref,"observation_id":"unreviewed:"+item.observation_id})
                for item in observations(10,reference_source="model_generated")]
    report=evaluate_calibration(synthetic+assisted+unreviewed,report_id="report-1",as_of=NOW,authorize=authorized)
    assert report.real_sample_count==0 and report.synthetic_sample_count==10
    assert report.error_rate is None and report.error_interval is None
    assert report.rejected_counts["assisted_observation"]==10
    assert report.rejected_counts["unreviewed_reference"]==10


def test_current_authorization_and_future_time_are_checked():
    report=evaluate_calibration(observations(5),report_id="report-1",as_of=NOW,authorize=lambda item:False)
    assert report.real_sample_count==0 and report.rejected_counts["current_research_unauthorized"]==5
    later=datetime(2026,10,7,tzinfo=timezone.utc)
    report=evaluate_calibration(observations(5,occurred_at=later),report_id="report-1",as_of=NOW,authorize=authorized)
    assert report.real_sample_count==0


def test_small_groups_are_hidden_even_if_total_dataset_is_large():
    history=observations(100)
    for i in range(5):
        history[i]=history[i].model_copy(update={"group":"private-small-group"})
    report=evaluate_calibration(history,report_id="report-1",as_of=NOW,authorize=authorized)
    assert report.suppressed_group_count==1 and len(report.groups)==1
    assert "private-small-group" not in report.model_dump_json()


def test_duplicate_evidence_is_not_extra_sample_and_conflict_is_rejected():
    history=observations(20)
    report=evaluate_calibration(history+history,report_id="report-1",as_of=NOW,authorize=authorized)
    assert report.real_sample_count==20
    conflicting=history[0].model_copy(update={"predicted_passed":False})
    with pytest.raises(ValueError,match="conflict"):
        evaluate_calibration(history+[conflicting],report_id="report-1",as_of=NOW,authorize=authorized)


def test_sampling_is_seeded_private_internal_plan_and_covers_errors():
    history=observations(100,errors=10)
    first=human_sampling_plan(history,seed=20261006,sample_size=20,as_of=NOW,authorize=authorized)
    assert first==human_sampling_plan(list(reversed(history)),seed=20261006,sample_size=20,as_of=NOW,authorize=authorized)
    assert len(first.observation_refs)==20 and first.error_samples>=1
    assert not first.publicly_publishable and first.seed==20261006
    assert "private:" not in first.model_dump_json()


def test_promotion_requires_real_sample_review_and_verified_authority():
    report=evaluate_calibration(observations(),report_id="report-1",as_of=NOW,authorize=authorized)
    no_review=promotion_gate(candidate(),report,review=None,verify_review=lambda review:False)
    assert no_review.status=="manual_review_required" and not no_review.automatic_promotion_enabled
    forged=promotion_gate(candidate(),report,review=reviewed(),verify_review=lambda review:False)
    assert forged.status=="blocked"
    ready=promotion_gate(candidate(),report,review=reviewed(),verify_review=lambda review:True)
    assert ready.status=="candidate_ready_for_manual_activation"
    assert ready.automatic_promotion_enabled is False and ready.changes_applied is False


def test_synthetic_cannot_pass_promotion_even_with_approval():
    report=evaluate_calibration(observations(100,source_kind="synthetic_ai_generated"),report_id="report-1",as_of=NOW,authorize=authorized)
    result=promotion_gate(candidate(),report,review=reviewed(),verify_review=lambda review:True)
    assert result.status=="blocked" and "insufficient_real_independent_samples" in result.reasons


def test_report_version_or_reference_mismatch_blocks_activation():
    report=evaluate_calibration(observations(),report_id="report-1",as_of=NOW,authorize=authorized)
    result=promotion_gate(candidate().model_copy(update={"evaluation_model_version":"wrong"}),report,
                          review=reviewed(),verify_review=lambda review:True)
    assert result.status=="blocked" and "evaluation_version_mismatch" in result.reasons


def test_poor_error_rate_never_passes_manual_candidate_gate():
    report=evaluate_calibration(observations(errors=50),report_id="report-1",as_of=NOW,authorize=authorized)
    result=promotion_gate(candidate(),report,review=reviewed(),verify_review=lambda review:True)
    assert result.status=="blocked" and "error_upper_bound_exceeds_limit" in result.reasons


def test_repeated_attempts_from_one_learner_do_not_create_independent_samples():
    report=evaluate_calibration(observations(100,learner_ref="same-learner"),report_id="report-1",as_of=NOW,authorize=authorized)
    assert report.real_sample_count==1 and report.independent_learner_count==1
    assert report.rejected_counts["same_learner_cluster"]==99


def test_cached_report_requires_current_authorization_recheck_at_promotion():
    report=evaluate_calibration(observations(),report_id="report-1",as_of=NOW,authorize=authorized)
    result=promotion_gate(candidate(),report,review=reviewed(),verify_review=lambda review:True,
                          verify_report=lambda report:False)
    assert result.status=="blocked"
    assert "report_provenance_or_current_research_authorization_unverified" in result.reasons
    default=_promotion_gate(candidate(),report,review=reviewed(),verify_review=lambda review:True)
    assert default.status=="blocked"
