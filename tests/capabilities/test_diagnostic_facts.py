"""Diagnostic facts do not manufacture independence, grades or mastery."""

import copy

from pydantic import ValidationError
import pytest

from deeptutor.learning.diagnosis import load_project_facts, project_facts
from deeptutor.learning.diagnosis_models import DiagnosticContext, DiagnosticObservation
from deeptutor.learning.models import (
    KnowledgePoint,
    LearnerProfile,
    LearningEvidence,
    LearningModule,
    LearningProgress,
    PendingQuestion,
    QuizAttempt,
)
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore


def state():
    return LearningProgress(
        book_id="p",
        modules=[
            LearningModule(
                id="m",
                name="Chapter",
                order=0,
                knowledge_points=[
                    KnowledgePoint(id="k", name="Equality", module_id="m", type="concept")
                ],
            )
        ],
    )


def test_empty_and_self_report_never_mean_weak_or_mastered():
    p = state()
    p.learner_profile = LearnerProfile(prior_knowledge="I know everything")
    before = copy.deepcopy(p)
    facts = project_facts(p)
    assert not facts.observations
    assert facts.objectives[0]["measurement_status"] == "unmeasured"
    assert facts.self_reports[0]["verification"] == "self_report_not_assessment"
    assert p == before


def test_legacy_unique_receipt_is_one_observation_with_unknown_assistance():
    p = state()
    p.quiz_attempts = [
        QuizAttempt(question_id="q", knowledge_point_id="k", is_correct=True, user_answer="0")
    ]
    p.learning_evidence = [
        LearningEvidence(question_id="q", knowledge_point_id="k", result="correct")
    ]
    facts = project_facts(p)
    assert len(facts.observations) == 1
    row = facts.observations[0]
    assert len(row.source_refs) == 2 and row.answer == "0"
    assert row.assistance == "unknown"
    assert row.score is None and row.max_score is None
    assert facts.objectives[0]["independent_count"] == 0


def test_ambiguous_legacy_retries_are_not_silently_paired_or_counted():
    p = state()
    for _ in range(2):
        p.quiz_attempts.append(
            QuizAttempt(question_id="q", knowledge_point_id="k", is_correct=True)
        )
        p.learning_evidence.append(
            LearningEvidence(question_id="q", knowledge_point_id="k", result="correct")
        )
    facts = project_facts(p)
    assert all(o.validity == "conflicting" for o in facts.observations)
    assert facts.objectives[0]["observation_count"] == 0


def test_voided_source_remains_auditable_but_not_counted():
    p = state()
    p.quiz_attempts = [
        QuizAttempt(question_id="q", knowledge_point_id="k", is_correct=True, voided=True)
    ]
    p.learning_evidence = [
        LearningEvidence(question_id="q", knowledge_point_id="k", result="correct")
    ]
    facts = project_facts(p)
    assert facts.observations[0].validity == "voided"
    assert facts.objectives[0]["observation_count"] == 0


@pytest.mark.parametrize("helped", [False, True])
def test_new_native_receipt_links_task_and_assistance_durably(tmp_path, helped):
    p = state()
    service = LearningService(LearningStore(tmp_path))
    question = PendingQuestion(
        question_id="q", knowledge_point_id="k", prompt="What is 1+1?", expected_answer="2"
    )
    service._apply_grade(
        p,
        question_id="q",
        knowledge_point_id="k",
        module_id="m",
        user_answer="2",
        expected_answer="2",
        question_type="short",
        session_id="s",
        turn_id="t",
        diagnostic_question=question,
        assistance_used=helped,
        scaffold_level=1 if helped else 0,
    )
    service.save(p)
    facts = load_project_facts(service._store, "p")
    assert len(facts.observations) == 1
    row = facts.observations[0]
    assert row.assistance == ("assisted" if helped else "independent")
    assert row.session_id == "s" and row.turn_id == "t"
    assert row.context.task_prompt == question.prompt
    assert len(row.context.task_version) == 64
    assert "expected_answer" not in row.model_dump_json()
    assert project_facts(p) == project_facts(p)


def test_standalone_grade_does_not_claim_verified_independence(tmp_path):
    p = state()
    service = LearningService(LearningStore(tmp_path))
    service._apply_grade(
        p,
        question_id="q",
        knowledge_point_id="k",
        module_id="m",
        user_answer="2",
        expected_answer="2",
        question_type="short",
    )
    assert project_facts(p).observations[0].assistance == "unknown"


def test_profile_updates_have_immutable_source_events(tmp_path):
    service = LearningService(LearningStore(tmp_path))
    service.save(state())
    service.record_learner_profile(
        "p",
        fields={"prior_knowledge": "I know equality"},
        learner_message="I know equality",
        session_id="s",
        turn_id="t",
    )
    service.record_learner_profile(
        "p",
        fields={"prior_knowledge": "I need help"},
        learner_message="I need help",
        session_id="s",
        turn_id="u",
    )
    facts = load_project_facts(service._store, "p")
    reports = [r for r in facts.self_reports if r["kind"] == "profile_update"]
    assert [r["values"]["prior_knowledge"] for r in reports] == ["I know equality", "I need help"]
    assert reports[0]["turn_id"] == "t"
    assert not facts.observations


def test_inconsistent_linked_result_is_a_conflict():
    p = state()
    p.quiz_attempts = [
        QuizAttempt(
            question_id="q", knowledge_point_id="k", is_correct=False, diagnostic_evidence_id="e"
        )
    ]
    p.learning_evidence = [
        LearningEvidence(evidence_id="e", question_id="q", knowledge_point_id="k", result="correct")
    ]
    assert project_facts(p).observations[0].validity == "conflicting"


def test_strict_context_and_nonfinite_scores_rejected():
    with pytest.raises(ValidationError):
        DiagnosticContext(assistance="independent", probability=0.9)
    for value in (float("nan"), float("inf"), -1):
        with pytest.raises(ValidationError):
            DiagnosticObservation(
                observation_id="o",
                objective_id="k",
                origin="legacy_record",
                source_refs=[],
                occurred_at=1,
                ability="recognition",
                result="correct",
                score=value,
            )


def test_missing_project_is_not_created(tmp_path):
    store = LearningStore(tmp_path)
    with pytest.raises(LookupError):
        load_project_facts(store, "missing")
    assert not store.exists("missing")


def test_repeated_question_uses_explicit_links_instead_of_collapsing_attempts():
    p = state()
    for i in range(2):
        p.quiz_attempts.append(
            QuizAttempt(
                question_id="q",
                knowledge_point_id="k",
                is_correct=True,
                diagnostic_evidence_id=f"e{i}",
                user_answer=f"answer {i}",
            )
        )
        p.learning_evidence.append(
            LearningEvidence(
                evidence_id=f"e{i}",
                question_id="q",
                knowledge_point_id="k",
                result="correct",
                diagnostic_context=DiagnosticContext(),
            )
        )
    facts = project_facts(p)
    assert len(facts.observations) == 2
    assert facts.objectives[0]["observation_count"] == 2
    assert len({o.observation_id for o in facts.observations}) == 2


def test_score_zero_is_preserved_and_out_of_range_is_rejected():
    kwargs = dict(
        observation_id="o",
        objective_id="k",
        origin="legacy_record",
        source_refs=[],
        occurred_at=1,
        ability="recognition",
        result="incorrect",
    )
    assert DiagnosticObservation(**kwargs, score=0, max_score=10).score == 0
    assert DiagnosticObservation(**kwargs).score is None
    with pytest.raises(ValidationError):
        DiagnosticObservation(**kwargs, score=11, max_score=10)
