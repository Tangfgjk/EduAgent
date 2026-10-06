"""Behavioral checks for versioned evidence-driven learning ports."""
from datetime import datetime, timedelta, timezone

import pytest

from app.learning.schema import AssessmentProfile, LearningEvidence, MasteryState, RetentionState
from app.learning.mastery_port import update_mastery
from app.learning.gates import evaluate_gate
from app.learning.review_port import retrievability, review_task, update_retention
from app.learning.replay import replay


NOW = datetime(2026, 10, 6, 5, tzinfo=timezone.utc)


def evidence(number=1, **overrides):
    values = dict(evidence_id=f"e{number}", learner_id="learner", session_id="session",
                  kc_refs=["MATH.EQUATIONS"], attempt_id=f"a{number}", artifact_ref="artifact",
                  verdict_ref=f"v{number}", verdict_status="passed", verifier_version="symbolic-v1",
                  confidence=1, occurred_at=NOW + timedelta(days=number - 1), event_seq=number,
                  consent_scope=["teaching"], consent_version="1", authorization_source="learner",
                  assessment_id=f"task{number}",assessment_kind="review", score=1)
    values.update(overrides)
    return LearningEvidence(**values)


@pytest.mark.parametrize("status", ["partial", "unverifiable"])
def test_nonbinary_results_skip_both_consumers(status):
    m = MasteryState(learner_id="learner", kc_id="MATH.EQUATIONS")
    r = RetentionState(learner_id="learner", kc_id="MATH.EQUATIONS")
    assert update_mastery(m, evidence(verdict_status=status)) == (m, status)
    assert update_retention(r, evidence(verdict_status=status)) == (r, status)


def test_mastery_has_bounds_monotone_confidence_and_idempotence():
    state = MasteryState(learner_id="learner", kc_id="MATH.EQUATIONS")
    for i in range(1, 12):
        updated, skipped = update_mastery(state, evidence(i))
        assert skipped is None
        assert 0 <= state.p_mastery < updated.p_mastery <= 1
        assert state.confidence < updated.confidence <= 1
        state = updated
    assert update_mastery(state, evidence(11)) == (state, "already_consumed")
    failed, _ = update_mastery(state, evidence(12, verdict_status="failed", score=0))
    assert failed.p_mastery < state.p_mastery


@pytest.mark.parametrize("override", [dict(hint_level=1), dict(assistance_mode="hint"),
                                       dict(answer_exposed=True), dict(assistance_mode="answer")])
def test_assisted_evidence_cannot_imply_independent_mastery_or_recall(override):
    m = MasteryState(learner_id="learner", kc_id="MATH.EQUATIONS")
    r = RetentionState(learner_id="learner", kc_id="MATH.EQUATIONS")
    assert update_mastery(m, evidence(**override))[0] == m
    assert update_retention(r, evidence(**override))[0] == r


def test_consumers_are_independent_and_research_does_not_authorize_teaching():
    m = MasteryState(learner_id="learner", kc_id="MATH.EQUATIONS")
    r = RetentionState(learner_id="learner", kc_id="MATH.EQUATIONS")
    practice = evidence(assessment_kind="practice")
    assert update_mastery(m, practice)[1] is None
    assert update_retention(r, practice) == (r, "not_recall_assessment")
    unauth = evidence(consent_scope=["research"])
    assert update_mastery(m, unauth) == (m, "teaching_not_authorized")
    assert update_retention(r, unauth) == (r, "teaching_not_authorized")


def test_cross_learner_or_kc_isolation():
    state = MasteryState(learner_id="another", kc_id="MATH.EQUATIONS")
    assert update_mastery(state, evidence()) == (state, "learner_mismatch")
    state = MasteryState(learner_id="learner", kc_id="MATH.OTHER")
    assert update_mastery(state, evidence()) == (state, "kc_mismatch")


def test_four_gate_statuses_and_provenance():
    profile = AssessmentProfile()
    initial = MasteryState(learner_id="learner", kc_id="MATH.EQUATIONS", p_mastery=.99)
    assert evaluate_gate(initial, [], profile).status == "insufficient_evidence"
    state = MasteryState(learner_id="learner", kc_id="MATH.EQUATIONS")
    history = []
    for i in range(1, 7):
        history.append(evidence(i))
        state, _ = update_mastery(state, history[-1])
    gate = evaluate_gate(state, history, profile)
    assert gate.status == "mastered"
    assert gate.evidence_refs == state.evidence_refs
    assert gate.assessment_profile_ref == "quantitative-default@1.0"
    uncertain = state.model_copy(update={"confidence": 0})
    assert evaluate_gate(uncertain, history, profile).status == "uncertain"
    failed = state.model_copy(update={"p_mastery": .2})
    assert evaluate_gate(failed, history, profile).status == "not_mastered"
    assert evaluate_gate(state, history[:-1], profile).status == "uncertain"


@pytest.mark.parametrize("mode", ["qualitative", "hybrid"])
def test_quiz_alone_and_single_llm_vote_cannot_pass_qualitative_gate(mode):
    profile = AssessmentProfile(mode=mode, min_evidence=2, min_confidence=.3, min_qualitative=2)
    history = [evidence(i) for i in range(1, 7)]
    state = MasteryState(learner_id="learner", kc_id="MATH.EQUATIONS")
    for item in history:
        state, _ = update_mastery(state, item)
    assert evaluate_gate(state, history, profile).status == "insufficient_evidence"
    history[0] = history[0].model_copy(update={"qualitative_pass": True})
    assert evaluate_gate(state, history, profile).status == "insufficient_evidence"
    history[1] = history[1].model_copy(update={"qualitative_pass": True})
    assert evaluate_gate(state, history, profile).status == "mastered"
    history[1] = history[1].model_copy(update={"qualitative_pass": False})
    assert evaluate_gate(state, history, profile).status == "not_mastered"


def test_missing_confidence_is_uncertain_even_after_many_passes():
    history = [evidence(i, confidence=None) for i in range(1, 8)]
    state = MasteryState(learner_id="learner", kc_id="MATH.EQUATIONS")
    for item in history:
        state, _ = update_mastery(state, item)
    assert state.p_mastery > .9
    assert evaluate_gate(state, history, AssessmentProfile()).status == "uncertain"


def test_retention_success_failure_time_and_review_task():
    state = RetentionState(learner_id="learner", kc_id="MATH.EQUATIONS")
    assert review_task(state, NOW) is None
    passed, skipped = update_retention(state, evidence())
    assert skipped is None and passed.stability > state.stability
    assert retrievability(passed, NOW) == 1
    later = NOW + timedelta(days=10)
    assert 0 < retrievability(passed, later) < 1
    task = review_task(passed, later)
    assert task is not None and task.forgetting_risk == 1 - task.retrievability
    assert task.as_of == later and task.evidence_refs == ["e1"]
    assert review_task(passed, later) == task
    failed, _ = update_retention(passed, evidence(2, verdict_status="failed", score=0))
    assert failed.lapse_count == 1 and failed.stability < passed.stability
    assert failed.next_review_at - failed.last_review_at < passed.next_review_at - passed.last_review_at
    assert update_retention(failed, evidence(1, evidence_id="late"))[1] == "late_evidence_requires_replay"
    with pytest.raises(ValueError):
        review_task(passed, NOW.replace(tzinfo=None))


def test_replay_is_stable_ordered_isolated_and_applies_corrections():
    history = [evidence(1), evidence(2, occurred_at=NOW), evidence(3, learner_id="other"),
               evidence(4, supersedes="e1", verdict_status="failed", score=0)]
    result = replay(history, "learner", NOW + timedelta(days=9))
    assert result == replay(list(reversed(history)), "learner", NOW + timedelta(days=9))
    assert result.evidence_refs == ["e2", "e4"]
    assert result.mastery["MATH.EQUATIONS"].evidence_refs == ["e2", "e4"]
    assert result.mastery["MATH.EQUATIONS"].effective_evidence_count == 2
    assert result.retention["MATH.EQUATIONS"].lapse_count == 1
    early = replay(history, "learner", NOW)
    assert early.evidence_refs == ["e1", "e2"]


def test_replay_rejects_unordered_or_conflicting_event_sets():
    with pytest.raises(ValueError):
        replay([evidence(event_seq=None)], "learner", NOW)
    with pytest.raises(ValueError):
        replay([evidence(1), evidence(2, event_seq=1, occurred_at=NOW)], "learner", NOW)
    with pytest.raises(ValueError):
        replay([evidence(1), evidence(2, evidence_id="e1", occurred_at=NOW)], "learner", NOW)


def test_unsupported_model_versions_do_not_silently_run_default_algorithm():
    state = MasteryState(learner_id="learner", kc_id="MATH.EQUATIONS", algorithm_version="future")
    with pytest.raises(ValueError):
        update_mastery(state, evidence())
    state = RetentionState(learner_id="learner", kc_id="MATH.EQUATIONS", scheduler_version="future")
    with pytest.raises(ValueError):
        update_retention(state, evidence())


def test_replay_consumes_late_recall_without_mutating_original_fact():
    original = evidence(1, occurred_at=NOW + timedelta(days=3))
    late = evidence(2, occurred_at=NOW)
    result = replay([original, late], "learner", NOW + timedelta(days=4))
    assert late.occurred_at == NOW
    assert result.retention["MATH.EQUATIONS"].evidence_refs == ["e1", "e2"]
    assert result.retention["MATH.EQUATIONS"].last_review_at == original.occurred_at
    assert result.skip_reasons["e2"]["retention:MATH.EQUATIONS"] == "late_evidence_replayed_at_effective_time"


def test_replay_as_of_is_calculation_time_and_kc_mapping_requires_migration():
    first = evidence(1)
    corrected = evidence(2, supersedes="e1",
                         occurred_at=NOW, ingested_at=NOW + timedelta(days=5))
    historical = replay([first, corrected], "learner", NOW + timedelta(days=1))
    assert historical.mastery["MATH.EQUATIONS"].evidence_refs == ["e2"]
    with pytest.raises(ValueError):
        replay([first, corrected.model_copy(update={"kc_refs": ["MATH.RATIOS"]})], "learner", NOW)


def test_gate_threshold_boundary_and_duplicate_evidence_is_not_extra_vote():
    history = [evidence(i, qualitative_pass=True) for i in range(1, 7)]
    state = MasteryState(learner_id="learner", kc_id="MATH.EQUATIONS")
    for item in history:
        state, _ = update_mastery(state, item)
    boundary = AssessmentProfile(mastery_threshold=state.p_mastery, min_confidence=state.confidence)
    assert evaluate_gate(state, history, boundary).status == "mastered"
    higher = boundary.model_copy(update={"mastery_threshold": state.p_mastery + .001})
    assert evaluate_gate(state, history, higher).status == "not_mastered"
    assert evaluate_gate(state, [*history, *history], boundary).effective_evidence_count == 6
    inconsistent = history[0].model_copy(update={"confidence": .1})
    assert evaluate_gate(state, [*history, inconsistent], boundary).status == "uncertain"


@pytest.mark.parametrize("kind", ["pre", "post", "transfer", "review"])
def test_versioned_recall_eligibility_accepts_all_independent_assessments(kind):
    state = RetentionState(learner_id="learner", kc_id="MATH.EQUATIONS")
    updated, reason = update_retention(state, evidence(assessment_kind=kind))
    assert reason is None and updated.state_version == 1
