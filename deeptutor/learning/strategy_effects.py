"""Personal, project-scoped strategy experiments over observed delivery episodes.

This protocol distinguishes assigned policy, actual public delivery, and fresh
independent outcomes. Semantic scores remain exploratory, not promotion evidence.
"""

from __future__ import annotations

import time
import uuid

from deeptutor.learning.diagnosis import _digest, project_facts

PROTOCOL = "observed-delivery-v2"


def rows(progress, *, verified=False):
    points = {k.id: k for m in progress.modules for k in m.knowledge_points}
    return sorted(
        [
            o
            for o in project_facts(progress).observations
            if o.validity == "recorded"
            and o.response_status == "answered"
            and o.objective_id in points
            and o.context
            and o.context.task_version
            and o.context.objective_scope == _digest(points[o.objective_id].model_dump(mode="json"))
            and (not verified or o.context.grading_method == "deterministic")
        ],
        key=lambda o: o.occurred_at,
    )


def experiment_rows(progress):
    from deeptutor.learning.goal_contract import assessment_version

    version = assessment_version(progress)
    return [o for o in rows(progress) if o.context.goal_version == version]


def episodes(progress, *, verified=True):
    exposures = sorted(progress.evolution.get("exposures", []), key=lambda e: e["at"])
    result = {e["id"]: {**e, "immediate": None, "delayed": None} for e in exposures}
    for row in rows(progress, verified=verified):
        if row.assistance != "independent" or row.ability not in {
            "application",
            "transfer",
            "explanation",
        }:
            continue
        candidates = [
            e
            for e in exposures
            if e["objective_id"] == row.objective_id and e["at"] < row.occurred_at
        ]
        if not candidates:
            continue
        e = candidates[-1]  # Never attribute one answer to two interventions.
        if (
            e.get("goal_version", 0) != row.context.goal_version
            or e["objective_scope"] != row.context.objective_scope
            or row.question_id == e["question_id"]
            or row.context.task_version == e["task_version"]
        ):
            continue
        delta = row.occurred_at - e["at"]
        if delta > 14 * 86400:
            continue
        slot = "immediate" if delta < 86400 else "delayed"
        if slot == "delayed" and row.ability not in {"application", "transfer"}:
            continue
        if result[e["id"]][slot] is None:
            result[e["id"]][slot] = row
    return list(result.values())


def metrics(progress, version, *, since=0, comparison_since=None):
    from deeptutor.learning.evolution import _interval

    all_episodes = episodes(progress)
    state = progress.evolution
    peer_versions = {state.get("active", {}).get("version", "baseline")}
    for key in ("candidate", "previous"):
        if state.get(key):
            peer_versions.add(state[key]["version"])
    peer_versions.discard(version)

    def stratum(e, slot):
        o = e[slot]
        return (
            (
                o.objective_id,
                o.context.question_type,
                o.ability,
                getattr(o.context, "goal_version", 0),
            )
            if o
            else None
        )

    selected = [e for e in all_episodes if e["version"] == version and e["at"] >= since]
    comparison_since = since if comparison_since is None else comparison_since
    peers = [
        e for e in all_episodes if e["version"] in peer_versions and e["at"] >= comparison_since
    ]
    out = {"delivered_n": len(selected), "protocol": PROTOCOL}
    for slot, prefix in (("immediate", "independent"), ("delayed", "delayed")):
        from collections import defaultdict
        from statistics import NormalDist

        own, other = defaultdict(list), defaultdict(list)
        for e in selected:
            if e[slot]:
                own[stratum(e, slot)].append(e[slot])
        for e in peers:
            if e[slot]:
                other[stratum(e, slot)].append(e[slot])
        keys = sorted(own.keys() & other.keys())
        observed = [e[slot] for e in selected if e[slot]]
        comparable = [o for key in keys for o in own[key]]
        # Equal predeclared weights prevent task-mix changes reversing the effect.
        # Bonferroni-adjusted bounds cover strata, two arms and two outcomes.
        # Repeated observational looks still do not establish a causal effect.
        z = NormalDist().inv_cdf(1 - 0.05 / (8 * len(keys))) if keys else 1.96
        strata = []
        for key in keys:
            n = len(own[key])
            successes = sum(o.result == "correct" for o in own[key])
            strata.append(
                {
                    "key": list(key),
                    "n": n,
                    "peer_n": len(other[key]),
                    "successes": successes,
                    "rate": successes / n,
                    "interval": list(_interval(successes, n, z=z)),
                }
            )
        interval = (
            [sum(s["interval"][i] for s in strata) / len(strata) for i in (0, 1)]
            if strata
            else [0.0, 1.0]
        )
        out.update(
            {
                prefix + "_n": len(comparable),
                prefix + "_successes": sum(s["successes"] for s in strata),
                prefix + "_interval": interval,
                prefix + "_rate": sum(s["rate"] for s in strata) / len(strata) if strata else None,
                prefix + "_strata": strata,
                prefix + "_comparable": bool(strata)
                and set(own) == set(other)
                and all(s["n"] >= 12 and s["peer_n"] >= 12 for s in strata),
                prefix + "_observed_n": len(observed),
                prefix + "_unmatched_n": len(observed) - len(comparable),
            }
        )
    out["exploratory_n"] = sum(
        bool(e["immediate"])
        for e in episodes(progress, verified=False)
        if e["version"] == version and e["at"] >= since
    ) - sum(bool(e["immediate"]) for e in selected)
    out["sources"] = [
        {
            "episode_id": e["id"],
            "session_id": e["session_id"],
            "turn_id": e["turn_id"],
            "question_id": e["question_id"],
            "outcomes": [
                {
                    "observation_id": e[s].observation_id,
                    "question_id": e[s].question_id,
                    "session_id": e[s].session_id,
                    "turn_id": e[s].turn_id,
                    "phase": s,
                    "result": e[s].result,
                }
                for s in ("immediate", "delayed")
                if e[s]
            ],
        }
        for e in selected
    ][-40:]
    return out


def update(progress, *, now=None, repair=False):
    state = progress.evolution
    if not state.get("consent"):
        return
    moment = time.time() if now is None else now
    active = state.setdefault("active", {"version": "baseline", "template": "reason_first"})
    candidate = state.get("candidate")
    if not candidate:
        state["status"] = (
            "awaiting_agent_proposal"
            if len(experiment_rows(progress))
            >= state.get("last_candidate_observation_count", 0) + 6
            else "collecting_evidence"
        )
        return
    if candidate["status"] not in {"trial", "promoted"}:
        count = candidate.setdefault("closed_observation_count", len(experiment_rows(progress)))
        if len(experiment_rows(progress)) >= count + 6:
            state["last_candidate_observation_count"] = count
            state.pop("candidate", None)
            state["status"] = "awaiting_agent_proposal"
        return
    from deeptutor.learning.evolution import CONSTRAINT_HASH, regression_check

    check = regression_check(candidate.get("template", ""))
    if (
        not check["passed"]
        or not candidate.get("regression", {}).get("passed")
        or candidate.get("regression", {}).get("constraint_hash") != CONSTRAINT_HASH
        or candidate.get("regression", {}).get("template_hash") != check["template_hash"]
    ):
        if candidate["status"] == "promoted" and state.get("previous"):
            state["active"] = dict(state["previous"])
        candidate["status"] = "rolled_back" if candidate["status"] == "promoted" else "rejected"
        state["status"] = candidate["status"]
        state.setdefault("history", []).append(
            {
                "event": "constraint_revalidation_failed",
                "at": moment,
                "version": candidate["version"],
            }
        )
        return
    since = candidate["created_at"]
    control = state.get("previous", active) if candidate["status"] == "promoted" else active
    baseline = metrics(progress, control["version"], since=since)
    treatment = metrics(progress, candidate["version"], since=since)
    state["evaluation"] = {
        "baseline": baseline,
        "candidate": treatment,
        "causal_claim": False,
        "limitations": [
            "仅本人当前项目；按相同知识点、题型和能力分层等权比较，每层双方至少12条。",
            "模型评分仅作探索观察；观看、聊天和受助答对不算独立后测。",
            "小样本与学习先后顺序仍可能混杂，区间不等于因果证明。",
        ],
    }
    enough = all(
        m["independent_comparable"] and m["delayed_comparable"] for m in (baseline, treatment)
    )
    better = (
        enough
        and treatment["independent_interval"][0] > baseline["independent_interval"][1]
        and treatment["delayed_interval"][0] >= baseline["delayed_interval"][1]
    )
    if candidate["status"] == "promoted":
        if repair and not better:
            state["active"] = state["previous"]
            candidate["status"] = "rolled_back"
            state["status"] = "rolled_back"
            state.setdefault("history", []).append(
                {"event": "evidence_revalidation_rollback", "at": moment}
            )
        else:
            recent = metrics(
                progress, candidate["version"], since=state["promoted_at"], comparison_since=since
            )
            old = state.get("promotion_evaluation", {}).get("candidate", treatment)
            if (
                recent["independent_comparable"]
                and recent["delayed_comparable"]
                and (
                    recent["independent_interval"][1] < old["independent_interval"][0]
                    or recent["delayed_interval"][1] < old["delayed_interval"][0]
                )
            ):
                state["active"] = state["previous"]
                candidate["status"] = "rolled_back"
                state["status"] = "rolled_back"
                state.setdefault("history", []).append(
                    {"event": "automatic_rollback", "at": moment}
                )
            elif (
                recent["independent_comparable"]
                and recent["delayed_comparable"]
                and recent["independent_n"] >= 24
                and recent["delayed_n"] >= 24
            ):
                state.setdefault("history", []).append(
                    {"event": "monitoring_completed", "at": moment, "version": candidate["version"]}
                )
                state["last_candidate_observation_count"] = len(experiment_rows(progress))
                state.pop("candidate", None)
                state["status"] = "collecting_evidence"
        return
    if not enough:
        state["status"] = "trial_insufficient_evidence"
        return
    if better:
        import copy

        state["previous"] = dict(active)
        state["active"] = {
            "version": candidate["version"],
            "template": candidate["template"],
            "regression": dict(candidate["regression"]),
        }
        candidate["status"] = "promoted"
        state.update(
            status="monitoring",
            promoted_at=moment,
            promotion_evaluation=copy.deepcopy(state["evaluation"]),
        )
        state.setdefault("history", []).append(
            {"event": "promoted", "at": moment, "version": candidate["version"]}
        )
    elif (
        treatment["independent_interval"][1] < baseline["independent_interval"][0]
        or treatment["delayed_interval"][1] < baseline["delayed_interval"][0]
    ):
        candidate["status"] = "rejected"
        state["status"] = "rejected"
        state.setdefault("history", []).append(
            {"event": "candidate_rejected", "at": moment, "version": candidate["version"]}
        )


def propose(store, project, template, hypothesis, observation_ids, *, session_id="", turn_id=""):
    from deeptutor.learning.evolution import TEMPLATES, regression_check

    if (
        not isinstance(observation_ids, list)
        or not 1 <= len(observation_ids) <= 10
        or any(not isinstance(i, str) or not i for i in observation_ids)
    ):
        raise ValueError("请引用1至10条真实观察记录")
    if template not in TEMPLATES or not 8 <= len(hypothesis.strip()) <= 600:
        raise ValueError("请选择已定义的教学动作并说明可检验的改进假设")
    if not regression_check(template)["passed"]:
        raise ValueError("教学模板或约束已变化，尚未通过准入检查")

    def change(tx):
        state = tx.progress.evolution
        if not state.get("consent") or state.get("protocol") != PROTOCOL:
            raise ValueError("学生尚未开启当前协议的策略试用")
        existing = state.get("candidate")
        if existing:
            if existing.get("proposal_turn_id") == turn_id and turn_id:
                return existing
            raise ValueError("当前已有候选策略，请先完成比较或停止本次试用")
        valid = {o.observation_id for o in experiment_rows(tx.progress)}
        if (
            len(valid) < state.get("last_candidate_observation_count", 0) + 6
            or not observation_ids
            or not set(observation_ids) <= valid
        ):
            raise ValueError(
                "请积累至少6条有效表现，并引用真实观察记录；不能用聊天或AI学生发言凑数"
            )
        if state.get("active", {}).get("template", "reason_first") == template:
            raise ValueError("候选动作应与当前策略不同；也可选择保持现有策略")
        candidate = {
            "version": "candidate-v2-" + uuid.uuid4().hex[:10],
            "template": template,
            "status": "trial",
            "created_at": time.time(),
            "hypothesis": hypothesis.strip(),
            "observation_ids": observation_ids,
            "allocation": 0.2,
            "regression": regression_check(template),
            "proposal_turn_id": turn_id,
        }
        state.update(candidate=candidate, status="trial")
        state.setdefault("history", []).append(
            {
                "event": "candidate_created",
                "at": time.time(),
                "version": candidate["version"],
                "hypothesis": candidate["hypothesis"],
            }
        )
        tx.emit("strategy.candidate_proposed", candidate, session_id=session_id, turn_id=turn_id)
        return candidate

    return store.mutate(project, change)[1]


def record_delivery(store, project, objective_id, template, quote, delivery):
    from deeptutor.learning.evolution import policy_for_next
    from deeptutor.learning.goal_contract import assessment_version

    def change(tx):
        state = tx.progress.evolution
        if not state.get("consent") or state.get("protocol") != PROTOCOL:
            raise ValueError("当前未开启策略试用")
        pending = tx.progress.pending_question
        question = (
            next(
                (
                    q
                    for q in (pending.questions or [pending])
                    if q.knowledge_point_id == objective_id
                ),
                None,
            )
            if pending
            else None
        )
        point = next(
            (k for m in tx.progress.modules for k in m.knowledge_points if k.id == objective_id),
            None,
        )
        if point is None:
            raise ValueError("知识点已变化")
        assignment = (
            question.strategy_version if question else policy_for_next(tx.progress)["version"]
        )
        selected = next(
            (
                p
                for p in (
                    state.get("active", {"version": "baseline", "template": "reason_first"}),
                    state.get("candidate") or {},
                )
                if p.get("version") == assignment
            ),
            None,
        )
        if not selected or selected["template"] != template:
            raise ValueError("实际动作与分配策略不同，不纳入本次比较；可按学生需要继续教学")
        anchor_id = question.question_id if question else ""
        exposures = state.setdefault("exposures", [])
        if any(
            e["question_id"] == anchor_id and e["turn_id"] == delivery["turn_id"] for e in exposures
        ):
            return
        if len(exposures) >= 500:
            raise ValueError("本项目策略交付记录已达上限，请停止试用并回顾结果")
        from deeptutor.learning.diagnosis import assessment_context

        context = (
            assessment_context(
                tx.progress, question, question.expected_answer, question.question_type
            )
            if question
            else None
        )
        exposure = {
            "id": uuid.uuid4().hex,
            "objective_id": objective_id,
            "objective_scope": _digest(point.model_dump(mode="json")),
            "goal_version": assessment_version(tx.progress),
            "question_id": anchor_id,
            "task_version": context.task_version if context else _digest(quote),
            "version": assignment,
            "template": template,
            "at": time.time(),
            "quote": quote,
            "session_id": delivery["session_id"],
            "turn_id": delivery["turn_id"],
            "delivery": delivery,
        }
        exposures.append(exposure)
        tx.emit(
            "strategy.delivered",
            exposure,
            session_id=delivery["session_id"],
            turn_id=delivery["turn_id"],
        )

    store.mutate(project, change)
