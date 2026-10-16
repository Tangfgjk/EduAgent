from sqlalchemy import func, select

from app.infrastructure.db.models import AgentRun, AnswerReview, AuditEvent, Conversation, LearningInput
from app.modules.errors import DomainError
from app.modules.learning.content_service import require_content
from app.modules.learning.content_catalog import get_pack
from app.modules.learning.activity_service import require_idle
from app.modules.planning.service import effective_policy
from app.modules.learning.service import Learning, event, request_hash
from app.modules.learning.tutor_graph import check_action, version_for


def public_tutor(session, run):
    view = dict(run.view) if run.view else None
    if view:
        view["pending_reviews"] = session.scalar(select(func.count()).select_from(AnswerReview).where(
            AnswerReview.run_id == run.id, AnswerReview.status == "pending"))
    return {"run_id": run.id, **{key: getattr(run, key) for key in (
        "workspace_id", "conversation_id", "status", "revision", "gate_id", "error_code", "event_seq", "created_at")}, "view": view}


class Tutoring(Learning):
    def owned(self, session, actor, wid, rid, active=False):
        return super().owned(session, actor, wid, rid, active=active, kind="tutor")

    def list(self, session, actor, wid, offset=0):
        self.spaces.owned(session, actor, wid)
        return {"items": [public_tutor(session, run) for run in session.scalars(select(AgentRun).where(
            AgentRun.workspace_id == wid, AgentRun.kind == "tutor").order_by(
            AgentRun.created_at.desc(), AgentRun.id.desc()).offset(offset).limit(20))]}

    def start(self, session, actor, wid, payload, key):
        workspace = self.spaces.owned(session, actor, wid)
        existing = session.scalar(select(AgentRun).where(AgentRun.workspace_id == wid, AgentRun.creation_key == key))
        if existing:
            if existing.kind != "tutor" or existing.creation_hash != request_hash(payload):
                raise DomainError(409, "idempotency_conflict", "该标识已用于其他请求")
            return public_tutor(session, existing)
        self.spaces.owned(session, actor, wid, active=True)
        item = get_pack(payload["unit_id"])
        require_content(session, actor.account.source_kind, item.unit_id, item.version)
        require_idle(session, wid, ["roundtable", "teach"])
        pending = session.scalar(select(AgentRun.id).where(AgentRun.workspace_id == wid, AgentRun.kind == "tutor",
            AgentRun.status.in_(["queued", "awaiting_input"])).limit(1))
        if pending:
            raise DomainError(409, "tutor_pending", "此空间已有未完成陪学，请先继续或取消")
        plan_id = None
        if workspace.kind == "project":
            plans = list(session.scalars(select(AgentRun).where(AgentRun.workspace_id == wid, AgentRun.kind == "plan")
                .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())))
            if not plans or any(run.status in {"queued", "awaiting_input"} for run in plans) or not any(run.status == "completed" for run in plans):
                raise DomainError(409, "plan_required", "请先在当前项目确认一份学习计划")
            plan_id = next(run.id for run in plans if run.status == "completed")
        conversation = self.spaces.create_conversation(session, actor, wid, "tutor")
        run = AgentRun(workspace_id=wid, conversation_id=conversation.id, creation_key=key,
            creation_hash=request_hash(payload), version=version_for(item.unit_id), kind="tutor")
        session.add(run)
        session.flush()
        strategy_id, strategy_policy, _ = effective_policy(session, wid)
        self.spaces.accept_input(session, actor, wid, conversation.id, "start:" + run.id, 1,
            {**payload, "run_id": run.id, "operation": "tutor_start", "plan_run_id": plan_id,
             "strategy_version_id": strategy_id, "reflection_focus": strategy_policy["reflection_focus"]})
        event(session, run, "run.queued")
        session.add(AuditEvent(actor_id=actor.account.id, action="tutor.start", target_id=run.id))
        session.flush()
        return public_tutor(session, run)

    def submit(self, session, actor, wid, rid, payload, key):
        run = self.owned(session, actor, wid, rid)
        operation = {**payload, "run_id": rid, "operation": "tutor_input"}
        existing = session.scalar(select(LearningInput).where(LearningInput.conversation_id == run.conversation_id,
            LearningInput.idempotency_key == key))
        if existing:
            if existing.payload != operation:
                raise DomainError(409, "idempotency_conflict", "该标识已用于不同操作")
            return public_tutor(session, run)
        self.spaces.owned(session, actor, wid, active=True)
        if not run.view:
            raise DomainError(409, "gate_conflict", "学习任务尚未准备好，请稍后刷新")
        item = get_pack(run.view["unit_id"])
        require_content(session, actor.account.source_kind, item.unit_id, item.version)
        require_idle(session, wid, ["roundtable", "teach"])
        if run.status != "awaiting_input" or run.revision != payload["expected_revision"] or run.gate_id != payload["gate_id"]:
            raise DomainError(409, "gate_conflict", "学习步骤已更新，请刷新后再提交")
        if run.version != version_for(item.unit_id):
            raise DomainError(409, "version_conflict", "教学版本已更新，请重新发起")
        check_action(run.view, payload["action"], actor.account.learner_id)
        conversation = session.get(Conversation, run.conversation_id)
        self.spaces.accept_input(session, actor, wid, run.conversation_id, key, conversation.revision, operation)
        run.status, run.gate_id = "queued", None
        run.revision += 1
        event(session, run, "run.queued")
        session.flush()
        return public_tutor(session, run)

    def cancel(self, session, actor, wid, rid, revision):
        super().cancel(session, actor, wid, rid, revision)
        return public_tutor(session, self.owned(session, actor, wid, rid))
