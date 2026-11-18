"""Replay, task isolation and tutor-tool integration for experimental BKT."""

import copy
import json

import pytest

from deeptutor.learning.bkt import BKTParameters, diagnostic_estimate, update
from deeptutor.learning.diagnosis import assessment_context
from deeptutor.learning.models import (
    KnowledgePoint,
    LearningEvidence,
    LearningModule,
    LearningProgress,
    PendingQuestion,
)
from deeptutor.learning.policy import is_assessed_mastered
from deeptutor.learning.wenjin import teaching_decision


def state():
    return LearningProgress(
        book_id="p",
        wenjin_enabled=True,
        modules=[
            LearningModule(
                id="m",
                name="Equations",
                order=0,
                knowledge_points=[
                    KnowledgePoint(
                        id="k",
                        name="Elimination",
                        type="procedure",
                        module_id="m",
                    )
                ],
            )
        ],
    )


def add(p, *, result="correct", ability="application", assistance=False, timestamp=1):
    index = len(p.learning_evidence)
    q = PendingQuestion(
        question_id=f"q{index}",
        knowledge_point_id="k",
        prompt="Solve 2x=4",
        question_type="short",
        expected_answer="2",
    )
    ctx = assessment_context(p, q, "2", "short", assistance_used=assistance)
    p.learning_evidence.append(
        LearningEvidence(
            evidence_id=f"e{index}",
            question_id=q.question_id,
            knowledge_point_id="k",
            diagnostic_context=ctx,
            evidence_kind=ability,
            result=result,
            timestamp=timestamp,
            learner_answer="2" if result == "correct" else "3",
        )
    )


def estimate(p):
    return diagnostic_estimate(p, p.modules[0].knowledge_points[0])


def test_numeric_update_uses_posterior_then_learning():
    # P(known|correct)=.18/(.18+.16)=9/17; learning adds (1-9/17)*.1.
    assert update(0.2, True, BKTParameters()) == pytest.approx(49 / 85)
    assert update(0.2, False, BKTParameters()) == pytest.approx(7 / 55)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"guess": 0},
        {"slip": 0},
        {"prior": float("nan")},
        {"learn": -1},
        {"prior": True},
        {"guess": 0.9, "slip": 0.2},
    ],
)
def test_bad_parameters_rejected(kwargs):
    with pytest.raises(ValueError):
        BKTParameters(**kwargs)


def test_no_observation_does_not_expose_prior_as_measurement():
    e = estimate(state())
    assert e["measurement_status"] == "unmeasured"
    assert e["streams"] == [] and e["n_eligible"] == 0
    assert "p_known" not in e


def test_correct_and_wrong_both_enter_with_same_contract():
    p = state()
    add(p)
    first = estimate(p)["streams"][0]["p_known"]
    add(p, result="incorrect", timestamp=2)
    e = estimate(p)
    assert e["n_eligible"] == 2
    assert e["streams"][0]["p_known"] < first
    assert e["streams"][0]["p_correct_next"] != e["streams"][0]["p_known"]


@pytest.mark.parametrize(
    "change,reason",
    [
        ("assisted", "assisted_or_unknown"),
        ("unknown", "assisted_or_unknown"),
        ("partial", "non_binary_result"),
        ("empty", "missing_response"),
        ("legacy", "assisted_or_unknown"),
        ("scope", "missing_or_changed_objective_scope"),
    ],
)
def test_ineligible_records_are_visible_but_not_used(change, reason):
    p = state()
    add(p)
    row = p.learning_evidence[0]
    if change in {"assisted", "unknown"}:
        row.diagnostic_context.assistance = change
    elif change == "partial":
        row.result = "partial"
    elif change == "empty":
        row.learner_answer = ""
    elif change == "legacy":
        row.diagnostic_context = None
    else:
        row.diagnostic_context.objective_scope = "old-scope"
    e = estimate(p)
    assert e["n_eligible"] == 0 and e["excluded_counts"][reason] == 1


def test_replay_is_pure_ordered_and_retracts_corrected_evidence():
    p = state()
    add(p, timestamp=2)
    add(p, result="incorrect", timestamp=1)
    before = copy.deepcopy(p)
    e = estimate(p)
    assert estimate(p) == e and p == before
    p.learning_evidence.reverse()
    replayed = estimate(p)
    assert replayed["streams"][0]["p_known"] == e["streams"][0]["p_known"]
    assert replayed["n_eligible"] == e["n_eligible"]
    p.learning_evidence[0].diagnostic_context.assistance = "assisted"
    assert estimate(p)["n_eligible"] == 1


def test_ability_families_and_objective_edits_never_pool():
    p = state()
    add(p)
    add(p, ability="transfer", timestamp=2)
    assert len(estimate(p)["streams"]) == 2
    p.modules[0].knowledge_points[0].name = "A revised objective"
    assert estimate(p)["n_eligible"] == 0


def test_duplicate_and_repeated_tasks_do_not_add_trials():
    p = state()
    add(p)
    p.learning_evidence.append(copy.deepcopy(p.learning_evidence[0]))
    assert estimate(p)["n_eligible"] == 0
    p = state()
    add(p)
    add(p, timestamp=2)
    p.learning_evidence[1].question_id = p.learning_evidence[0].question_id
    assert estimate(p)["n_eligible"] == 0


def test_high_estimate_does_not_clear_missing_ability_or_command_fading():
    p = state()
    for t in range(12):
        add(p, timestamp=t)
    kp = p.modules[0].knowledge_points[0]
    d = teaching_decision(p, kp)
    assert d["diagnostic_estimate"]["streams"][0]["p_known"] > 0.99
    assert not is_assessed_mastered(p, kp)  # Transfer is still unobserved.
    assert d["recommendation_only"] is True
    assert d["decision_authority"] == "tutor_model_with_evidence_constraints"
    assert "transfer" in d["evidence"]["missing"]


@pytest.mark.asyncio
async def test_actual_status_tool_delivers_diagnostic_context(tmp_path, monkeypatch):
    from deeptutor.capabilities.mastery import tools
    from deeptutor.learning.service import LearningService
    from deeptutor.learning.storage import LearningStore

    store = LearningStore(tmp_path / "learning")
    p = state()
    add(p)
    store.save(p)
    monkeypatch.setattr(tools, "_new_service", lambda: LearningService(store))
    result = await tools.MasteryStatusTool().execute(_mastery_path_id="p")
    assert result.success
    d = json.loads(result.content)["wenjin"]["decision"]
    assert d["diagnostic_estimate"]["n_eligible"] == 1
    assert d["diagnostic_estimate"]["calibration_status"] == "uncalibrated"
    assert d["recommendation_only"]


@pytest.mark.parametrize(
    "sequence", [[True], [False], [True, False, True, True], [False] * 5, [True] * 12]
)
def test_reference_pybkt_matches_every_prediction(sequence):
    pytest.importorskip("pyBKT")
    import numpy as np
    from pyBKT.fit import predict_onestep

    p = BKTParameters()
    n = len(sequence) + 1
    model = {
        "prior": p.prior,
        "learns": np.array([p.learn]),
        "forgets": np.array([0.0]),
        "guesses": np.array([p.guess]),
        "slips": np.array([p.slip]),
    }
    data = {
        "data": np.array([[2 if c else 1 for c in sequence] + [2]], dtype=np.int32),
        "resources": np.ones(n, dtype=np.int64),
        "starts": np.array([1]),
        "lengths": np.array([n]),
    }
    predicted, states = predict_onestep.run(model, data, parallel=False)
    known = p.prior
    for index, correct in enumerate(sequence):
        assert states[1, index] == pytest.approx(known)
        known = update(known, correct, p)
    assert states[1, -1] == pytest.approx(known)
    assert predicted[-1] == pytest.approx(known * (1 - p.slip) + (1 - known) * p.guess)
