"""Evidence-linked independent checks, delayed review and transfer orchestration.

Uses the existing tutor question/assessment path. Never grades chat, grants mastery,
or changes BKT parameters. All state is owned by the project's learning transaction.
"""

from datetime import datetime, timezone
import hashlib
import time
import uuid

from deeptutor.learning.diagnosis import project_facts
from deeptutor.learning.review_strategy import scheduler_for_progress

STAGES = {"initial": "独立核查", "review": "延迟复习", "transfer": "迁移验证"}


def objective_scope(kp, progress=None):
    from deeptutor.learning.goal_contract import assessment_version

    version = assessment_version(progress) if progress else 0
    payload = kp.model_dump_json() + (f":goal:{version}" if version else "")
    return hashlib.sha256(payload.encode()).hexdigest()


def wait_interval(progress, kp, now):
    scheduled = scheduler_for_progress(progress).get_initial_state(kp.type, now=now)
    return max(86400, scheduled.next_review_at - now)


def state_for(progress, kp, *, now=None):
    now = time.time() if now is None else now
    cycle = progress.check_cycles.get(kp.id, {})
    from deeptutor.learning.goal_contract import active as active_goal
    from deeptutor.learning.goal_contract import qualifying_rows, task_kind

    scope = objective_scope(kp, progress)
    goal = active_goal(progress)
    changed = bool(cycle and cycle.get("scope") != scope)
    rounds = [] if changed else cycle.get("rounds", [])
    all_observations = project_facts(progress).observations
    if goal:
        valid = {o.observation_id for o in qualifying_rows(progress, kp, all_observations)}
        all_observations = [
            o for o in all_observations if o.observation_id in valid or o.validity != "recorded"
        ]
    observations = {o.question_id: o for o in all_observations if o.validity == "recorded"}
    phase, due, completed, evidence, used = "initial", 0, 0, [], set()
    active, feedback, result, invalidated = None, "", "", changed
    for r in rounds:
        if r["phase"] != phase or r["started"] < due:
            continue
        rows = [
            observations[q] for q in r.get("questions", []) if q in observations and q not in used
        ]
        rows = [
            o
            for o in rows
            if o.objective_id == kp.id
            and o.context
            and o.context.task_version
            and o.context.objective_scope
            and o.ability == task_kind(progress, phase)
            and o.occurred_at >= r["started"]
        ]
        invalid = any(
            o.question_id in r.get("questions", []) and o.validity != "recorded"
            for o in all_observations
        )
        if invalid:
            invalidated = True
            active = None
            feedback = "本轮部分证据已作废或发生冲突，请换一个新任务核查。"
            continue
        if not rows or len(rows) < len(r.get("questions", [])):
            active = r
            continue
        row = max(rows, key=lambda o: o.occurred_at)
        used.update(o.question_id for o in rows)
        active = None
        feedbacks = []
        independent = all(o.assistance == "independent" for o in rows)
        result = (
            "incorrect"
            if any(o.result == "incorrect" for o in rows)
            else "partial"
            if any(o.result != "correct" for o in rows)
            else "correct"
        )
        for receipt in rows:
            message = next(
                (
                    e.feedback
                    for e in progress.learning_evidence
                    if e.question_id == receipt.question_id
                ),
                "",
            )
            if message:
                feedbacks.append(message)
            evidence.append(
                {
                    "question_id": receipt.question_id,
                    "observation_id": receipt.observation_id,
                    "phase": phase,
                    "result": receipt.result,
                    "independent": receipt.assistance == "independent",
                    "session_id": receipt.session_id,
                    "turn_id": receipt.turn_id,
                    "source_refs": receipt.source_refs,
                    "at": receipt.occurred_at,
                    "feedback": message,
                    "model_feedback": receipt.context.grading_method == "semantic",
                }
            )
        feedback = " ".join(feedbacks)
        if any(o.result != "correct" for o in rows) or not independent:
            feedback = (
                "本次用过帮助，请先巩固，再换一道新题独立核查。"
                if not independent
                else "先回顾不确定的步骤，再用新任务核查。"
            ) + feedback
            continue
        if phase == "initial":
            phase, due = "review", row.occurred_at + r["interval"]
        elif phase == "review" and (not goal or goal.level == "transfer"):
            phase, due = "transfer", 0
        else:
            completed += 1
            phase, due = "review", row.occurred_at + r["interval"]
    status = "in_progress" if active else "waiting" if due > now else "ready"
    return {
        "objective_id": kp.id,
        "objective_name": kp.name,
        "module_id": kp.module_id,
        "phase": phase,
        "phase_label": STAGES[phase],
        "status": status,
        "due_at": due or None,
        "completed_cycles": completed,
        "active_round": active,
        "evidence": evidence,
        "feedback": feedback,
        "latest_result": result,
        "scope_changed": changed,
        "evidence_invalidated": invalidated,
        "goal_version": goal.assessment_version if goal else 0,
        "required_evidence_kind": task_kind(progress, phase),
        "next_action": "继续当前任务" if active else "到期后复习" if due > now else STAGES[phase],
    }


def snapshot(store, project, *, now=None):
    p = store.load(project)
    if p is None:
        raise LookupError("项目不存在")
    rows = [
        state_for(p, k, now=now)
        for m in sorted(p.modules, key=lambda m: m.order)
        for k in m.knowledge_points
    ]
    return {
        "project_id": project,
        "objectives": rows,
        "due_count": sum(r["phase"] == "review" and r["status"] == "ready" for r in rows),
        "rules": [
            "观看或阅读不计为通过；使用已有作答证据，不另外生成成绩。",
            "模型反馈保留来源及其局限，不直接成为已校准诊断或独立掌握结论。",
            "到期前可以继续学习；提前练习不代替延迟复习。",
        ],
    }


def start(store, project, objective_id, *, now=None):
    moment = time.time() if now is None else now

    def change(tx):
        kp = next(
            (k for m in tx.progress.modules for k in m.knowledge_points if k.id == objective_id),
            None,
        )
        if kp is None:
            raise ValueError("知识点不存在或已调整，请刷新目录")
        pending = tx.active_interaction()
        if pending:
            return {
                "pending": True,
                "session_id": pending.session_id,
                "question_id": pending.question.question_id,
                "message": "请先接续当前未完成的题目；不会用新核查覆盖它。",
            }
        state = state_for(tx.progress, kp, now=moment)
        if state["status"] == "waiting":
            raise ValueError("复习时间尚未到；可先阅读或陪学，提前练习不能代替延迟核查")
        cycle = tx.progress.check_cycles.get(kp.id)
        if not cycle or cycle["scope"] != objective_scope(kp, tx.progress):
            if cycle:
                tx.emit("check.scope_changed", {"objective_id": kp.id, "previous": cycle})
            cycle = {"scope": objective_scope(kp, tx.progress), "rounds": []}
            tx.progress.check_cycles[kp.id] = cycle
        r = state["active_round"]
        if not r:
            if len(cycle["rounds"]) >= 200:
                raise ValueError("本知识点核查记录已达上限，请整理学习项目")
            r = {
                "id": uuid.uuid4().hex,
                "phase": state["phase"],
                "started": moment,
                "questions": [],
                "interval": wait_interval(tx.progress, kp, moment),
            }
            cycle["rounds"].append(r)
            tx.emit(
                "check.started", {"objective_id": kp.id, "round_id": r["id"], "phase": r["phase"]}
            )
        tx.progress.check_intent = {"objective_id": kp.id, "round_id": r["id"]}
        tx.touch()
        seen = [
            o.question_id
            for o in project_facts(tx.progress).observations
            if o.objective_id == kp.id
        ]
        prompt = (
            f"我想对「{kp.name}」做一次{STAGES[r['phase']]}。"
            "请给我一道没做过的新任务，先让我独立尝试，不要提前给答案或提示。"
        )
        return {
            "pending": False,
            "round_id": r["id"],
            "phase": r["phase"],
            "session_id": "",
            "prompt": prompt,
            "avoid_question_ids": seen[-30:],
        }

    _, result = store.mutate(project, change)
    return result


def bind(tx, questions, *, session_id="", turn_id=""):
    intent = tx.progress.check_intent
    if not intent:
        return
    kp = next(
        (
            k
            for m in tx.progress.modules
            for k in m.knowledge_points
            if k.id == intent.get("objective_id")
        ),
        None,
    )
    if not kp:
        return
    state = state_for(tx.progress, kp)
    r = state["active_round"]
    if not r and turn_id and any(e["turn_id"] == turn_id for e in state["evidence"]):
        raise ValueError(
            "本轮核查已完成，请先总结反馈和下一步复习时间。本轮不要自动追加题目；后续可由学生主动继续练习。"
        )
    if not r or r["id"] != intent.get("round_id"):
        return
    from deeptutor.learning.goal_contract import task_kind

    kind = task_kind(tx.progress, r["phase"])
    bound = []
    for q in questions:
        if (
            q.knowledge_point_id == kp.id
            and q.evidence_kind == kind
            and q.question_id not in r["questions"]
        ):
            if any(
                o.question_id == q.question_id
                or o.context
                and o.context.task_prompt.strip() == q.prompt.strip()
                for o in project_facts(tx.progress).observations
            ):
                raise ValueError("核查必须使用未作答过的新题")
            r["questions"].append(q.question_id)
            bound.append(q.question_id)
    if bound:
        r["session_id"] = session_id
        tx.emit(
            "check.question_registered",
            {"round_id": r["id"], "question_ids": bound},
            session_id=session_id,
        )


def withdraw(tx, question, *, reason):
    """Keep skipped/replaced tasks for audit without blocking a fresh check."""
    if question is None:
        return
    ids = {q.question_id for q in question.questions or [question]}
    for cycle in tx.progress.check_cycles.values():
        for round_ in cycle["rounds"]:
            removed = ids.intersection(round_["questions"])
            if removed:
                round_.setdefault("withdrawn", []).append(
                    {"question_ids": sorted(removed), "reason": reason, "at": time.time()}
                )
                round_["questions"] = [q for q in round_["questions"] if q not in removed]
                tx.emit(
                    "check.task_withdrawn",
                    {"round_id": round_["id"], "question_ids": sorted(removed), "reason": reason},
                )


def handoff(progress):
    intent = progress.check_intent
    if not intent:
        return None
    kp = next(
        (
            k
            for m in progress.modules
            for k in m.knowledge_points
            if k.id == intent.get("objective_id")
        ),
        None,
    )
    if not kp:
        return None
    s = state_for(progress, kp)
    active = s["active_round"]
    current_round = next(
        (
            r
            for r in progress.check_cycles.get(kp.id, {}).get("rounds", [])
            if r["id"] == intent.get("round_id")
        ),
        {},
    )
    return {
        "objective_id": kp.id,
        "objective": kp.name,
        "phase": s["phase"],
        "status": s["status"],
        "due_at": s["due_at"],
        "due_at_iso": datetime.fromtimestamp(s["due_at"], timezone.utc).isoformat()
        if s["due_at"]
        else None,
        "closing_turn_id": s["evidence"][-1]["turn_id"] if not active and s["evidence"] else None,
        "round_id": active["id"] if active else None,
        "question_ids": current_round.get("questions", []),
        "required_evidence_kind": s["required_evidence_kind"] if active else None,
        "latest_feedback": s["feedback"],
        "rules": (
            "For an active check, register one NEW task for this objective with required_evidence_kind, "
            "withhold answers and hints, wait for the learner, and grade using existing tools. "
            "Do not repeat worked examples shown in sources or PPTs. Transfer must change the context "
            "or application conditions, not just numbers. Record every use of help. "
            "After grading, summarize the feedback and current phase; do not automatically pose another task. "
            "If status is waiting, explain the due date and end the check. Offer optional reading or practice, "
            "but never count early practice as delayed review or skip to transfer. "
            "Never infer mastery from viewing, self-report, or a single model verdict."
        ),
    }
