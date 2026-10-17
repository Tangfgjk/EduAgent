from sqlalchemy import select

from app.infrastructure.db.models import Account, AgentRun, AnswerReview, AuditEvent, ContentApproval, LearningInput, Workspace, now
from app.modules.memory.facts import publish_judgment
from app.modules.errors import DomainError
from app.modules.learning.development_content import CONTENT_VERSION, TASKS, TITLE, UNIT_ID


def require_content(session, source_kind, unit_id=UNIT_ID, version=CONTENT_VERSION):
    approval = session.get(ContentApproval, unit_id, with_for_update=True)
    if source_kind == "real_student":
        raise DomainError(409, "development_content_only", "当前为开发题库，完成专业内容审核前暂不面向真实学生开放")
    if approval is None or not approval.enabled or approval.version != version:
        raise DomainError(409, "content_not_enabled", "请先由管理员核对并启用当前版本的开发内容")
    from app.modules.governance.reviews import check_content
    check_content(session, unit_id, version)


def require_activity_content(session, source_kind, unit_id=None):
    from app.modules.learning.activity_catalog import get_activity
    from app.modules.learning.activity_content import UNIT_ID as default_id
    item = get_activity(unit_id or default_id)
    require_content(session, source_kind, item.unit_id, item.version)
    if item.flow == 'teach_cycle_v1':
        require_content(session, source_kind, item.parent.unit_id, item.parent.version)


class Content:
    def __init__(self, unit_id=UNIT_ID, title=TITLE, version=CONTENT_VERSION, tasks=TASKS):
        self.unit_id, self.title, self.version, self.tasks = unit_id, title, version, tasks

    def view(self, session, admin=False):
        approval = session.get(ContentApproval, self.unit_id)
        return {"unit_id": self.unit_id, "title": self.title, "content_version": self.version,
            "enabled": bool(approval and approval.enabled and approval.version == self.version),
            "revision": approval.revision if approval else 0, "source": "self_authored_development",
            "professional_review": False, "allowed_sources": ["synthetic", "adult_test"],
            "tasks": self.tasks if admin else []}

    def toggle(self, session, actor, body):
        if body["content_version"] != self.version:
            raise DomainError(409, "version_conflict", "题库版本已更新，请重新核对内容")
        from app.modules.governance.reviews import check_content, content_lock
        approval = content_lock(session, actor, self.unit_id, self.version)
        if (approval.revision if approval else 0) != body["expected_revision"]:
            raise DomainError(409, "revision_conflict", "内容状态已更新，请刷新后核对")
        if body['enabled']:
            check_content(session, self.unit_id, self.version)
        if not approval:
            approval = ContentApproval(unit_id=self.unit_id, version=self.version, enabled=body["enabled"],
                revision=1, reviewer_id=actor.account.id)
            session.add(approval)
        else:
            approval.version, approval.enabled = self.version, body["enabled"]
            approval.revision += 1
            approval.reviewer_id, approval.reviewed_at = actor.account.id, now()
        session.add(AuditEvent(actor_id=actor.account.id, action="content.enable" if body["enabled"] else "content.disable", target_id=self.unit_id))
        session.flush()
        return self.view(session, admin=True)

    def review_out(self, session, row):
        entry = session.get(LearningInput, row.input_id)
        return {"review_id": row.id, "run_id": row.run_id, "workspace_id": entry.workspace_id,
            "source_kind": entry.source_kind, "task_id": row.task_id, "content_version": row.content_version,
            "task": row.task_snapshot,
            "answer": entry.payload["answer"], "reasoning": entry.payload.get("reasoning", ""),
            "status": row.status, "revision": row.revision, "conclusion": row.conclusion, "note": row.note}

    def reviews(self, session, offset, status):
        rows = session.scalars(select(AnswerReview).where(AnswerReview.status == status)
            .order_by(AnswerReview.created_at, AnswerReview.id).offset(offset).limit(20))
        return {"items": [self.review_out(session, row) for row in rows]}

    def resolve(self, session, actor, review_id, body):
        row = session.get(AnswerReview, review_id)
        if row is None:
            raise DomainError(404, "not_found", "未找到复核记录")
        entry = session.get(LearningInput, row.input_id)
        session.get(Workspace, entry.workspace_id, with_for_update=True)
        row = session.get(AnswerReview, review_id, with_for_update=True, populate_existing=True)
        if row.revision != body["expected_revision"] or row.status != "pending":
            raise DomainError(409, "revision_conflict", "此解答已被复核，请刷新后查看")
        row.status, row.conclusion, row.note = "resolved", body["conclusion"], body["note"]
        row.reviewer_id, row.revision = actor.account.id, row.revision + 1
        session.add(AuditEvent(actor_id=actor.account.id, action="answer.review", target_id=row.id))
        session.flush()
        publish_judgment(session, row, actor.account.id)
        return self.review_out(session, row)


def catalog_unit(session, item, admin=False):
    return {**Content(item.unit_id, item.title, item.version, item.tasks).view(session, admin),
        "grade": item.grade, "domain": item.domain, "knowledge_points": item.knowledge_points,
        "prerequisites": item.prerequisites, "task_count": len(item.tasks), "boundary": item.boundary}


def catalog(session, admin=False):
    from app.modules.learning.content_catalog import PACKS
    return {"items": [catalog_unit(session, item, admin) for item in PACKS],
        "coverage_note": f"当前有 {len(PACKS)} 个自编开发单元，共 {sum(len(p.tasks) for p in PACKS)} 题；不是七至九年级完整课程。新增概率计数、平均数与频率，新增内容须先保存开发审核依据再启用；全部未经专业教师审核，不面向真实学生。年级与先修关系仅供编排参考，不表示已掌握。圆桌与我教 AI 独立管理版本和开放。"}
