from datetime import datetime, timedelta, timezone

import pytest

from pathlib import Path

from app.learning.assets import AssetCatalog, VersionedRef
from app.learning.metrics import PerformanceObservation, observation_from_evidence, performance_gain
from app.learning.schema import LearningEvidence


BASE = datetime(2026, 10, 6, 8, tzinfo=timezone.utc)
KC = VersionedRef(asset_id="math", version="1.0.0")


def row(evidence_id, kind, score, **kwargs):
    return PerformanceObservation(
        evidence_id=evidence_id, learner_id="s", kc_ref=KC,
        assessment_ref=VersionedRef(asset_id=evidence_id, version="1.0.0"),
        rubric_ref=VersionedRef(asset_id="rubric", version="1.0.0"),
        comparison_group="linear-equation", difficulty_band="easy", assessment_kind=kind,
        score=score, occurred_at=BASE + timedelta(days=kind != "pretest"), **kwargs)


def history():
    return (row("pre1", "pretest", 0.2), row("pre2", "pretest", 0.4),
            row("post1", "posttest", 0.6), row("post2", "posttest", 0.8))


def measure(rows, **kwargs):
    return performance_gain(tuple(rows), learner_id="s", kc_ref=KC, as_of=BASE + timedelta(days=2), **kwargs)


def test_gain_is_comparable_unassisted_and_explicitly_descriptive():
    result = measure(history())
    assert result.status == "comparable"
    assert result.gain == pytest.approx(0.4)
    assert result.pre_count == result.post_count == 2
    assert len(result.evidence_refs) == 4
    assert result.uncertainty_method == "descriptive_only_no_causal_or_statistical_claim"


@pytest.mark.parametrize("updates", [{"hint_level": 1}, {"answer_exposed": True},
                                      {"verified": False}, {"confidence": 0.2}, {"assistance_mode": "teacher"}])
def test_assisted_low_confidence_unverified_scores_do_not_enter_gain(updates):
    rows = list(history())
    rows[-1] = rows[-1].model_copy(update=updates)
    result = measure(rows)
    assert result.status == "insufficient_data"
    assert result.gain is None
    assert result.excluded_count == 1


@pytest.mark.parametrize("field,value", [("difficulty_band", "hard"), ("comparison_group", "different"),
                                         ("rubric_ref", VersionedRef(asset_id="rubric", version="2.0.0"))])
def test_mismatched_rubric_difficulty_or_form_is_not_comparable(field, value):
    rows = list(history())
    rows[2:] = [item.model_copy(update={field: value}) for item in rows[2:]]
    result = measure(rows)
    assert result.status == "incomparable"
    assert result.gain is None


def test_empty_duplicate_and_foreign_records_are_handled():
    assert measure(()).status == "insufficient_data"
    rows = history()
    assert measure(rows + rows) == measure(rows)
    assert measure(tuple(item.model_copy(update={"learner_id": "other"}) for item in rows)).pre_count == 0
    with pytest.raises(ValueError, match="conflicting"):
        measure(rows + (rows[0].model_copy(update={"score": 1}),))


def test_metrics_reject_time_overlap_repeated_items_and_future_evidence():
    rows = list(history())
    rows[2] = rows[2].model_copy(update={"occurred_at": BASE})
    assert measure(rows).reason == "pretest_window_must_precede_posttest"
    rows = list(history())
    rows[2] = rows[2].model_copy(update={"assessment_ref": rows[0].assessment_ref})
    assert measure(rows).reason == "repeated_assessment_not_independent_comparison"
    result = performance_gain(history(), learner_id="s", kc_ref=KC, as_of=BASE)
    assert result.status == "insufficient_data"


def test_transfer_and_delayed_measurements_are_separate_windows():
    rows = history()[:2] + (row("t1", "transfer", 0.5), row("t2", "transfer", 0.7))
    assert measure(rows).status == "insufficient_data"
    assert measure(rows, post_kind="transfer").gain == pytest.approx(0.3)


def test_multiple_valid_groups_require_explicit_selection():
    extra = tuple(item.model_copy(update={"evidence_id": "x-" + item.evidence_id,
                                          "assessment_ref": VersionedRef(asset_id="x-" + item.evidence_id, version="1"),
                                          "comparison_group": "second"}) for item in history())
    assert measure(history() + extra).status == "incomparable"
    assert measure(history() + extra, comparison_group="second").status == "comparable"


def test_repeated_attempts_on_one_item_do_not_inflate_sample_threshold():
    rows = list(history())
    rows[1] = rows[1].model_copy(update={"assessment_ref": rows[0].assessment_ref})
    assert measure(rows).status == "insufficient_data"


def test_evidence_projection_uses_known_versioned_assets_and_keeps_action_provenance():
    assets = AssetCatalog.load(Path(__file__).parents[1] / "seeds" / "learning_assets_v1.json")
    evidence = LearningEvidence(
        evidence_id="e", learner_id="s", session_id="session", kc_refs=["MATH.G7.EQ.SOLVE"],
        attempt_id="attempt", artifact_ref="artifact", verdict_ref="verdict", verdict_status="passed",
        verifier_version="symbolic-v1", confidence=0.9, occurred_at=BASE, consent_scope=["teaching"],
        consent_version="v1", authorization_source="learner", assessment_id="PRE-SOLVE-1",
        assessment_version="1.0.0", assessment_kind="pre", rubric_version="1.0.0",
        difficulty_band="easy", score=1, action_ref="a1", policy_version="p1")
    result = observation_from_evidence(evidence, assets)
    assert result.assessment_kind == "pretest"
    assert result.kc_ref == VersionedRef(asset_id="MATH.G7.EQ.SOLVE", version="1.0.0")
    assert result.action_id == "a1"
    assert observation_from_evidence(evidence.model_copy(update={"assessment_version": "missing"}), assets) is None
    assert observation_from_evidence(evidence.model_copy(update={"rubric_version": "missing"}), assets) is None
    assert observation_from_evidence(evidence.model_copy(update={"assessment_kind": "post"}), assets) is None
    assert observation_from_evidence(evidence.model_copy(update={"verifier_details": {"diagnostic_response": "guessed"}}), assets) is None
