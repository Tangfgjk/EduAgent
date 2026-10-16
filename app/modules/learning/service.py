"""应用用例只接收已鉴权身份，范围与运行标识由服务端关联。"""
import json

from sqlalchemy import select

from app.infrastructure.db.models import AgentEvent, AgentRun, AuditEvent, Conversation, LearningInput
from app.modules.errors import DomainError
from app.modules.identity.security import digest
from app.modules.learning.graph import VERSION, govern
from app.modules.workspaces.service import Workspaces


def public_run(run):
    return {"run_id": run.id, **{key: getattr(run, key) for key in (
        "workspace_id", "conversation_id", "status", "revision", "draft", "gate_id", "error_code", "event_seq", "created_at")}}


def event(session, run, kind):
    run.event_seq += 1
    session.add(AgentEvent(run_id=run.id, seq=run.event_seq, kind=kind,
        payload={"status": run.status, "revision": run.revision}))


def request_hash(payload):
    return digest(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


class Learning:
    spaces = Workspaces()

    def owned(self, session, actor, wid, rid, active=False, kind="plan"):
        self.spaces.owned(session, actor, wid, active=active)
        run = session.scalar(select(AgentRun).where(AgentRun.id == rid, AgentRun.workspace_id == wid, AgentRun.kind == kind).with_for_update())
        if run is None:
            raise DomainError(404, "not_found", "未找到此空间中的运行")
        return run

    def list(self, session, actor, wid, offset=0):
        self.spaces.owned(session, actor, wid)
        return {"items": [public_run(run) for run in session.scalars(select(AgentRun).where(
            AgentRun.workspace_id == wid, AgentRun.kind == "plan").order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).offset(offset).limit(20))]}

    def start(self, session, actor, wid, payload, key):
        workspace = self.spaces.owned(session, actor, wid)
        existing = session.scalar(select(AgentRun).where(AgentRun.workspace_id == wid, AgentRun.creation_key == key))
        if existing:
            if existing.kind != "plan" or existing.creation_hash != request_hash(payload):
                raise DomainError(409, "idempotency_conflict", "该提交标识已用于不同内容")
            return public_run(existing)
        if workspace.kind != "project":
            raise DomainError(409, "project_required", "当前计划确认流程需要正式学习项目")
        pending = session.scalar(select(AgentRun.id).where(AgentRun.workspace_id == wid,
            AgentRun.kind == "plan",
            AgentRun.status.in_(["queued", "awaiting_input"])).limit(1))
        if pending:
            raise DomainError(409, "plan_pending", "当前项目已有待处理计划，请先确认或取消")
        govern(payload["goal"], actor.account.learner_id)
        conversation = self.spaces.create_conversation(session, actor, wid, "tutor")
        run = AgentRun(workspace_id=wid, conversation_id=conversation.id, creation_key=key,
                       creation_hash=request_hash(payload), version=VERSION)
        session.add(run)
        session.flush()
        self.spaces.accept_input(session, actor, wid, conversation.id, "start:" + run.id, 1,
                                 {**payload, "run_id": run.id, "operation": "start"})
        event(session, run, "run.queued")
        session.add(AuditEvent(actor_id=actor.account.id, action="plan.start", target_id=run.id))
        session.flush()
        return public_run(run)

    def resume(self, session, actor, wid, rid, payload, key):
        run = self.owned(session, actor, wid, rid)
        entry = session.scalar(select(LearningInput).where(LearningInput.conversation_id == run.conversation_id,
                                                           LearningInput.idempotency_key == key))
        operation = {**payload, "run_id": rid, "operation": "resume"}
        if entry:
            if entry.payload != operation:
                raise DomainError(409, "idempotency_conflict", "该提交标识已用于不同内容")
            return public_run(run)
        self.spaces.owned(session, actor, wid, active=True)
        if run.status != "awaiting_input" or run.gate_id != payload["gate_id"] or run.revision != payload["expected_revision"]:
            raise DomainError(409, "gate_conflict", "此确认关口已更新，请刷新后重试")
        if run.version != VERSION:
            raise DomainError(409, "version_conflict", "流程版本已更新，请重新发起计划")
        if payload["action"] == "confirm" and payload["daily_minutes"] != run.draft["daily_minutes"]:
            raise DomainError(409, "draft_changed", "请先调整并查看新草案，再确认")
        conversation = session.get(Conversation, run.conversation_id)
        self.spaces.accept_input(session, actor, wid, run.conversation_id, key, conversation.revision, operation)
        run.status, run.gate_id = "queued", None
        run.revision += 1
        event(session, run, "run.queued")
        session.add(AuditEvent(actor_id=actor.account.id, action="plan." + payload["action"], target_id=rid))
        session.flush()
        return public_run(run)

    def cancel(self, session, actor, wid, rid, revision):
        run = self.owned(session, actor, wid, rid)
        if run.status == "cancelled":
            return public_run(run)
        if run.revision != revision or run.status in {"completed", "failed"}:
            raise DomainError(409, "revision_conflict", "运行已更新，请刷新后重试")
        run.status, run.gate_id = "cancelled", None
        run.revision += 1
        event(session, run, "run.cancelled")
        session.add(AuditEvent(actor_id=actor.account.id, action="plan.cancel", target_id=rid))
        session.flush()
        return public_run(run)

    def events(self, session, actor, wid, rid, after):
        run = self.owned(session, actor, wid, rid)
        if after > run.event_seq:
            raise DomainError(409, "cursor_conflict", "事件游标不属于当前运行进度")
        rows = session.scalars(select(AgentEvent).where(AgentEvent.run_id == rid, AgentEvent.seq > after)
                               .order_by(AgentEvent.seq).limit(100))
        return [{"run_id": rid, "seq": row.seq, "kind": row.kind, "payload": row.payload,
                 "created_at": row.created_at} for row in rows]
