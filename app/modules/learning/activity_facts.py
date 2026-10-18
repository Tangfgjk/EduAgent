from sqlalchemy import select

from app.infrastructure.db.models import AnswerReview, LearningFact
from app.modules.learning.activity_catalog import activity_for_version
from app.modules.memory.projections import rebuild


def publish_activity(session, run, entry, state):
    if entry.payload.get("action") not in {"contribute", "refine", "conclude", "verify"}:
        return
    if session.scalar(select(LearningFact.id).where(LearningFact.input_id == entry.id)):
        return
    item = activity_for_version(state["view"]["activity_version"], session)
    verification = entry.payload.get('action') == 'verify'
    independent = bool(verification and state['view']['verification_check']['independent'])
    task = item.verification_task if verification else item.task
    check = state['view']['verification_check'] if verification else state["view"]["check"]
    # 只收学生原始贡献；模板发言不进入 L1，不计入数值正确率或独立检测。
    fact = LearningFact(workspace_id=run.workspace_id, run_id=run.id, input_id=entry.id,
        kind="activity_contribution", source_kind=entry.source_kind, task_id=task["id"],
        content_version=item.parent.version, created_at=entry.created_at,
        payload={"stage": run.kind, "status": check["status"] if check else None,
                 "hints_used": 0, "task_snapshot": task, "turn": state["turn"],
                 "activity_version": item.version, "action": entry.payload["action"],
                 "independent": independent, "reasoning_reviewed": False})
    session.add(fact)
    if check and check["status"] == "unverifiable":
        session.add(AnswerReview(input_id=entry.id, run_id=run.id, task_id=task["id"],
            content_version=item.parent.version, task_snapshot=task))
    rebuild(session, run.workspace_id)
