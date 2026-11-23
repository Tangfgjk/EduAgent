"""Goal confirmation and learning evidence use the same production interface."""

import copy
from types import SimpleNamespace

import pytest

from deeptutor.learning import check_cycles as checks
from deeptutor.learning import goal_contract as goals
from deeptutor.learning import policy
from deeptutor.learning.diagnosis import assessment_context
from deeptutor.learning.models import (
    KnowledgePoint,
    LearningEvidence,
    LearningModule,
    LearningProgress,
    PendingQuestion,
)
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore
from deeptutor.learning.wenjin import objective_evidence


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = LearningStore(tmp_path / "learning")
    s.save(
        LearningProgress(
            book_id="p",
            name="方程",
            wenjin_enabled=True,
            modules=[
                LearningModule(
                    id="m",
                    name="方程",
                    order=0,
                    knowledge_points=[
                        KnowledgePoint(id="k", name="方程的含义", type="concept", module_id="m")
                    ],
                )
            ],
        )
    )
    monkeypatch.setattr(goals, "time", SimpleNamespace(time=lambda: 100.0))
    return s


def confirm(s, level="understand", **kwargs):
    return goals.confirm(
        s,
        "p",
        revision=s.load("p").version,
        level=level,
        summary="学习方程的含义",
        request_id=kwargs.pop("request_id", "goal-request-1"),
        **kwargs,
    )


def receipt(s, name, ability, *, at=101, assisted=False, goal_version=None):
    def record(tx):
        q = PendingQuestion(
            question_id=name,
            knowledge_point_id="k",
            module_id="m",
            prompt="new task " + name,
            expected_answer="2",
            evidence_kind=ability,
        )
        goals.bind_task(tx.progress, q)
        if goal_version is not None:
            q.goal_version = goal_version
        checks.bind(tx, [q], session_id="s-" + name)
        tx.progress.learning_evidence.append(
            LearningEvidence(
                evidence_id=name,
                question_id=name,
                knowledge_point_id="k",
                evidence_kind=ability,
                timestamp=at,
                result="correct",
                learner_answer="2",
                session_id="s-" + name,
                turn_id="t-" + name,
                diagnostic_context=assessment_context(
                    tx.progress, q, "2", "short", assistance_used=assisted
                ),
            )
        )
        tx.emit("test.answer", {})

    s.mutate("p", record)


def test_goal_proposal_never_confirms_or_grants_ability(store):
    before = store.load("p")
    view = goals.propose(
        store, "p", "transfer", "处理新情境", "先确认目标程度", session_id="s", turn_id="t"
    )
    assert view["status"] == "needs_confirmation" and view["current"] is None
    p = store.load("p")
    assert p.goal_contract is None and p.learning_evidence == before.learning_evidence
    assert not policy.is_assessed_mastered(p, p.modules[0].knowledge_points[0])


def test_confirmed_understanding_accepts_real_independent_tasks_not_assisted_work(store):
    view = confirm(store)
    assert view["criteria"]["abilities"] == ["recognition", "explanation"]
    receipt(store, "one", "recognition")
    receipt(store, "helped", "explanation", assisted=True)
    p = store.load("p")
    k = p.modules[0].knowledge_points[0]
    assert not policy.is_assessed_mastered(p, k)
    receipt(store, "two", "explanation", at=102)
    p = store.load("p")
    assert policy.is_assessed_mastered(p, p.modules[0].knowledge_points[0])
    assert goals.load(store, "p")["progress"]["demonstrated"] == 1


def test_goal_change_retains_receipts_pending_question_and_versions(store):
    confirm(store)
    receipt(store, "one", "recognition")
    receipt(store, "two", "explanation")
    p = store.load("p")
    q = PendingQuestion(
        question_id="old", knowledge_point_id="k", prompt="2x=4", expected_answer="2"
    )
    LearningService(store).set_pending_question(p, q)
    before = store.load("p")
    changed = confirm(store, "apply", request_id="goal-request-2")
    p = store.load("p")
    assert len(p.goal_history) == 2 and changed["current"]["assessment_version"] == 2
    assert p.pending_question == before.pending_question and p.pending_question.goal_version == 1
    assert p.learning_evidence == before.learning_evidence
    assert not policy.is_assessed_mastered(p, p.modules[0].knowledge_points[0])
    assert assessment_context(p, p.pending_question, "2", "short").goal_version == 1
    receipt(store, "old-answer", "application", goal_version=1)
    assert (
        "application"
        in objective_evidence(store.load("p"), p.modules[0].knowledge_points[0])["missing"]
    )
    receipt(store, "new-why", "explanation", at=103)
    receipt(store, "new-apply", "application", at=104)
    p = store.load("p")
    assert policy.is_assessed_mastered(p, p.modules[0].knowledge_points[0])


def test_time_budget_edit_keeps_assessment_revision_and_achievement(store):
    confirm(store)
    receipt(store, "one", "recognition")
    receipt(store, "two", "explanation")
    changed = confirm(store, time_budget="每晚半小时", request_id="goal-request-2")
    assert changed["current"]["version"] == 2 and changed["current"]["assessment_version"] == 1
    assert changed["progress"]["demonstrated"] == 1


def test_confirmation_cas_idempotency_and_payload_conflict(store):
    revision = store.load("p").version
    first = confirm(store)
    repeated = goals.confirm(
        store,
        "p",
        revision=revision,
        level="understand",
        summary="学习方程的含义",
        request_id="goal-request-1",
    )
    assert repeated["source_revision"] == first["source_revision"]
    with pytest.raises(ValueError):
        goals.confirm(
            store,
            "p",
            revision=revision,
            level="apply",
            summary="学习方程的含义",
            request_id="goal-request-1",
        )
    with pytest.raises(RuntimeError):
        goals.confirm(
            store,
            "p",
            revision=revision,
            level="apply",
            summary="new target",
            request_id="different-request",
        )


def test_free_text_target_edit_requires_confirmation_without_erasing_contract(store):
    from deeptutor.learning.learning_support import edit_profile

    confirm(store)
    before = store.load("p")
    edit_profile(store, "p", {"target_level": "新的学习目标"}, before.version)
    after = store.load("p")
    assert goals.view(after)["status"] == "needs_confirmation"
    assert after.goal_contract == before.goal_contract and after.goal_history == before.goal_history


@pytest.mark.parametrize(
    "level,kind,needs_transfer",
    [
        ("understand", "explanation", False),
        ("apply", "application", False),
        ("transfer", "application", True),
    ],
)
def test_goal_specific_delayed_cycle(store, level, kind, needs_transfer):
    confirm(store, level)
    checks.start(store, "p", "k", now=101)
    receipt(store, "initial", kind, at=102)
    p = store.load("p")
    kp = p.modules[0].knowledge_points[0]
    state = checks.state_for(p, kp, now=103)
    assert state["phase"] == "review" and state["status"] == "waiting"
    with pytest.raises(ValueError):
        checks.start(store, "p", "k", now=state["due_at"] - 1)
    checks.start(store, "p", "k", now=state["due_at"])
    receipt(store, "review", kind, at=state["due_at"] + 1)
    p = store.load("p")
    state = checks.state_for(p, kp, now=state["due_at"] + 2)
    assert state["completed_cycles"] == (0 if needs_transfer else 1)
    if needs_transfer:
        assert state["phase"] == "transfer"
        checks.start(store, "p", "k", now=state["evidence"][-1]["at"] + 1)
        receipt(store, "transfer", "transfer", at=state["evidence"][-1]["at"] + 2)
        p = store.load("p")
        assert checks.state_for(p, kp)["completed_cycles"] == 1
        assert policy.is_assessed_mastered(p, kp)


def test_another_store_cannot_see_goal_or_proposal(store, tmp_path):
    confirm(store)
    other = LearningStore(tmp_path / "other")
    other.save(LearningProgress(book_id="p"))
    assert goals.load(other, "p")["current"] is None
    assert goals.load(other, "p")["history"] == []


@pytest.mark.asyncio
async def test_native_tool_is_registered_and_cannot_confirm_or_forge_a_quote(store, monkeypatch):
    from deeptutor.capabilities.mastery import MASTERY_TOOL_NAMES, MASTERY_TOOL_TYPES, tools

    assert (
        "mastery_goal_propose" in MASTERY_TOOL_NAMES
        and tools.MasteryGoalProposeTool in MASTERY_TOOL_TYPES
    )
    monkeypatch.setattr(tools, "_new_service", lambda: SimpleNamespace(store=store))
    tool = tools.MasteryGoalProposeTool()
    args = {
        "_mastery_path_id": "p",
        "_session_id": "s",
        "_turn_id": "t",
        "level": "apply",
        "summary": "独立应用",
        "reason": "先明确目标",
        "learner_quote": "伪造学生的话",
        "_learner_message": "你好",
    }
    assert not (await tool.execute(**args)).success
    args["learner_quote"] = ""
    assert (await tool.execute(**args)).success
    assert store.load("p").goal_contract is None


def test_goal_api_rejects_spoofed_confirmation_and_stale_revision(store, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from deeptutor.api.routers import mastery_path

    monkeypatch.setattr(mastery_path, "LearningStore", lambda: store)
    app = FastAPI()
    app.include_router(mastery_path.router, prefix="/api/mastery-paths")
    client = TestClient(app)
    endpoint = "/api/mastery-paths/topics/p/goal-contract"
    body = {
        "source_revision": store.load("p").version,
        "level": "apply",
        "summary": "独立应用",
        "request_id": "request-1234",
    }
    assert client.post(endpoint, json={**body, "confirmed_by": "tutor"}).status_code == 422
    assert client.post(endpoint, json=body).status_code == 200
    assert client.post(endpoint, json={**body, "request_id": "new-request-123"}).status_code == 409
    assert client.get(endpoint).json()["status"] == "confirmed"


def test_goal_roundtrip_uses_postgres_without_touching_sqlite_origin(store):
    from pathlib import Path
    from unittest.mock import patch

    from psycopg import sql

    from deeptutor.services.storage.postgres_adapter import connect_raw, schema_name
    from deeptutor.services.storage.postgres_migration import migrate

    migrate(store.db_path)
    try:
        with patch(
            "deeptutor.services.storage.postgres_adapter.enabled",
            side_effect=lambda p: Path(p).resolve() == store.db_path.resolve(),
        ):
            confirm(store)
            assert (
                goals.load(LearningStore(store.db_path.parent), "p")["current"]["level"]
                == "understand"
            )
        assert goals.load(store, "p")["current"] is None
    finally:
        with connect_raw() as c:
            c.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name(store.db_path)))
            )


def test_confirmation_preserves_intake_budget_and_can_clear_it_explicitly(store):
    from deeptutor.learning.models import LearnerProfile

    def intake(tx):
        tx.progress.learner_profile = LearnerProfile(time_budget="30 minutes three times a week")
        tx.emit("test.intake", {})

    store.mutate("p", intake)
    assert goals.view(store.load("p"))["suggested_time_budget"] == "30 minutes three times a week"
    first = confirm(store)
    assert first["current"]["time_budget"] == "30 minutes three times a week"
    cleared = confirm(store, time_budget="", request_id="clear-time-budget")
    assert cleared["current"]["time_budget"] == ""
    assert cleared["current"]["assessment_version"] == 1


def test_changed_goal_archives_strategy_and_excludes_previous_goal_observations(store):
    from deeptutor.learning.strategy_effects import experiment_rows

    confirm(store)
    receipt(store, "old-1", "recognition")
    receipt(store, "old-2", "explanation")

    def strategy(tx):
        tx.progress.evolution = {
            "consent": True,
            "active": {"version": "old-active", "template": "worked_example"},
            "candidate": {"version": "old-candidate"},
            "evaluation": {"approved": True},
            "exposures": [{"id": "historical-delivery", "goal_version": 1}],
        }
        tx.emit("test.strategy", {})

    store.mutate("p", strategy)
    assert len(experiment_rows(store.load("p"))) == 2
    confirm(store, "apply", request_id="changed-goal-request")
    p = store.load("p")
    assert p.evolution["consent"] is True
    assert p.evolution["active"]["version"] == "baseline"
    assert "candidate" not in p.evolution and "evaluation" not in p.evolution
    assert p.evolution["history"][-1]["previous"]["active"]["version"] == "old-active"
    assert p.evolution["exposures"][0]["id"] == "historical-delivery"
    assert experiment_rows(p) == []
    receipt(store, "new-goal-task", "application", at=103)
    assert len(experiment_rows(store.load("p"))) == 1
