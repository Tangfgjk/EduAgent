"""Unified facts drive teaching without forced testing or fabricated mastery."""

import copy

import pytest

from deeptutor.learning.diagnosis_models import DiagnosticContext
from deeptutor.learning.models import (
    KnowledgePoint,
    LearnerProfile,
    LearningEvidence,
    LearningModule,
    LearningProgress,
    PendingQuestion,
    QuizAttempt,
)
from deeptutor.learning.policy import is_assessed_mastered, next_objective
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore
from deeptutor.learning.wenjin import objective_evidence, teaching_decision


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
                    KnowledgePoint(id="k", name="Elimination", type="procedure", module_id="m")
                ],
            )
        ],
    )


def add(p, kind="application", result="correct", assistance="independent", timestamp=1):
    sid = str(len(p.quiz_attempts))
    p.quiz_attempts.append(
        QuizAttempt(
            question_id="q" + sid,
            knowledge_point_id="k",
            is_correct=result == "correct",
            result=result,
            independent=assistance == "independent",
            timestamp=timestamp,
            evidence_kind=kind,
            diagnostic_evidence_id=sid,
        )
    )
    p.learning_evidence.append(
        LearningEvidence(
            evidence_id=sid,
            question_id="q" + sid,
            knowledge_point_id="k",
            result=result,
            timestamp=timestamp,
            evidence_kind=kind,
            diagnostic_context=DiagnosticContext(
                assistance=assistance, objective_name="Elimination"
            ),
        )
    )


def test_no_history_unknown_not_weak_and_no_mutation():
    p = state()
    before = copy.deepcopy(p)
    d = teaching_decision(p, p.modules[0].knowledge_points[0])
    assert d["reason_code"] == "not_yet_observed" and d["action"] == "probe"
    assert d["choices"] == ["teach_first", "probe_first", "pause"]
    assert p == before


def test_self_report_and_legacy_defaults_do_not_grant_independence():
    p = state()
    p.learner_profile = LearnerProfile(prior_knowledge="I know all this")
    p.quiz_attempts = [QuizAttempt(question_id="q", knowledge_point_id="k", is_correct=True)]
    kp = p.modules[0].knowledge_points[0]
    assert teaching_decision(p, kp)["action"] == "verify_independence"
    assert not objective_evidence(p, kp)["achieved"]
    assert not is_assessed_mastered(p, kp)


def test_teach_first_respects_preference_and_does_not_pass_gate():
    p = state()
    add(p, result="incorrect")
    p.learner_profile = LearnerProfile(teaching_strategy="teach_first")
    assert teaching_decision(p, p.modules[0].knowledge_points[0])["action"] == "teach"
    assert next_objective(p).action == "teach"
    assert not is_assessed_mastered(p, p.modules[0].knowledge_points[0])


def test_independent_open_abilities_advance_real_policy():
    p = state()
    kp = p.modules[0].knowledge_points[0]
    add(p)
    assert teaching_decision(p, kp)["action"] == "check_transfer"
    add(p, kind="transfer", timestamp=2)
    assert is_assessed_mastered(p, kp)
    assert next_objective(p).action == "complete"


def test_error_asks_reason_then_minimal_hint_then_fades_on_new_task():
    p = state()
    kp = p.modules[0].knowledge_points[0]
    add(p, result="incorrect")
    assert teaching_decision(p, kp)["action"] == "ask_reason"
    add(p, result="partial", timestamp=2)
    assert teaching_decision(p, kp)["action"] == "offer_hint"
    add(p, assistance="assisted", timestamp=3)
    assert teaching_decision(p, kp)["scaffold"] == "fade_one_step"
    assert not is_assessed_mastered(p, kp)


def test_invalid_sources_are_excluded_and_explained():
    p = state()
    add(p)
    p.quiz_attempts[0].voided = True
    d = teaching_decision(p, p.modules[0].knowledge_points[0])
    assert d["action"] == "verify_record" and d["evidence"]["excluded_count"] == 1


def test_newer_assisted_failure_requires_recheck_not_old_pass():
    p = state()
    add(p)
    add(p, kind="transfer", timestamp=2)
    assert is_assessed_mastered(p, p.modules[0].knowledge_points[0])
    add(p, kind="transfer", result="incorrect", assistance="assisted", timestamp=3)
    assert not is_assessed_mastered(p, p.modules[0].knowledge_points[0])


def test_question_pending_is_not_replaced_by_diagnostic_recommendation():
    p = state()
    p.pending_question = PendingQuestion(
        question_id="pending", knowledge_point_id="k", prompt="Solve it", expected_answer="2"
    )
    d = next_objective(p)
    assert d.action == "answer_pending" and d.pending_prompt == "Solve it"


def test_prerequisite_is_optional_and_only_from_explicit_graph():
    p = state()
    add(p, result="incorrect")
    prior = p.modules[0].knowledge_points[0]
    target = KnowledgePoint(
        id="next", name="Applications", module_id="m", type="procedure", prerequisite_ids=["k"]
    )
    p.modules[0].knowledge_points.append(target)
    d = teaching_decision(p, target)
    assert d["prerequisite_suggestions"][0]["id"] == prior.id
    assert d["action"] == "probe"  # Never silently redirect away from the requested objective.


def test_rsi_does_not_count_unknown_legacy_success():
    from deeptutor.learning.evolution import metrics

    p = state()
    p.quiz_attempts = [
        QuizAttempt(
            question_id="q", knowledge_point_id="k", is_correct=True, evidence_kind="application"
        )
    ]
    assert metrics(p, "baseline")["independent_n"] == 0


def test_native_grade_flows_into_decision(tmp_path):
    p = state()
    service = LearningService(LearningStore(tmp_path))
    kp = p.modules[0].knowledge_points[0]
    q = PendingQuestion(
        question_id="q",
        knowledge_point_id="k",
        prompt="Apply method",
        expected_answer="2",
        evidence_kind="application",
    )
    service._apply_grade(
        p,
        question_id="q",
        knowledge_point_id="k",
        module_id="m",
        user_answer="2",
        expected_answer="2",
        question_type="short",
        diagnostic_question=q,
        evidence_kind="application",
        session_id="s",
        turn_id="t",
    )
    service.save(p)
    restored = service.store.load("p")
    assert teaching_decision(restored, kp)["action"] == "check_transfer"
    assert teaching_decision(restored, kp)["source_revision"] == restored.version


def test_replaced_invalid_task_does_not_trap_next_ability():
    p = state()
    add(p)
    p.quiz_attempts[0].voided = True
    add(p, timestamp=2)
    d = teaching_decision(p, p.modules[0].knowledge_points[0])
    assert d["action"] == "check_transfer"


@pytest.mark.asyncio
async def test_status_tool_exposes_same_normalized_decision(tmp_path, monkeypatch):
    import json

    from deeptutor.capabilities.mastery import tools

    p = state()
    p.quiz_attempts = [QuizAttempt(question_id="q", knowledge_point_id="k", is_correct=True)]
    service = LearningService(LearningStore(tmp_path))
    service.save(p)
    monkeypatch.setattr(tools, "_new_service", lambda: service)
    result = await tools.MasteryStatusTool().execute(_mastery_path_id="p", _session_id="test")
    assert result.success, result.content
    status = json.loads(result.content)
    assert status["wenjin"]["decision"]["action"] == "verify_independence"
    assert status["next"]["action"] == "probe"
    assert status["learning_stages"]["k"] != "independently_demonstrated"
