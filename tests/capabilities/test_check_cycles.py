import time

import pytest

from deeptutor.learning import check_cycles as checks
from deeptutor.learning.diagnosis import assessment_context
from deeptutor.learning.models import (
    KnowledgePoint,
    LearningEvidence,
    LearningModule,
    LearningProgress,
    PendingQuestion,
    QuizAttempt,
)
from deeptutor.learning.storage import LearningStore


@pytest.fixture
def store(tmp_path):
    s = LearningStore(tmp_path / "learning")
    s.save(
        LearningProgress(
            book_id="p",
            name="核查验收",
            wenjin_enabled=True,
            modules=[
                LearningModule(
                    id="m",
                    name="方程",
                    order=0,
                    knowledge_points=[
                        KnowledgePoint(id="k", name="代入法", type="procedure", module_id="m")
                    ],
                )
            ],
        )
    )
    return s


def answer(s, qid, at, *, result="correct", assisted=False, kind="application"):
    def record(tx):
        q = PendingQuestion(
            question_id=qid,
            knowledge_point_id="k",
            module_id="m",
            prompt="new task " + qid,
            expected_answer="2",
            evidence_kind=kind,
        )
        checks.bind(tx, [q], session_id="session-" + qid)
        context = assessment_context(tx.progress, q, "2", "short", assistance_used=assisted)
        tx.progress.learning_evidence.append(
            LearningEvidence(
                evidence_id="ev-" + qid,
                question_id=qid,
                knowledge_point_id="k",
                evidence_kind=kind,
                result=result,
                timestamp=at,
                learner_answer="2",
                session_id="session-" + qid,
                feedback="具体反馈",
                hints_used=int(assisted),
                diagnostic_context=context,
            )
        )
        tx.progress.quiz_attempts.append(
            QuizAttempt(
                diagnostic_evidence_id="ev-" + qid,
                question_id=qid,
                knowledge_point_id="k",
                module_id="m",
                evidence_kind=kind,
                result=result,
                is_correct=result == "correct",
                independent=not assisted,
                timestamp=at,
                user_answer="2",
            )
        )
        tx.emit("test.answered", {})

    s.mutate("p", record)


def test_full_cycle_delays_transfer_and_keeps_diagnostic_source(store, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(checks.time, "time", lambda: clock[0])
    first = checks.start(store, "p", "k")
    assert first["phase"] == "initial"
    assert checks.start(store, "p", "k")["round_id"] == first["round_id"]
    answer(store, "q1", 1001)
    row = checks.snapshot(store, "p", now=1002)["objectives"][0]
    assert row["phase"] == "review" and row["status"] == "waiting"
    assert row["due_at"] >= 1001 + 86400
    with pytest.raises(ValueError):
        checks.start(store, "p", "k", now=row["due_at"] - 1)
    clock[0] = row["due_at"]
    assert checks.start(store, "p", "k")["phase"] == "review"
    answer(store, "q2", clock[0] + 1)
    row = checks.snapshot(store, "p")["objectives"][0]
    assert row["phase"] == "transfer" and row["status"] == "ready"
    clock[0] += 2
    checks.start(store, "p", "k")
    answer(store, "q3", clock[0] + 1, kind="transfer")
    row = checks.snapshot(store, "p")["objectives"][0]
    assert row["completed_cycles"] == 1 and row["phase"] == "review" and row["status"] == "waiting"
    assert len(row["evidence"]) == 3 and row["evidence"][0]["session_id"] == "session-q1"
    assert store.load("p").mastery_levels == {}
    assert (
        LearningStore(store.root if hasattr(store, "root") else store.db_path.parent).load("p")
        is not None
    )


def test_help_or_incorrect_answer_cannot_advance(store, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(checks.time, "time", lambda: clock[0])
    checks.start(store, "p", "k")
    answer(store, "helped", 1001, assisted=True)
    row = checks.snapshot(store, "p")["objectives"][0]
    assert (
        row["phase"] == "initial"
        and row["status"] == "ready"
        and not row["evidence"][0]["independent"]
    )
    clock[0] = 1002
    checks.start(store, "p", "k")
    answer(store, "wrong", 1003, result="incorrect")
    row = checks.snapshot(store, "p")["objectives"][0]
    assert row["phase"] == "initial" and row["feedback"]


def test_voided_evidence_reopens_check_and_scope_change_resets_progress(store, monkeypatch):
    monkeypatch.setattr(checks.time, "time", lambda: 1000.0)
    checks.start(store, "p", "k")
    answer(store, "q1", 1001)

    def invalidate(tx):
        tx.progress.quiz_attempts[0].voided = True
        tx.emit("test.void", {})

    store.mutate("p", invalidate)
    row = checks.snapshot(store, "p")["objectives"][0]
    assert row["phase"] == "initial" and row["evidence_invalidated"] and row["status"] == "ready"
    assert checks.start(store, "p", "k")["phase"] == "initial"

    def rename(tx):
        tx.progress.modules[0].knowledge_points[0].name = "新范围"
        tx.emit("test.scope", {})

    store.mutate("p", rename)
    assert checks.snapshot(store, "p")["objectives"][0]["scope_changed"]


def test_unrelated_chat_and_old_questions_do_not_count(store, monkeypatch):
    monkeypatch.setattr(checks.time, "time", lambda: 1000.0)
    checks.start(store, "p", "k")
    q = PendingQuestion(
        question_id="old", knowledge_point_id="k", prompt="old question", expected_answer="2"
    )

    def save(tx):
        tx.progress.learning_evidence.append(
            LearningEvidence(
                question_id="old",
                knowledge_point_id="k",
                timestamp=2000,
                result="correct",
                diagnostic_context=assessment_context(tx.progress, q, "2", "short"),
            )
        )
        tx.emit("chat.message", {"text": "我学会了"})

    store.mutate("p", save)
    row = checks.snapshot(store, "p")["objectives"][0]
    assert row["status"] == "in_progress" and row["evidence"] == []


def test_unknown_project_and_objective_are_rejected(store):
    with pytest.raises(LookupError):
        checks.snapshot(store, "other")
    with pytest.raises(ValueError):
        checks.start(store, "p", "missing")


def test_native_registration_binds_check_and_retains_unfinished_question(store, monkeypatch):
    from deeptutor.learning import lesson_evidence
    from deeptutor.learning.service import LearningService

    monkeypatch.setattr(lesson_evidence, "question_snapshot", lambda *args: {})
    checks.start(store, "p", "k")
    service = LearningService(store)
    q = PendingQuestion(
        question_id="native",
        knowledge_point_id="k",
        module_id="m",
        prompt="独立应用新任务",
        expected_answer="2",
        evidence_kind="application",
    )
    service.register_question("p", q, session_id="existing-session")
    saved = store.load("p").check_cycles["k"]["rounds"][0]
    assert saved["questions"] == ["native"]
    result = checks.start(store, "p", "k")
    assert (
        result["pending"]
        and result["session_id"] == "existing-session"
        and result["question_id"] == "native"
    )
    assert store.load("p").pending_question.question_id == "native"


def test_check_handoff_explains_waiting_after_feedback(store, monkeypatch):
    monkeypatch.setattr(checks.time, "time", lambda: 1000.0)
    first = checks.start(store, "p", "k")
    assert "目标ID" not in first["prompt"] and "evidence_kind" not in first["prompt"]
    assert checks.handoff(store.load("p"))["required_evidence_kind"] == "application"
    answer(store, "q1", 1001)
    packet = checks.handoff(store.load("p"))
    assert packet["status"] == "waiting" and packet["due_at"] >= 87401
    assert packet["required_evidence_kind"] is None
    assert "never count early practice" in packet["rules"]


def test_all_group_receipts_are_visible_and_one_wrong_leaf_prevents_advance(store, monkeypatch):
    monkeypatch.setattr(checks.time, "time", lambda: 1000.0)
    checks.start(store, "p", "k")

    def bind_group(tx):
        checks.bind(
            tx,
            [
                PendingQuestion(
                    question_id=q,
                    knowledge_point_id="k",
                    module_id="m",
                    prompt="new task " + q,
                    expected_answer="2",
                    evidence_kind="application",
                )
                for q in ("g1", "g2")
            ],
        )
        tx.emit("test.group", {})

    store.mutate("p", bind_group)
    answer(store, "g1", 1001, result="incorrect")
    assert checks.snapshot(store, "p")["objectives"][0]["status"] == "in_progress"
    answer(store, "g2", 1002)
    row = checks.snapshot(store, "p")["objectives"][0]
    assert row["phase"] == "initial" and row["latest_result"] == "incorrect"
    assert {e["question_id"] for e in row["evidence"]} == {"g1", "g2"}


def test_same_grading_turn_cannot_append_task_but_later_practice_is_allowed(store, monkeypatch):
    monkeypatch.setattr(checks.time, "time", lambda: 1000.0)
    checks.start(store, "p", "k")
    answer(store, "q1", 1001)

    def add_turn(tx):
        tx.progress.learning_evidence[0].turn_id = "grade-turn"
        tx.emit("test.turn", {})

    store.mutate("p", add_turn)
    q = PendingQuestion(
        question_id="extra",
        knowledge_point_id="k",
        prompt="extra task",
        expected_answer="2",
        evidence_kind="transfer",
    )
    with pytest.raises(ValueError, match="本轮核查已完成"):
        store.mutate("p", lambda tx: checks.bind(tx, [q], turn_id="grade-turn"))
    store.mutate("p", lambda tx: checks.bind(tx, [q], turn_id="later-practice-turn"))
    assert checks.snapshot(store, "p")["objectives"][0]["status"] == "waiting"


def test_scheduled_review_can_retest_unconfirmed_mastery_only_for_its_objective(store, monkeypatch):
    from deeptutor.capabilities.mastery.tools import _review_scope_refusal

    clock = [1000.0]
    monkeypatch.setattr(checks.time, "time", lambda: clock[0])
    checks.start(store, "p", "k")
    answer(store, "q1", 1001)
    row = checks.snapshot(store, "p")["objectives"][0]
    assert (
        _review_scope_refusal({"_mastery_session_mode": "review"}, store.load("p"), "k") is not None
    )
    clock[0] = row["due_at"]
    checks.start(store, "p", "k")
    assert _review_scope_refusal({"_mastery_session_mode": "review"}, store.load("p"), "k") is None
    assert store.load("p").mastery_levels == {}


def test_skip_keeps_audit_but_does_not_block_fresh_check_and_reset_clears_it(store, monkeypatch):
    from deeptutor.learning import lesson_evidence
    from deeptutor.learning.service import LearningService

    monkeypatch.setattr(lesson_evidence, "question_snapshot", lambda *args: {})
    checks.start(store, "p", "k")
    service = LearningService(store)
    question = PendingQuestion(
        question_id="skip",
        knowledge_point_id="k",
        module_id="m",
        prompt="skip me",
        expected_answer="2",
        evidence_kind="application",
    )
    service.register_question("p", question, session_id="s")
    service.abandon_active_question("p")
    r = store.load("p").check_cycles["k"]["rounds"][0]
    assert r["questions"] == [] and r["withdrawn"][0]["question_ids"] == ["skip"]
    checks.start(store, "p", "k")
    answer(store, "fresh", time.time() + 1)
    assert checks.snapshot(store, "p")["objectives"][0]["status"] == "waiting"
    service.reset_path("p")
    assert store.load("p").check_cycles == {} and store.load("p").check_intent == {}


def test_card_answer_brief_closes_check_instead_of_requiring_another_quiz():
    from deeptutor.capabilities.mastery.loop import MasteryLoopCapability
    from deeptutor.core.context import UnifiedContext

    check = {
        "question_ids": ["q"],
        "round_id": None,
        "phase": "review",
        "status": "waiting",
        "due_at_iso": "2026-11-23T13:00:00+00:00",
    }
    context = UnifiedContext(
        user_message="answer",
        session_id="s",
        metadata={
            "mastery_mode": True,
            "mastery_card_grade": {
                "independent_check": check,
                "result": {"result": "correct"},
                "mastered": False,
            },
        },
    )
    brief = MasteryLoopCapability()._grade_seed(context)
    assert "END this turn" in brief and "2026-11-23" in brief
    assert "pose the next question" not in brief
    context.metadata["mastery_path_id"] = "p"
    context.metadata["turn_id"] = "graded-turn"
    context.extension("mastery")["independent_check"] = {**check, "closing_turn_id": "graded-turn"}
    MasteryLoopCapability().augment_kwargs("mastery_quiz", {}, context)
    assert not context.extension("mastery")["quiz_requested"]
