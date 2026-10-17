from sqlalchemy import select

from app.infrastructure.db.models import AgentRun, AuditEvent, Conversation, LearningInput
from app.modules.errors import DomainError
from app.modules.learning.activity_graph import check_action, version_for
from app.modules.learning.activity_catalog import get_activity, activity_for_run
from app.modules.learning.content_service import require_activity_content
from app.modules.learning.service import Learning, event, request_hash
from app.infrastructure.runtime import model_tasks
from app.infrastructure.db.models import ModelPolicy, ModelConfiguration, Workspace, Account, ModelOperation
from app.modules.learning.teach_coaching import UNIT as TEACH_UNIT


def require_idle(session, wid, kinds):
    pending = session.scalar(select(AgentRun.id).where(AgentRun.workspace_id == wid,
        AgentRun.kind.in_(kinds), AgentRun.status.in_(["queued", "awaiting_input"])).limit(1))
    if pending:
        raise DomainError(409, "learning_busy", "请先完成或结束当前陪学/互动活动，再切换路线；独立检测期间不开放辅助互动")


def public_activity(session, run):
    policy = session.get(ModelPolicy, 1)
    config = session.get(ModelConfiguration, policy.active_id) if policy.active_id else None
    space = session.get(Workspace, run.workspace_id)
    account = session.get(Account, space.owner_id)
    eligible = bool(run.kind == 'teach' and run.view and run.view.get('flow') == 'teach_cycle_v1'
        and run.view.get('activity_version') == get_activity(TEACH_UNIT).version
        and account.source_kind in {'synthetic', 'adult_test'})
    remaining = model_tasks.remaining_calls(session, account.id, config) if config and eligible else None
    coaching = model_tasks.latest(session, run)
    if coaching and run.view and run.view['phase'] == 'summary' and coaching['status'] == 'succeeded':
        coaching['result'] = session.get(ModelOperation, coaching['operation_id']).result
    return {'coaching': coaching, 'coaching_available': bool(config and eligible and run.status == 'awaiting_input'
        and run.view['phase'] == 'opening' and (remaining is None or remaining > 0)),
        'coaching_remaining_calls': remaining, "run_id": run.id, **{key: getattr(run, key) for key in (
        "workspace_id", "conversation_id", "kind", "status", "revision", "gate_id", "error_code", "event_seq", "created_at", "view")}}


class Activities(Learning):
    def __init__(self, kind):
        if kind not in {"roundtable", "teach"}:
            raise ValueError("unsupported activity")
        self.kind = kind

    def owned(self, session, actor, wid, rid, active=False):
        return super().owned(session, actor, wid, rid, active=active, kind=self.kind)

    def list(self, session, actor, wid, offset=0):
        self.spaces.owned(session, actor, wid)
        return {"items": [public_activity(session, run) for run in session.scalars(select(AgentRun).where(
            AgentRun.workspace_id == wid, AgentRun.kind == self.kind).order_by(
            AgentRun.created_at.desc(), AgentRun.id.desc()).offset(offset).limit(20))]}

    def start(self, session, actor, wid, payload, key):
        self.spaces.owned(session, actor, wid)
        existing = session.scalar(select(AgentRun).where(AgentRun.workspace_id == wid, AgentRun.creation_key == key))
        if existing:
            if existing.kind != self.kind or existing.creation_hash != request_hash(payload):
                raise DomainError(409, "idempotency_conflict", "该标识已用于其他请求")
            return public_activity(session, existing)
        self.spaces.owned(session, actor, wid, active=True)
        item = get_activity(payload["unit_id"])
        if item.flow == 'teach_cycle_v1' and self.kind != 'teach':
            raise DomainError(409, 'activity_kind_forbidden', '此流程仅用于我教 AI，请选择圆桌专用互动包')
        require_activity_content(session, actor.account.source_kind, item.unit_id)
        require_idle(session, wid, ["tutor", "roundtable", "teach"])
        conversation = self.spaces.create_conversation(session, actor, wid, self.kind)
        run = AgentRun(workspace_id=wid, conversation_id=conversation.id, creation_key=key,
            creation_hash=request_hash(payload), version=version_for(item.unit_id), kind=self.kind)
        session.add(run)
        session.flush()
        self.spaces.accept_input(session, actor, wid, conversation.id, "start:" + run.id, 1,
            {**payload, "run_id": run.id, "operation": "activity_start", "activity_kind": self.kind})
        event(session, run, "run.queued")
        session.add(AuditEvent(actor_id=actor.account.id, action=self.kind + ".start", target_id=run.id))
        session.flush()
        return public_activity(session, run)

    def submit(self, session, actor, wid, rid, payload, key):
        run = self.owned(session, actor, wid, rid)
        operation = {**payload, "run_id": rid, "operation": "activity_input", "activity_kind": self.kind}
        if not operation.get('request_model'):
            operation.pop('request_model', None)  # Preserve idempotency for pre-model activity inputs.
        existing = session.scalar(select(LearningInput).where(LearningInput.conversation_id == run.conversation_id,
            LearningInput.idempotency_key == key))
        if existing:
            if existing.payload != operation:
                raise DomainError(409, "idempotency_conflict", "该标识已用于不同操作")
            return public_activity(session, run)
        self.spaces.owned(session, actor, wid, active=True)
        if not run.view:
            raise DomainError(409, "gate_conflict", "互动任务尚未准备好，请稍后刷新")
        item = activity_for_run(run)
        require_activity_content(session, actor.account.source_kind, item.unit_id)
        require_idle(session, wid, ["tutor"])
        if run.status != "awaiting_input" or run.revision != payload["expected_revision"] or run.gate_id != payload["gate_id"]:
            raise DomainError(409, "gate_conflict", "互动步骤已更新，请刷新后再提交")
        if run.version != version_for(item.unit_id):
            raise DomainError(409, "version_conflict", "互动版本已更新，请重新发起")
        check_action(run.view, payload["action"])
        conversation = session.get(Conversation, run.conversation_id)
        if payload.get('request_model') and (self.kind != 'teach' or item.unit_id != TEACH_UNIT or payload['action'] != 'contribute'):
            raise DomainError(409, 'model_scope_forbidden', '仅首次概率讲解可请求模型追问')
        entry = self.spaces.accept_input(session, actor, wid, run.conversation_id, key, conversation.revision, operation)
        run.status, run.gate_id = "queued", None
        run.revision += 1
        if payload.get('request_model'):
            model_tasks.reserve(session, actor.account, run, entry, payload['reasoning'], teach_item=item)
        event(session, run, "run.queued")
        session.flush()
        return public_activity(session, run)

    def cancel(self, session, actor, wid, rid, revision):
        super().cancel(session, actor, wid, rid, revision)
        run = self.owned(session, actor, wid, rid)
        for op in session.scalars(select(ModelOperation).where(ModelOperation.run_id == rid, ModelOperation.status.in_(model_tasks.ACTIVE))):
            model_tasks.cancel(session, run, op.id)
        session.flush()
        return public_activity(session, self.owned(session, actor, wid, rid))
