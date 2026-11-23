"""One goal contract for task selection, evidence, progress and support decisions.

Models can propose. Only a learner-facing action confirms the contract. Neither
operation rewrites attempts, unanswered tasks, diagnostic parameters or grades.
"""

import time
import uuid

from deeptutor.learning.goal_models import GoalContract, GoalProposal

LEVELS = {
    "understand": {
        "label": "理解概念",
        "abilities": ["recognition", "explanation"],
        "description": "能辨认关键条件，并用自己的话解释。",
    },
    "apply": {
        "label": "独立应用",
        "abilities": ["explanation", "application"],
        "description": "能说明方法，并独立完成相应任务。",
    },
    "transfer": {
        "label": "迁移解决问题",
        "abilities": ["application", "transfer"],
        "description": "能独立应用，并在改变情境或条件后解决问题。",
    },
}
RULE_VERSION = "learner-goal-v1"


def active(progress):
    return None if progress.goal_needs_confirmation else progress.goal_contract


def assessment_version(progress):
    goal = active(progress)
    return goal.assessment_version if goal else 0


def criteria(level):
    item = LEVELS[level]
    return {
        **item,
        "level": level,
        "minimum_distinct_tasks": 2,
        "independent": True,
        "delayed_transfer_required": level == "transfer",
        "rules": [
            "每个知识点至少两个不同的新任务，覆盖所选目标的能力。",
            "用过帮助的作答仍会保留，需另做独立任务核查。",
            "观看资料、自述和诊断概率不代替作答表现。",
        ],
    }


def view(progress):
    goal = active(progress)
    proposal = progress.goal_proposal
    profile = progress.learner_profile
    return {
        "suggested_time_budget": profile.time_budget if profile else "",
        "source_revision": progress.version,
        "status": "confirmed" if goal else "needs_confirmation",
        "current": progress.goal_contract.model_dump(mode="json")
        if progress.goal_contract
        else None,
        "proposal": proposal.model_dump(mode="json") if proposal else None,
        "suggested_summary": proposal.summary
        if proposal
        else profile.target_level
        if profile
        else progress.name
        if not progress.goal_needs_confirmation
        else "",
        "criteria": criteria(goal.level) if goal else None,
        "levels": [criteria(level) for level in LEVELS],
        "history": [g.model_dump(mode="json") for g in progress.goal_history],
        "rule_version": RULE_VERSION,
        "message": "目标可随时修改；修改验收目标后用新任务复核，过去的学习记录会保留。"
        if goal
        else "先确认希望学到的程度；可以继续学习，暂不自动宣称达标或减少支持。",
    }


def load(store, project):
    p = store.load(project)
    if p is None:
        raise LookupError("学习项目不存在")
    data = view(p)
    from deeptutor.learning.diagnosis import project_facts
    from deeptutor.learning.wenjin import objective_evidence

    observations = project_facts(p).observations
    results = [
        objective_evidence(p, k, observations=observations)
        for m in p.modules
        for k in m.knowledge_points
    ]
    data["progress"] = {
        "total": len(results),
        "demonstrated": sum(r["goal_achieved"] for r in results),
        "model_feedback_used": any(r["model_feedback_used"] for r in results),
    }
    return data


def confirm(store, project, *, revision, level, summary, time_budget=None, reason="", request_id):
    from deeptutor.learning.models import LearnerProfile

    summary, reason = summary.strip(), reason.strip()
    provided_budget = time_budget.strip() if time_budget is not None else None
    if (
        level not in LEVELS
        or not 1 <= len(summary) <= 1500
        or len(provided_budget or "") > 2000
        or len(reason) > 600
        or not 8 <= len(request_id) <= 128
    ):
        raise ValueError("请选择目标程度并填写有效的目标描述")

    def change(tx):
        p = tx.progress
        current = p.goal_contract
        time_budget = (
            provided_budget
            if provided_budget is not None
            else (
                current.time_budget
                if current
                else p.learner_profile.time_budget
                if p.learner_profile
                else ""
            )
        )
        if current and current.request_id == request_id:
            if (current.level, current.summary, current.time_budget, current.reason) != (
                level,
                summary,
                time_budget,
                reason,
            ):
                raise ValueError("请求编号已用于不同的目标内容")
            return
        if p.version != revision:
            raise RuntimeError("学习记录已更新，请刷新目标后再确认")
        if len(p.goal_history) >= 200:
            raise ValueError("目标修改记录已达上限，请整理项目")
        changed = (
            not current
            or p.goal_needs_confirmation
            or (current.level, current.summary) != (level, summary)
        )
        moment = time.time()
        contract = GoalContract(
            version=current.version + 1 if current else 1,
            assessment_version=(current.assessment_version + int(changed)) if current else 1,
            level=level,
            summary=summary,
            time_budget=time_budget,
            reason=reason,
            confirmed_at=moment,
            evidence_since=moment if changed else current.evidence_since,
            request_id=request_id,
        )
        p.wenjin_enabled = True
        p.goal_contract = contract
        p.goal_history.append(contract.model_copy(deep=True))
        p.goal_needs_confirmation = False
        p.goal_proposal = None
        profile = p.learner_profile or LearnerProfile()
        p.learner_profile = profile.model_copy(
            update={"target_level": summary, "time_budget": time_budget, "updated_at": moment}
        )
        if changed:
            p.support_goal_updated_at = moment
            if p.evolution:
                state = p.evolution
                previous = {
                    k: state[k] for k in ("active", "candidate", "evaluation") if k in state
                }
                state.setdefault("history", []).append(
                    {
                        "event": "goal_changed",
                        "at": moment,
                        "previous": previous,
                        "goal_version": contract.assessment_version,
                    }
                )
                for key in ("candidate", "evaluation", "promotion_evaluation", "previous"):
                    state.pop(key, None)
                state.update(
                    active={"version": "baseline", "template": "reason_first"},
                    status="collecting_evidence",
                    last_candidate_observation_count=0,
                )
                tx.emit(
                    "strategy.goal_changed",
                    {"previous": previous, "goal_version": contract.assessment_version},
                )
        tx.emit(
            "goal.confirmed",
            {
                "contract": contract.model_dump(mode="json"),
                "assessment_changed": changed,
                "authority": "learner",
            },
        )

    store.mutate(project, change)
    return load(store, project)


def propose(store, project, level, summary, reason, *, learner_quote="", session_id="", turn_id=""):
    if (
        level not in LEVELS
        or not 1 <= len(summary.strip()) <= 1500
        or not 1 <= len(reason.strip()) <= 600
    ):
        raise ValueError("请提出明确目标程度、描述和建议理由")

    def change(tx):
        p = tx.progress
        if p.goal_proposal and turn_id and p.goal_proposal.turn_id == turn_id:
            return
        p.goal_proposal = GoalProposal(
            id=uuid.uuid4().hex,
            level=level,
            summary=summary.strip(),
            reason=reason.strip(),
            learner_quote=learner_quote,
            session_id=session_id,
            turn_id=turn_id,
            created_at=time.time(),
        )
        tx.emit(
            "goal.proposed",
            {"proposal": p.goal_proposal.model_dump(mode="json"), "confirmed": False},
            session_id=session_id,
            turn_id=turn_id,
        )

    store.mutate(project, change)
    return load(store, project)


def task_kind(progress, phase):
    goal = active(progress)
    return (
        "transfer"
        if phase == "transfer"
        else "explanation"
        if goal and goal.level == "understand"
        else "application"
    )


def bind_task(progress, question):
    version = assessment_version(progress)
    for leaf in question.questions or [question]:
        leaf.goal_version = version
    question.goal_version = version


def qualifying_rows(progress, kp, observations):
    from deeptutor.learning.diagnosis import _digest

    goal = active(progress)
    if not goal:
        return []
    scope = _digest(kp.model_dump(mode="json"))
    return [
        o
        for o in observations
        if o.objective_id == kp.id
        and o.validity == "recorded"
        and o.context
        and o.context.objective_scope == scope
        and o.context.task_version
        and o.context.goal_version == goal.assessment_version
        and o.occurred_at >= goal.evidence_since
    ]
