"""Normal tutoring flow and the frozen invariants of bounded policy evolution."""

from __future__ import annotations

import json

import pytest

from deeptutor.learning import policy
from deeptutor.learning.evolution import (
    monitor_evolution,
    policy_for_next,
    regression_check,
    rollback,
    set_consent,
    update_evolution,
)
from deeptutor.learning.models import (
    KnowledgePoint,
    LearningModule,
    LearningProgress,
    PendingQuestion,
    QuizAttempt,
)
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore
from deeptutor.learning.wenjin import (
    enable_path,
    learner_continuity,
    objective_evidence,
    record_reflection,
    research_topic,
    teaching_decision,
)


def progress(kind="memory", path_id="p"):
    return LearningProgress(
        book_id=path_id,
        wenjin_enabled=True,
        modules=[
            LearningModule(
                id="m",
                name="Module",
                order=0,
                knowledge_points=[
                    KnowledgePoint(id="kp", name="An objective", type=kind, module_id="m")
                ],
            )
        ],
    )


def add(
    state,
    question,
    kind="recognition",
    correct=True,
    independent=True,
    *,
    timestamp=1,
    strategy="baseline",
    kp_id="kp",
):
    state.quiz_attempts.append(
        QuizAttempt(
            question_id=question,
            knowledge_point_id=kp_id,
            is_correct=correct,
            independent=independent,
            evidence_kind=kind,
            timestamp=timestamp,
            strategy_version=strategy,
        )
    )


def test_recognition_alone_never_proves_explanation():
    state = progress()
    kp = state.modules[0].knowledge_points[0]
    for index in range(6):
        add(state, str(index))
    state.mastery_levels["kp"] = 1
    assert not policy.is_assessed_mastered(state, kp)
    assert objective_evidence(state, kp)["missing"] == ["explanation"]
    assert teaching_decision(state, kp)["evidence_kind"] == "explanation"
    add(state, "assisted", "explanation", independent=False)
    assert not policy.is_assessed_mastered(state, kp)
    add(state, "why", "explanation")
    assert policy.is_assessed_mastered(state, kp)


@pytest.mark.parametrize(
    "kind,required",
    [
        ("memory", ["recognition", "explanation"]),
        ("concept", ["explanation", "application"]),
        ("procedure", ["application", "transfer"]),
        ("design", ["explanation", "transfer"]),
    ],
)
def test_gate_requires_independent_distinct_abilities(kind, required):
    state = progress(kind)
    kp = state.modules[0].knowledge_points[0]
    for index, ability in enumerate(required):
        assert not policy.is_assessed_mastered(state, kp)
        add(state, str(index), ability)
    assert policy.is_assessed_mastered(state, kp)
    state.quiz_attempts[-1].voided = True
    assert not policy.is_assessed_mastered(state, kp)


def test_one_error_asks_reason_before_increasing_help():
    state = progress()
    add(state, "a", correct=False)
    kp = state.modules[0].knowledge_points[0]
    assert teaching_decision(state, kp)["action"] == "ask_reason"
    add(state, "b", correct=False)
    assert teaching_decision(state, kp)["action"] == "offer_hint"


def test_old_progress_upgrade_preserves_attempts_and_is_idempotent(tmp_path):
    store = LearningStore(tmp_path)
    state = progress()
    state.wenjin_enabled = False
    add(state, "old")
    store.save(state)
    first = enable_path(store, "p")
    second = enable_path(store, "p")
    assert first.quiz_attempts == second.quiz_attempts == state.quiz_attempts
    assert first.version == second.version
    assert first.wenjin_enabled


@pytest.mark.asyncio
async def test_choice_cannot_masquerade_as_an_explanation():
    from deeptutor.capabilities.mastery.tools import MasteryQuizTool
    from deeptutor.core.tool_protocol import ToolResult

    result = await MasteryQuizTool()._build_question(
        {
            "knowledge_point_id": "kp",
            "question": "Why?",
            "expected_answer": "A",
            "options": [{"label": "A", "body": "Because."}, {"label": "B", "body": "Other."}],
            "evidence_kind": "explanation",
            "_mastery_session_mode": "study",
        },
        progress(),
    )
    assert isinstance(result, ToolResult) and not result.success


@pytest.mark.asyncio
async def test_open_response_kind_reaches_durable_independent_evidence(tmp_path, monkeypatch):
    from deeptutor.capabilities.mastery.tools import MasteryQuizTool
    from deeptutor.learning.semantic_assessment import Verdict

    service = LearningService(LearningStore(tmp_path))
    service.save(progress("procedure"))
    question, _, _ = await MasteryQuizTool()._build_question(
        {
            "knowledge_point_id": "kp",
            "question": "Show how to apply the method to this case.",
            "question_type": "open",
            "expected_answer": "A valid worked process",
            "evidence_kind": "application",
            "_mastery_session_mode": "study",
        },
        service.store.load("p"),
    )
    service.register_question("p", question, session_id="s", turn_id="q")
    service.mark_question_awaiting("p", interaction_id=question.question_id)

    # Exercise the normal semantic grading transaction with a fixed verifier.
    async def judge(config, items):
        return {
            item["question_id"]: Verdict(
                result="correct", confidence=1, feedback="Worked correctly"
            )
            for item in items
        }

    monkeypatch.setattr("deeptutor.learning.semantic_assessment.judge_answers", judge)
    state, _, _ = await service.grade_interaction_semantic(
        "p",
        question_id=question.question_id,
        answer="A valid worked process",
        session_id="s",
        turn_id="answer",
    )
    fresh, evidence, _ = service.store.load_with_learning_evidence("p", "kp")
    assert state.quiz_attempts[-1].evidence_kind == "application"
    assert evidence[-1].evidence_kind == "application"
    assert evidence[-1].strategy_version == "baseline"


@pytest.mark.asyncio
async def test_topic_search_without_uploads_stores_real_sources_only(tmp_path, monkeypatch):
    store = LearningStore(tmp_path)
    store.save(progress())
    monkeypatch.setattr(
        "deeptutor.services.search.web_search",
        lambda query: {
            "search_results": [
                {
                    "url": "https://example.org/lesson",
                    "title": "Lesson",
                    "snippet": "Source excerpt",
                },
                {"url": "javascript:alert(1)", "title": "Invalid", "snippet": "Ignore"},
            ]
        },
    )
    result = await research_topic(store, "p", "equation systems")
    assert result["status"] == "retrieved"
    assert len(result["sources"]) == 1
    assert store.load("p").research_sources[0]["url"] == "https://example.org/lesson"


@pytest.mark.asyncio
async def test_unavailable_search_keeps_topic_learning_available(tmp_path, monkeypatch):
    store = LearningStore(tmp_path)
    store.save(progress())

    def unavailable(query):
        raise RuntimeError("No search provider")

    monkeypatch.setattr("deeptutor.services.search.web_search", unavailable)
    result = await research_topic(store, "p", "equation systems")
    assert result["status"] == "unavailable"
    assert not store.load("p").research_sources
    assert store.load("p").wenjin_enabled


def test_reflection_is_durable_and_never_grants_mastery(tmp_path):
    store = LearningStore(tmp_path)
    store.save(progress())
    text = "I now check the total degree of each term before deciding."
    first = record_reflection(store, "p", "kp", text, session_id="s", turn_id="t")
    second = record_reflection(store, "p", "kp", text, session_id="s", turn_id="t")
    assert len(second.reflections) == 1
    assert first.version == second.version
    assert not policy.is_assessed_mastered(second, second.modules[0].knowledge_points[0])


def test_same_learner_history_is_advisory_and_store_scoped(tmp_path):
    one = LearningStore(tmp_path / "one")
    other = LearningStore(tmp_path / "other")
    old = progress(path_id="old")
    add(old, "prior", "explanation")
    one.save(old)
    current = progress()
    one.save(current)
    other.save(progress())
    assert learner_continuity(one, current)[0]["objective"] == "An objective"
    assert not learner_continuity(other, current)
    assert not policy.is_assessed_mastered(current, current.modules[0].knowledge_points[0])


def test_no_evolution_without_consent_and_no_promotion_from_short_term_results():
    state = progress()
    for index in range(10):
        add(state, str(index), "application")
    update_evolution(state)
    assert not state.evolution
    state.evolution = {"consent": True, "consent_at": 0}
    update_evolution(state, now=100)
    assert state.evolution["candidate"]["status"] == "trial"
    update_evolution(state, now=101)
    assert state.evolution["status"] == "trial_insufficient_evidence"
    assert state.evolution["active"]["version"] == "baseline"


@pytest.mark.parametrize("template", ["reason_first", "contrast_first"])
def test_frozen_template_constraints(template):
    assert regression_check(template)["passed"]
    assert not regression_check("arbitrary_model_instruction")["passed"]


def _cohort(state, strategy, success, prefix, start=1):
    for index in range(12):
        kp = prefix + str(index)
        add(
            state,
            prefix + "initial" + str(index),
            "application",
            success,
            timestamp=start + index,
            strategy=strategy,
            kp_id=kp,
        )
        add(
            state,
            prefix + "delayed" + str(index),
            "transfer",
            success,
            timestamp=start + 90000 + index,
            strategy=strategy,
            kp_id=kp,
        )


def test_candidate_promotes_only_after_independent_and_delayed_evidence(tmp_path):
    store = LearningStore(tmp_path)
    state = progress()
    state.evolution = {
        "consent": True,
        "consent_at": 0,
        "active": {"version": "baseline", "template": "reason_first"},
        "candidate": {
            "version": "candidate-1",
            "template": "contrast_first",
            "status": "trial",
            "regression": regression_check("contrast_first"),
        },
    }
    _cohort(state, "baseline", False, "b")
    _cohort(state, "candidate-1", True, "c")
    update_evolution(state, now=200000)
    assert state.evolution["status"] == "monitoring"
    assert state.evolution["active"]["version"] == "candidate-1"
    store.save(state)
    restored = rollback(store, "p")
    assert restored.evolution["active"]["version"] == "baseline"
    assert restored.evolution["history"][-1]["event"] == "learner_rollback"


def test_automatic_rollback_on_later_regression():
    state = progress()
    state.evolution = {
        "consent": True,
        "status": "monitoring",
        "promoted_at": 200000,
        "active": {"version": "candidate-1", "template": "contrast_first"},
        "previous": {"version": "baseline", "template": "reason_first"},
        "candidate": {"version": "candidate-1", "status": "promoted"},
        "evaluation": {
            "baseline": {"independent_interval": [0.8, 1], "delayed_interval": [0.8, 1]}
        },
        "history": [],
    }
    _cohort(state, "candidate-1", False, "bad", start=200001)
    monitor_evolution(state)
    assert state.evolution["status"] == "rolled_back"
    assert state.evolution["active"]["version"] == "baseline"


def test_consent_revocation_stops_new_trials(tmp_path):
    store = LearningStore(tmp_path)
    store.save(progress())
    enabled = set_consent(store, "p", True)
    assert enabled.evolution["consent"]
    disabled = set_consent(store, "p", False)
    before = json.dumps(disabled.evolution)
    update_evolution(disabled)
    assert json.dumps(disabled.evolution) == before
    assert policy_for_next(disabled)["version"] == "baseline"


def test_new_independent_failure_reopens_the_ability_gap():
    state = progress()
    add(state, "a")
    add(state, "b", "explanation")
    kp = state.modules[0].knowledge_points[0]
    assert policy.is_assessed_mastered(state, kp)
    add(state, "c", "explanation", correct=False)
    assert objective_evidence(state, kp)["missing"] == ["explanation"]
    assert not policy.is_assessed_mastered(state, kp)


@pytest.mark.asyncio
async def test_state_and_consent_endpoints_preserve_user_control(tmp_path, monkeypatch):
    from fastapi import FastAPI
    import httpx

    from deeptutor.api.routers import mastery_path

    store = LearningStore(tmp_path)
    initial = progress()
    initial.wenjin_enabled = False
    store.save(initial)
    monkeypatch.setattr(mastery_path, "LearningStore", lambda: store)
    app = FastAPI()
    app.include_router(mastery_path.router, prefix="/api/mastery-paths")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        root = "/api/mastery-paths/topics/p/wenjin"
        response = await client.get(root)
        assert response.status_code == 200 and not response.json()["enabled"]
        assert (await client.post(root + "/enable")).json()["enabled"]
        enabled = await client.put(root + "/consent", json={"enabled": True})
        assert enabled.status_code == 200 and enabled.json()["strategy"]["consent"]
        disabled = await client.put(root + "/consent", json={"enabled": False})
        assert disabled.status_code == 200 and not disabled.json()["strategy"]["consent"]
        assert (await client.post(root + "/rollback")).status_code == 409
        assert (await client.get("/api/mastery-paths/topics/missing/wenjin")).status_code == 404
        assert (
            await client.put(
                "/api/mastery-paths/topics/missing/wenjin/consent", json={"enabled": True}
            )
        ).status_code == 404


@pytest.mark.asyncio
async def test_disabled_web_search_is_honored_without_a_provider_call(monkeypatch):
    from deeptutor.capabilities.mastery.tools import MasteryResearchTool

    def forbidden(*args, **kwargs):
        raise AssertionError("Disabled web search must not run.")

    monkeypatch.setattr("deeptutor.services.search.web_search", forbidden)
    result = await MasteryResearchTool().execute(
        _mastery_path_id="p", _web_research_allowed=False, query="equations"
    )
    assert result.success
    assert json.loads(result.content)["status"] == "disabled"


def test_invalidated_evidence_withdraws_a_promotion():
    from deeptutor.learning.evolution import revalidate_after_repair

    state = progress()
    state.evolution = {
        "consent": True,
        "consent_at": 0,
        "active": {"version": "baseline", "template": "reason_first"},
        "candidate": {
            "version": "candidate-1",
            "template": "contrast_first",
            "status": "trial",
            "regression": regression_check("contrast_first"),
        },
    }
    _cohort(state, "baseline", False, "b")
    _cohort(state, "candidate-1", True, "c")
    update_evolution(state, now=200000)
    assert state.evolution["status"] == "monitoring"
    for attempt in state.quiz_attempts:
        if attempt.strategy_version == "candidate-1":
            attempt.voided = True
    revalidate_after_repair(state)
    assert state.evolution["status"] == "rolled_back"
    assert state.evolution["active"]["version"] == "baseline"
