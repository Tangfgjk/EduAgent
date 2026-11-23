"""A learner-editable portrait and reversible future-task support plans.

Self-reports, observed performance, delivered help and planned help stay distinct.
"""

from __future__ import annotations

import time
import uuid

from deeptutor.learning.goal_contract import assessment_version
from deeptutor.learning.goal_contract import view as goal_view
from deeptutor.learning.strategy_effects import PROTOCOL, rows

LEVELS = {
    0: "先独立尝试，帮助仍可随时请求",
    1: "按需给一个方向提示",
    2: "按需拆成小步",
    3: "按需提供类似例题与详细讲解",
}


def support_plan(progress, objective_id):
    point = next(
        (k for m in progress.modules for k in m.knowledge_points if k.id == objective_id), None
    )
    if point is None:
        raise ValueError("知识点不存在或已调整")
    plan = progress.support_plans.get(objective_id, {})
    pending = progress.pending_question
    question = (
        next(
            (q for q in pending.questions or [pending] if q.knowledge_point_id == objective_id),
            None,
        )
        if pending
        else None
    )
    last_choice = next(
        (
            h
            for h in reversed(plan.get("history", []))
            if h["action"] in {"fade_one_step", "restore_support", "independent"}
        ),
        None,
    )
    if last_choice is None:
        last_choice = plan.get("fading_basis")
    last_fade = last_choice if last_choice and last_choice["action"] == "fade_one_step" else None
    valid_ids = {o.observation_id for o in rows(progress) if o.objective_id == objective_id}
    invalidated = bool(
        last_fade
        and (
            not set(last_fade["observation_ids"]) <= valid_ids
            or last_fade.get("goal_version", 0) != assessment_version(progress)
            or progress.support_goal_updated_at > last_fade["at"]
        )
    )
    effective = (
        max(plan.get("level", 1), last_fade["before"]) if invalidated else plan.get("level", 1)
    )
    return {
        "evidence_invalidated": invalidated,
        "objective_id": objective_id,
        "level": effective,
        "label": LEVELS[effective],
        "mode": plan.get("mode", "adaptive"),
        "reason": "此前减少帮助的依据已变化，暂按较多支持接续；旧决定保留供回顾。"
        if invalidated
        else plan.get("reason", "尚未安排减少帮助，先了解你的起点与目标。"),
        "actual_level": question.scaffold_level if question else None,
        "history": plan.get("history", [])[-20:],
        "rules": "计划只影响后续新任务；当前题已交付帮助不会被抹去。可随时请求或恢复帮助。",
    }


def record_plan(
    store,
    project,
    objective_id,
    action,
    reason,
    observation_ids,
    *,
    session_id="",
    turn_id="",
    learner_choice=False,
):
    if action not in {"maintain", "fade_one_step", "restore_support", "adaptive", "independent"}:
        raise ValueError("不支持的学习支持动作")
    if (
        not isinstance(observation_ids, list)
        or len(observation_ids) > 10
        or any(not isinstance(i, str) or not i for i in observation_ids)
    ):
        raise ValueError("请引用不超过10条有效表现记录")
    if not reason.strip() or len(reason) > 600:
        raise ValueError("请简短说明调整帮助的理由")
    if not learner_choice and action in {"adaptive", "independent"}:
        raise ValueError("这项选择需由学生本人作出")

    def change(tx):
        p = tx.progress
        current = support_plan(p, objective_id)
        if turn_id and any(
            h["turn_id"] == turn_id and h["action"] == action for h in current["history"]
        ):
            return current
        valid = {o.observation_id: o for o in rows(p) if o.objective_id == objective_id}
        if not set(observation_ids) <= set(valid):
            raise ValueError("请引用本知识点当前有效的表现记录")
        level = current["level"]
        mode = current["mode"]
        if action == "fade_one_step":
            if mode != "adaptive":
                raise ValueError("学生已选择自己的帮助方式；请先尊重该选择")
            if not p.learner_profile or not p.learner_profile.target_level.strip():
                raise ValueError("学习目标程度尚未明确，请先询问学生，保持现有支持")
            from deeptutor.learning.goal_contract import active, qualifying_rows

            goal = active(p)
            if p.wenjin_enabled and not goal:
                raise ValueError("请先由学生确认目标及达标标准；暂时保持支持")
            evidence = [valid[i] for i in dict.fromkeys(observation_ids)]
            if goal:
                point = next(
                    k for m in p.modules for k in m.knowledge_points if k.id == objective_id
                )
                evidence = qualifying_rows(p, point, evidence)
            last_fade_at = max(
                (h["at"] for h in current["history"] if h["action"] == "fade_one_step"),
                default=p.support_plans.get(objective_id, {}).get("fading_basis", {}).get("at", 0),
            )
            successful = [
                o
                for o in evidence
                if o.assistance == "independent"
                and o.result == "correct"
                and o.occurred_at >= p.support_goal_updated_at
                and o.occurred_at > last_fade_at
            ]
            latest = list(valid.values())[-1:]
            if len({o.context.task_version for o in successful}) < 2 or (
                latest and latest[-1].result != "correct"
            ):
                raise ValueError(
                    "需要至少两道不同新题的独立正确表现，且最新表现无待澄清困难；不凭BKT概率或自述撤架"
                )
            distinct_sessions = {o.session_id for o in successful if o.session_id}
            if (
                len(distinct_sessions) < 2
                and max(o.occurred_at for o in successful) - min(o.occurred_at for o in successful)
                < 86400
            ):
                raise ValueError("还需跨对话或跨天的表现验证，先保持支持")
            if level <= 1:
                from deeptutor.learning.check_cycles import state_for

                point = next(
                    k for m in p.modules for k in m.knowledge_points if k.id == objective_id
                )
                from deeptutor.learning.wenjin import objective_evidence

                if goal and not objective_evidence(p, point)["goal_achieved"]:
                    raise ValueError("目标所需能力尚未核查齐全，先保持支持")
                if state_for(p, point)["completed_cycles"] < 1:
                    raise ValueError("减少到独立尝试前，还需完成延迟复习与迁移验证")
            level = max(0, level - 1)
        elif action == "restore_support":
            previous = next(
                (
                    h["before"]
                    for h in reversed(current["history"])
                    if h["action"] == "fade_one_step"
                ),
                2,
            )
            level = min(3, max(2, level, previous))
            if learner_choice:
                mode = "more_support"
        elif action == "independent":
            level = 0
            mode = "independent"
        elif action == "adaptive":
            mode = "adaptive"
        plan = p.support_plans.setdefault(objective_id, {"history": []})
        if turn_id and any(
            h["turn_id"] == turn_id and h["action"] == action for h in plan["history"]
        ):
            return support_plan(p, objective_id)
        history = {
            "id": uuid.uuid4().hex,
            "action": action,
            "before": current["level"],
            "after": level,
            "reason": reason.strip(),
            "observation_ids": list(dict.fromkeys(observation_ids)),
            "basis": [
                {
                    "observation_id": i,
                    "session_id": valid[i].session_id,
                    "turn_id": valid[i].turn_id,
                    "question_id": valid[i].question_id,
                }
                for i in dict.fromkeys(observation_ids)
            ],
            "goal_version": assessment_version(p),
            "authority": "learner" if learner_choice else "tutor_model",
            "session_id": session_id,
            "turn_id": turn_id,
            "at": time.time(),
        }
        if action == "fade_one_step":
            plan["fading_basis"] = dict(history)
        elif action in {"restore_support", "independent"}:
            plan.pop("fading_basis", None)
        plan.update(level=level, mode=mode, reason=reason.strip(), updated_at=time.time())
        plan["history"] = (plan["history"] + [history])[-100:]
        tx.emit(
            "support.plan_changed",
            {"objective_id": objective_id, **history},
            session_id=session_id,
            turn_id=turn_id,
        )
        return support_plan(p, objective_id)

    return store.mutate(project, change)[1]


def portrait(store, project):
    from deeptutor.learning.check_cycles import state_for
    from deeptutor.learning.wenjin import state_summary

    p = store.load(project)
    if p is None:
        raise LookupError("项目不存在")
    facts = rows(p)
    state = state_summary(p, store=store)
    objectives = []
    for m in p.modules:
        for point in m.knowledge_points:
            evidence = [o for o in facts if o.objective_id == point.id]
            objectives.append(
                {
                    "id": point.id,
                    "name": point.name,
                    "observed": bool(evidence),
                    "independent_n": sum(o.assistance == "independent" for o in evidence),
                    "assisted_n": sum(o.assistance == "assisted" for o in evidence),
                    "latest_result": evidence[-1].result if evidence else None,
                    "support": support_plan(p, point.id),
                    "check": {
                        k: v
                        for k, v in state_for(p, point).items()
                        if k in {"phase_label", "status", "due_at", "completed_cycles"}
                    },
                    "evidence": [
                        {
                            "id": o.observation_id,
                            "question_id": o.question_id,
                            "session_id": o.session_id,
                            "turn_id": o.turn_id,
                            "at": o.occurred_at,
                            "ability": o.ability,
                            "result": o.result,
                            "assistance": o.assistance,
                            "model_feedback": o.context.grading_method == "semantic",
                        }
                        for o in evidence[-8:]
                    ],
                }
            )
    topic = store.get_topic(project, progress=p)
    return {
        "project_id": project,
        "name": p.name,
        "source_revision": p.version,
        "goal": topic.metadata.goal if topic else p.name,
        "self_report": p.learner_profile.model_dump(mode="json") if p.learner_profile else {},
        "objective_id": (state.get("objective") or {}).get("id"),
        "objectives": objectives,
        "strategy": strategy_view(p),
        "limits": [
            "起点和目标由学生自述，可修改；自述不直接算掌握。",
            "记录不足表示未知，不表示不会。",
            "支持计划与当前题实际获得的帮助分开，恢复帮助不扣分。",
        ],
    }


def strategy_view(progress):
    import copy

    from deeptutor.learning.evolution import TEMPLATES
    from deeptutor.learning.strategy_effects import update

    p = progress.model_copy(deep=True)
    if p.evolution.get("protocol") == PROTOCOL:
        update(p)
    state = copy.deepcopy(p.evolution)
    state.pop("exposures", None)
    state["delivery_count"] = len(progress.evolution.get("exposures", []))
    state["catalogue"] = [
        {"id": k, "name": v.get("name", k), "instruction": v["instruction"]}
        for k, v in TEMPLATES.items()
    ]
    state["proposal_observations"] = [
        {
            "id": o.observation_id,
            "objective_id": o.objective_id,
            "result": o.result,
            "assistance": o.assistance,
        }
        for o in rows(progress)[-12:]
    ]
    state["limitations"] = [
        "这是有边界的教学策略改进，不训练陪学大模型权重，也不修改诊断或评分规则。",
        "只使用本人当前项目的数据；结构检查通过不代表教学效果已证实。",
        "启用需可比的独立后测与至少一天后的保持证据，模型评分仅作探索观察。",
    ]
    return state


def edit_profile(store, project, fields, revision):
    from deeptutor.learning.models import LearnerProfile

    allowed = {"prior_knowledge", "target_level", "time_budget", "preferences"}
    if not set(fields) <= allowed or any(
        not isinstance(v, str) or len(v) > 2000 for v in fields.values()
    ):
        raise ValueError("请填写2000字以内的学习信息")

    def change(tx):
        if tx.progress.version != revision:
            raise RuntimeError("学习记录已变化，请刷新画像后再保存")
        profile = tx.progress.learner_profile or LearnerProfile()
        changed = {k: v.strip() for k, v in fields.items() if getattr(profile, k) != v.strip()}
        if changed:
            if "target_level" in changed:
                tx.progress.support_goal_updated_at = time.time()
                tx.progress.goal_needs_confirmation = True
            tx.progress.learner_profile = profile.model_copy(
                update={**changed, "updated_at": time.time()}
            )
            tx.emit(
                "learner.profile_ui_updated", {"fields": list(changed), "basis": "learner_report"}
            )

    store.mutate(project, change)
    return portrait(store, project)


def agent_context(progress):
    from deeptutor.learning.policy import next_objective

    objective = next_objective(progress).knowledge_point_id
    if progress.pending_question:
        objective = progress.pending_question.knowledge_point_id
    strategy = strategy_view(progress)
    strategy["history"] = strategy.get("history", [])[-6:]
    if strategy.get("evaluation"):
        for arm in ("baseline", "candidate"):
            strategy["evaluation"][arm]["sources"] = strategy["evaluation"][arm].get("sources", [])[
                -6:
            ]
    return {
        "goal_contract": goal_view(progress),
        "self_report": progress.learner_profile.model_dump(mode="json")
        if progress.learner_profile
        else {},
        "support": support_plan(progress, objective) if objective else None,
        "experiment": strategy,
        "instructions": "A goal proposal is not confirmation. If goal_contract needs confirmation, discuss the target and use mastery_goal_propose if helpful, then ask the learner to confirm in the goal panel. Never confirm it on their behalf. Self-reports are not performance facts. A stored teaching_strategy may be the default, not an explicit learner choice; do not attribute a choice without learner evidence. Speak in the learner language using plain words, not internal codes such as probe_first or tool IDs. Explain why you maintain/change support using actual observation IDs. Use mastery_support_plan to persist a chosen future-task support change; never claim it was saved without a receipt. Respect learner control and restore support on request; supply learner_request_quote quoting their current request so it is protected as their choice. A support plan never changes help already delivered or a grade. For consented v2 experiments you may propose one bounded catalogue strategy with mastery_strategy_candidate when enough observations exist; retaining the current policy is valid. After actually delivering the selected strategy publicly, call mastery_strategy_delivery with an exact quote BEFORE posing the next quiz card (posing a card ends the turn). Allocation is not delivery. Observe fresh independent and delayed outcomes; never change scoring, model weights, access rules or sources. Semantic feedback is exploratory only, not promotion evidence. Do not interrupt a pending independent check or delayed-review schedule merely to run an experiment.",
    }
