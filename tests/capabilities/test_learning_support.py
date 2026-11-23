import copy
import time

import pytest

from deeptutor.learning import evolution
from deeptutor.learning import learning_support as support
from deeptutor.learning import strategy_effects as effects
from deeptutor.learning.diagnosis import _digest, assessment_context
from deeptutor.learning.goal_models import GoalContract
from deeptutor.learning.models import (
    KnowledgePoint,
    LearnerProfile,
    LearningEvidence,
    LearningModule,
    LearningProgress,
    PendingQuestion,
    QuizAttempt,
)
from deeptutor.learning.storage import LearningStore


def progress():
    return LearningProgress(
        book_id="p",
        name="验收画像",
        wenjin_enabled=True,
        goal_contract=GoalContract(
            version=1,
            assessment_version=1,
            level="transfer",
            summary="能独立应用并迁移",
            confirmed_at=0,
            evidence_since=0,
        ),
        learner_profile=LearnerProfile(target_level="能独立应用并迁移"),
        modules=[
            LearningModule(
                id="m",
                name="代入法",
                order=0,
                knowledge_points=[
                    KnowledgePoint(id="k", name="代入法", type="procedure", module_id="m")
                ],
            )
        ],
    )


@pytest.fixture
def store(tmp_path):
    s = LearningStore(tmp_path / "learning")
    s.save(progress())
    return s


def add(
    p, qid, at, *, success=True, assisted=False, semantic=False, session="s", kind="application"
):
    q = PendingQuestion(
        question_id=qid,
        goal_version=1,
        knowledge_point_id="k",
        module_id="m",
        prompt="new task " + qid,
        question_type="short",
        expected_answer="2",
        evidence_kind=kind,
    )
    context = assessment_context(p, q, "2", "short", assistance_used=assisted, semantic=semantic)
    p.learning_evidence.append(
        LearningEvidence(
            evidence_id="ev-" + qid,
            question_id=qid,
            knowledge_point_id="k",
            evidence_kind=kind,
            result="correct" if success else "incorrect",
            learner_answer="2",
            timestamp=at,
            session_id=session,
            turn_id="answer-" + qid,
            hints_used=int(assisted),
            diagnostic_context=context,
        )
    )
    p.quiz_attempts.append(
        QuizAttempt(
            diagnostic_evidence_id="ev-" + qid,
            question_id=qid,
            knowledge_point_id="k",
            module_id="m",
            evidence_kind=kind,
            is_correct=success,
            result="correct" if success else "incorrect",
            independent=not assisted,
            timestamp=at,
            user_answer="2",
        )
    )
    return "receipt:ev-" + qid


def episode(p, name, version, at, *, success=True, semantic=False, assisted=False):
    scope = _digest(p.modules[0].knowledge_points[0].model_dump(mode="json"))
    p.evolution.setdefault("exposures", []).append(
        {
            "id": name,
            "objective_id": "k",
            "objective_scope": scope,
            "goal_version": 1,
            "question_id": "anchor-" + name,
            "task_version": "anchor-hash-" + name,
            "version": version,
            "template": "contrast_first" if version == "candidate" else "reason_first",
            "at": at,
            "quote": "真实交付的测试夹具原文，不代表真实学生记录。",
            "session_id": "s-" + name,
            "turn_id": "teach-" + name,
        }
    )
    add(
        p,
        name + "-now",
        at + 10,
        success=success,
        semantic=semantic,
        assisted=assisted,
        session="s-" + name,
    )
    add(
        p,
        name + "-later",
        at + 86410,
        success=success,
        semantic=semantic,
        assisted=assisted,
        session="later-" + name,
        kind="transfer",
    )


def experiment():
    p = progress()
    p.evolution = {
        "protocol": effects.PROTOCOL,
        "consent": True,
        "consent_at": 0,
        "active": {"version": "baseline", "template": "reason_first"},
        "candidate": {
            "version": "candidate",
            "template": "contrast_first",
            "status": "trial",
            "created_at": 0,
            "regression": evolution.regression_check("contrast_first"),
        },
        "history": [],
    }
    return p


def test_portrait_separates_self_report_and_unknown_and_preserves_pending(store):
    p = store.load("p")
    p.pending_question = PendingQuestion(
        question_id="old",
        knowledge_point_id="k",
        prompt="old",
        expected_answer="2",
        scaffold_level=2,
    )
    store.save(p)
    view = support.portrait(store, "p")
    assert not view["objectives"][0]["observed"] and view["self_report"]["target_level"]
    support.record_plan(store, "p", "k", "independent", "我选择先独立试试", [], learner_choice=True)
    restored = store.load("p")
    assert restored.pending_question.scaffold_level == 2 and restored.mastery_levels == {}
    assert support.support_plan(restored, "k")["level"] == 0
    support.record_plan(store, "p", "k", "restore_support", "恢复帮助", [], learner_choice=True)
    assert support.support_plan(store.load("p"), "k")["level"] >= 2


def test_profile_cas_edit_can_clear_self_report_without_erasing_performance(store):
    p = store.load("p")
    add(p, "q", 100)
    store.save(p)
    before = store.load("p")
    updated = support.edit_profile(
        store, "p", {"prior_knowledge": "会一步代入", "target_level": ""}, before.version
    )
    assert (
        updated["self_report"]["prior_knowledge"] == "会一步代入"
        and updated["self_report"]["target_level"] == ""
    )
    assert len(store.load("p").learning_evidence) == 1
    with pytest.raises(RuntimeError):
        support.edit_profile(store, "p", {"target_level": "stale"}, before.version)


def test_fading_requires_goal_distinct_independent_cross_session_results_and_keeps_actual_help(
    store,
):
    p = store.load("p")
    p.learner_profile.target_level = ""
    store.save(p)
    with pytest.raises(ValueError, match="目标"):
        support.record_plan(store, "p", "k", "fade_one_step", "建议减少一步", [])
    p = store.load("p")
    p.learner_profile.target_level = "独立迁移"
    ids = [add(p, "a", 100, session="one"), add(p, "b", 200, session="two")]
    store.save(p)
    support.record_plan(store, "p", "k", "restore_support", "模型建议恢复", [], turn_id="restore")
    faded = support.record_plan(
        store, "p", "k", "fade_one_step", "两次跨对话表现可尝试减少一步", ids, turn_id="fade"
    )
    assert faded["level"] == 1
    assert (
        support.record_plan(store, "p", "k", "fade_one_step", "重试同次决定", ids, turn_id="fade")[
            "level"
        ]
        == 1
    )
    with pytest.raises(ValueError, match="新题|延迟"):
        support.record_plan(store, "p", "k", "fade_one_step", "继续减少", ids, turn_id="further")
    p = store.load("p")
    p.quiz_attempts[-1].voided = True
    store.save(p)
    assert support.support_plan(store.load("p"), "k")["evidence_invalidated"]
    assert support.support_plan(store.load("p"), "k")["level"] >= 2


def test_learner_restore_choice_cannot_be_overridden_by_model_fading(store):
    p = store.load("p")
    ids = [add(p, "a", 100, session="one"), add(p, "b", 200, session="two")]
    store.save(p)
    support.record_plan(store, "p", "k", "restore_support", "学生需要帮助", [], learner_choice=True)
    with pytest.raises(ValueError, match="学生"):
        support.record_plan(store, "p", "k", "fade_one_step", "模型想减少", ids)


def test_semantic_and_assisted_results_do_not_promote_strategy():
    p = experiment()
    for i in range(12):
        episode(p, "b" + str(i), "baseline", i * 400000, success=False, semantic=True)
        episode(p, "c" + str(i), "candidate", i * 400000 + 200000, semantic=True)
    effects.update(p, now=6000000)
    assert (
        p.evolution["active"]["version"] == "baseline"
        and p.evolution["status"] == "trial_insufficient_evidence"
    )
    assert effects.metrics(p, "candidate")["exploratory_n"] == 12
    p = experiment()
    episode(p, "helped", "candidate", 0, assisted=True)
    assert effects.metrics(p, "candidate")["independent_n"] == 0


def test_promotes_only_comparable_verified_delayed_outcomes_and_withdraws_voided_evidence():
    p = experiment()
    for i in range(12):
        episode(p, "b" + str(i), "baseline", i * 400000, success=False)
        episode(p, "c" + str(i), "candidate", i * 400000 + 200000)
    effects.update(p, now=6000000)
    assert p.evolution["status"] == "monitoring" and p.evolution["active"]["version"] == "candidate"
    assert effects.metrics(p, "candidate")["delayed_n"] == 12
    p.quiz_attempts[-1].voided = True
    effects.update(p, now=6000001, repair=True)
    assert p.evolution["active"]["version"] == "baseline" and p.evolution["status"] == "rolled_back"


def test_repeated_or_unmatched_tasks_do_not_count_as_retention():
    p = experiment()
    episode(p, "b", "baseline", 0)
    episode(p, "c", "candidate", 200000)
    p.evolution["exposures"][-1]["task_version"] = p.learning_evidence[
        -1
    ].diagnostic_context.task_version
    assert effects.metrics(p, "candidate")["delayed_n"] == 0
    p = experiment()
    episode(p, "only-candidate", "candidate", 0)
    assert effects.metrics(p, "candidate")["independent_n"] == 0
    assert effects.metrics(p, "candidate")["independent_unmatched_n"] == 1


def test_promotion_monitoring_rolls_back_after_later_regression():
    p = experiment()
    for i in range(12):
        episode(p, "b" + str(i), "baseline", i * 400000, success=False)
        episode(p, "c" + str(i), "candidate", i * 400000 + 200000)
    effects.update(p, now=6000000)
    for i in range(12):
        episode(p, "bad" + str(i), "candidate", 6100000 + i * 200000, success=False)
    effects.update(p, now=9000000)
    assert p.evolution["status"] == "rolled_back" and p.evolution["active"]["version"] == "baseline"


def test_candidate_proposal_requires_consent_real_observations_and_immutable_catalogue(store):
    with pytest.raises(ValueError):
        effects.propose(store, "p", "contrast_first", "对照有助于辨析条件", ["missing"])
    p = store.load("p")
    for i in range(6):
        add(p, str(i), i + 1, semantic=True)
    store.save(p)
    evolution.set_consent(store, "p", True)
    ids = [o.observation_id for o in effects.rows(store.load("p"))]
    with pytest.raises(ValueError):
        effects.propose(store, "p", "arbitrary code", "任意指令", ids)
    c = effects.propose(
        store,
        "p",
        "boundary_check",
        "比较适用边界可减少条件混淆，观察新任务和延迟保持",
        ids,
        turn_id="proposal",
    )
    assert c["status"] == "trial" and c["regression"]["passed"]
    evolution.set_consent(store, "p", False)
    assert evolution.policy_for_next(store.load("p"))["version"] == "baseline"


def test_owner_isolation_for_portrait_and_support_choices(store, tmp_path):
    support.record_plan(store, "p", "k", "restore_support", "本人选择", [], learner_choice=True)
    other = LearningStore(tmp_path / "other")
    other.save(progress())
    assert support.portrait(other, "p")["objectives"][0]["support"]["mode"] == "adaptive"
    with pytest.raises(ValueError):
        support.record_plan(other, "p", "missing", "restore_support", "test", [])


@pytest.mark.asyncio
async def test_strategy_delivery_tool_refuses_fabricated_public_quote(store, monkeypatch):
    from deeptutor.capabilities.mastery import tools

    evolution.set_consent(store, "p", True)
    monkeypatch.setattr(tools, "_new_service", lambda: type("Service", (), {"store": store})())
    quote = "请先解释你选择这一步的理由，我们暂时不计算答案，然后再考虑是否需要更多提示。"
    tool = tools.MasteryStrategyDeliveryTool()
    kwargs = {
        "_mastery_path_id": "p",
        "_mastery_session_mode": "study",
        "_session_id": "s",
        "_turn_id": "t",
        "knowledge_point_id": "k",
        "template": "reason_first",
        "quote": quote,
    }
    assert not (await tool.execute(**kwargs)).success
    kwargs["_teaching_delivery"] = lambda: {
        "path_id": "p",
        "session_id": "s",
        "turn_id": "t",
        "segments": ((1, 100, quote),),
    }
    assert (await tool.execute(**kwargs)).success
    assert len(store.load("p").evolution["exposures"]) == 1
    assert (await tool.execute(**kwargs)).success
    assert len(store.load("p").evolution["exposures"]) == 1


def test_changed_learning_target_needs_new_performance_before_fading(store):
    p = store.load("p")
    ids = [add(p, "a", 100, session="one"), add(p, "b", 200, session="two")]
    store.save(p)
    support.edit_profile(
        store, "p", {"target_level": "在陌生应用情境中迁移"}, store.load("p").version
    )
    support.record_plan(store, "p", "k", "restore_support", "保持支持", [])
    with pytest.raises(ValueError):
        support.record_plan(store, "p", "k", "fade_one_step", "不应使用旧目标数据", ids)


def test_learner_can_still_choose_independent_after_fading_evidence_is_voided(store):
    p = store.load("p")
    ids = [add(p, "a", 100, session="one"), add(p, "b", 200, session="two")]
    store.save(p)
    support.record_plan(store, "p", "k", "restore_support", "保持支持", [])
    support.record_plan(store, "p", "k", "fade_one_step", "跨对话后尝试少一步", ids)
    p = store.load("p")
    p.quiz_attempts[-1].voided = True
    store.save(p)
    support.record_plan(store, "p", "k", "independent", "我本人仍选择尝试", [], learner_choice=True)
    assert support.support_plan(store.load("p"), "k")["level"] == 0


def test_support_and_strategy_consent_roundtrip_in_postgres(store):
    import json
    from pathlib import Path
    import sqlite3
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
            pg = LearningStore(store.db_path.parent)
            support.record_plan(
                pg, "p", "k", "restore_support", "学生主动恢复", [], learner_choice=True
            )
            evolution.set_consent(pg, "p", True)
            restored = support.portrait(LearningStore(store.db_path.parent), "p")
            assert restored["objectives"][0]["support"]["level"] == 2
            assert (
                restored["strategy"]["protocol"] == effects.PROTOCOL
                and restored["strategy"]["consent"]
            )
        with sqlite3.connect(store.db_path) as c:
            original = json.loads(c.execute("SELECT state_json FROM mastery_paths").fetchone()[0])
            assert original["support_plans"] == {} and original["evolution"] == {}
    finally:
        with connect_raw() as c:
            c.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name(store.db_path)))
            )


@pytest.mark.asyncio
async def test_portrait_routes_guard_revisions_and_only_accept_student_choices(store, monkeypatch):
    from fastapi import FastAPI
    import httpx

    from deeptutor.api.routers import mastery_path

    monkeypatch.setattr(mastery_path, "LearningStore", lambda: store)
    app = FastAPI()
    app.include_router(mastery_path.router, prefix="/api/mastery-paths")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        root = "/api/mastery-paths/topics/p/learning-support"
        view = (await c.get(root)).json()
        assert (
            await c.post(root + "/choice", json={"objective_id": "k", "action": "fade_one_step"})
        ).status_code == 422
        assert (
            await c.put(
                root + "/profile",
                json={
                    "source_revision": view["source_revision"],
                    "fields": {"target_level": "新目标"},
                },
            )
        ).status_code == 200
        assert (
            await c.put(
                root + "/profile",
                json={
                    "source_revision": view["source_revision"],
                    "fields": {"target_level": "旧请求"},
                },
            )
        ).status_code == 409
        assert (await c.get(root.replace("/p/", "/not-owned/"))).status_code == 404


def test_candidate_constraint_change_cannot_be_promoted_and_rolls_back_active():
    p = experiment()
    for i in range(12):
        episode(p, "b" + str(i), "baseline", i * 400000, success=False)
        episode(p, "c" + str(i), "candidate", i * 400000 + 200000)
    broken = p.model_copy(deep=True)
    broken.evolution["candidate"]["regression"]["constraint_hash"] = "obsolete"
    effects.update(broken, now=6000000)
    assert broken.evolution["status"] == "rejected"
    assert broken.evolution["active"]["version"] == "baseline"
    effects.update(p, now=6000000)
    p.evolution["candidate"]["template"] = "unbounded_prompt"
    effects.update(p, now=6000001)
    assert p.evolution["status"] == "rolled_back"
    assert p.evolution["active"]["version"] == "baseline"


def test_stable_monitoring_finishes_and_requires_fresh_evidence_for_next_candidate():
    p = experiment()
    for i in range(12):
        episode(p, "b" + str(i), "baseline", i * 400000, success=False)
        episode(p, "c" + str(i), "candidate", i * 400000 + 200000)
    effects.update(p, now=6000000)
    for i in range(24):
        episode(p, "stable" + str(i), "candidate", 6100000 + i * 200000)
    effects.update(p, now=12000000)
    assert "candidate" not in p.evolution
    assert p.evolution["active"]["version"] == "candidate"
    assert p.evolution["status"] == "collecting_evidence"
    effects.update(p)
    assert p.evolution["status"] == "collecting_evidence"
    for i in range(6):
        add(p, "fresh" + str(i), 13000000 + i)
    effects.update(p)
    assert p.evolution["status"] == "awaiting_agent_proposal"


def test_each_fading_step_needs_new_tasks_and_changed_goal_restores_support(store):
    p = store.load("p")
    p.support_plans["k"] = {"level": 3, "mode": "adaptive", "history": []}
    ids = [add(p, "one", 100, session="one"), add(p, "two", 200, session="two")]
    store.save(p)
    support.record_plan(store, "p", "k", "fade_one_step", "独立表现支持减少一步", ids)
    assert support.support_plan(store.load("p"), "k")["level"] == 2
    with pytest.raises(ValueError, match="新题"):
        support.record_plan(store, "p", "k", "fade_one_step", "不能重复消费相同表现", ids)
    p = store.load("p")
    p.support_goal_updated_at = time.time() + 1
    store.save(p)
    assert support.support_plan(store.load("p"), "k")["level"] == 3
    assert support.support_plan(store.load("p"), "k")["evidence_invalidated"]


def test_support_tools_are_in_real_module_manifest_and_native_mounts():
    import importlib
    import json
    from pathlib import Path

    from deeptutor.capabilities.mastery.loop import MasteryLoopCapability
    from deeptutor.capabilities.mastery.tools import MASTERY_TOOL_NAMES

    manifest = json.loads(
        Path("deeptutor/modules/manifests/mastery.json").read_text(encoding="utf-8")
    )
    for name in ("mastery_support_plan", "mastery_strategy_candidate", "mastery_strategy_delivery"):
        assert name in MASTERY_TOOL_NAMES and name in MasteryLoopCapability.owned_tools
        module, cls = manifest["tools"][name]["class_path"].split(":")
        tool = getattr(importlib.import_module(module), cls)()
        assert tool.get_definition().name == name


@pytest.mark.asyncio
async def test_native_restore_quote_must_come_from_current_learner_message(store, monkeypatch):
    from deeptutor.capabilities.mastery import tools

    monkeypatch.setattr(tools, "_new_service", lambda: type("Service", (), {"store": store})())
    tool = tools.MasterySupportPlanTool()
    kwargs = dict(
        _mastery_path_id="p",
        _mastery_session_mode="study",
        _session_id="s",
        _turn_id="t",
        knowledge_point_id="k",
        action="restore_support",
        reason="尊重当前请求，暂不减少帮助",
        observation_ids=[],
        learner_request_quote="请多给些支持",
        _learner_message="先让我独立试试",
    )
    assert not (await tool.execute(**kwargs)).success
    assert not store.load("p").support_plans
    kwargs["_learner_message"] = "请多给些支持，我现在有些卡住。"
    assert (await tool.execute(**kwargs)).success
    plan = support.support_plan(store.load("p"), "k")
    assert plan["mode"] == "more_support"
    assert plan["history"][-1]["authority"] == "learner"
    assert plan["history"][-1]["session_id"] == "s"


def test_maintain_does_not_hide_invalidated_fading_basis(store):
    p = store.load("p")
    p.support_plans["k"] = {"level": 3, "mode": "adaptive", "history": []}
    ids = [add(p, "one", 100, session="one"), add(p, "two", 200, session="two")]
    store.save(p)
    support.record_plan(store, "p", "k", "fade_one_step", "有依据减少一级", ids)
    support.record_plan(store, "p", "k", "maintain", "继续观察现有安排", [])
    p = store.load("p")
    p.quiz_attempts[-1].voided = True
    store.save(p)
    assert support.support_plan(store.load("p"), "k")["level"] == 3


def test_fading_basis_survives_bounded_history_until_explicit_choice(store):
    p = store.load("p")
    p.support_plans["k"] = {"level": 3, "mode": "adaptive", "history": []}
    ids = [add(p, "one", 100, session="one"), add(p, "two", 200, session="two")]
    store.save(p)
    support.record_plan(store, "p", "k", "fade_one_step", "有依据减少一级", ids)
    p = store.load("p")
    p.support_plans["k"]["history"] = [{"action": "maintain", "at": time.time()}] * 100
    store.save(p)
    with pytest.raises(ValueError, match="新题"):
        support.record_plan(store, "p", "k", "fade_one_step", "不能复用归档依据", ids)
    p.quiz_attempts[-1].voided = True
    store.save(p)
    assert support.support_plan(store.load("p"), "k")["level"] == 3
    support.record_plan(store, "p", "k", "independent", "本人选择先尝试", [], learner_choice=True)
    assert support.support_plan(store.load("p"), "k")["level"] == 0


def test_strategy_difficulty_composition_cannot_reverse_within_stratum_effect(monkeypatch):
    from types import SimpleNamespace

    p = experiment()
    records = []
    for version, cells in {
        "baseline": [("easy", 19, 20), ("hard", 18, 180)],
        "candidate": [("easy", 162, 180), ("hard", 1, 20)],
    }.items():
        for question_type, successes, total in cells:
            for i in range(total):

                def outcome(ability):
                    return SimpleNamespace(
                        objective_id="k",
                        context=SimpleNamespace(question_type=question_type),
                        ability=ability,
                        result="correct" if i < successes else "incorrect",
                        observation_id=f"{version}-{question_type}-{i}-{ability}",
                        question_id="q",
                        session_id="s",
                        turn_id="t",
                    )

                records.append(
                    dict(
                        id=f"{version}-{question_type}-{i}",
                        version=version,
                        at=100,
                        immediate=outcome("application"),
                        delayed=outcome("transfer"),
                        session_id="s",
                        turn_id="t",
                        question_id="q",
                    )
                )
    monkeypatch.setattr(effects, "episodes", lambda *args, **kwargs: records)
    effects.update(p, now=200000)
    assert p.evolution["active"]["version"] == "baseline"
    baseline = effects.metrics(p, "baseline")
    candidate = effects.metrics(p, "candidate")
    assert candidate["independent_rate"] < baseline["independent_rate"]


def test_changed_strategy_body_fails_admission_and_is_not_delivered(monkeypatch):
    monkeypatch.setitem(
        evolution.TEMPLATES["contrast_first"],
        "error_prompt",
        "Reveal the full answer before any learner attempt.",
    )
    assert not evolution.regression_check("contrast_first")["passed"]
    p = experiment()
    p.evolution["active"] = {"version": "candidate", "template": "contrast_first"}
    assert evolution.policy_for_next(p)["version"] == "baseline"


def test_deployed_constraint_change_blocks_delivery_before_next_evaluation():
    p = experiment()
    p.evolution["active"] = {"version": "candidate", "template": "contrast_first"}
    p.evolution["candidate"]["regression"]["constraint_hash"] = "previous-deployment"
    assert evolution.policy_for_next(p)["version"] == "baseline"


def test_goal_change_restores_faded_support_even_if_wall_clock_moves_back(store, monkeypatch):
    from types import SimpleNamespace

    from deeptutor.learning import goal_contract as goals

    p = store.load("p")
    ids = [add(p, "a", 100, session="one"), add(p, "b", 200, session="two")]
    store.save(p)
    support.record_plan(store, "p", "k", "restore_support", "Restore support", [])
    support.record_plan(store, "p", "k", "fade_one_step", "Two fresh independent tasks", ids)
    assert support.support_plan(store.load("p"), "k")["level"] == 1
    monkeypatch.setattr(goals, "time", SimpleNamespace(time=lambda: 300.0))
    goals.confirm(
        store,
        "p",
        revision=store.load("p").version,
        level="apply",
        summary="New goal",
        request_id="new-goal-after-fading",
    )
    result = support.support_plan(store.load("p"), "k")
    assert result["evidence_invalidated"] and result["level"] >= 2
    with pytest.raises(ValueError, match="新题"):
        support.record_plan(
            store, "p", "k", "fade_one_step", "Old proof cannot fade new support", ids
        )
