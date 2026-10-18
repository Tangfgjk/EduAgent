"""策略治理 Interface：冻结来源、受限提案、双重确认和可追溯激活。"""
from sqlalchemy import select
from pydantic import ValidationError

from app.core.rules import registry_hash
from app.infrastructure.db.models import Account, ActiveStrategy, AgentCheckpoint, AgentRun, AuditEvent, Conversation, FactJudgment, LearningFact, LearningInput, MaterialRelease, MemoryNote, OutboxJob, PlanningMaterial, StrategyActivation, StrategyReceipt, StrategyVersion, Workspace, now
from app.modules.errors import DomainError
from app.modules.learning.service import Learning, event, request_hash
from app.modules.planning.contracts import SoftPolicy
from app.modules.planning.graph import VERSION, advance_strategy
from app.modules.workspaces.service import Workspaces

DEFAULT = {"daily_minutes": 15, "reflection_focus": "basis"}
PROMPTS = {"basis": "这一步用到了什么依据？下次准备怎么检查？", "verification": "怎样把结果代回原条件检查？请写下你的核验与提醒。"}


def fail(code, message):
    raise DomainError(409, code, message)


def material_out(session, row):
    release = session.get(MaterialRelease, row.id)
    return {"material_id": row.id, **{key: getattr(row, key) for key in (
        "title", "kind", "edition", "source_ref", "body", "content_hash", "created_at")},
        "enabled": release.enabled, "revision": release.revision, "professional_review": False}


class Materials:
    def list(self, session, admin=False, offset=0):
        query = select(PlanningMaterial).join(MaterialRelease)
        if not admin: query = query.where(MaterialRelease.enabled.is_(True))
        return {"items": [material_out(session, row) for row in session.scalars(query.order_by(
            PlanningMaterial.created_at.desc(), PlanningMaterial.id.desc()).offset(offset).limit(20))]}

    def create(self, session, actor, payload):
        signature = request_hash(payload)
        existing = session.scalar(select(PlanningMaterial).where(PlanningMaterial.content_hash == signature))
        if existing: return material_out(session, existing)
        row = PlanningMaterial(**payload, content_hash=signature, actor_id=actor.account.id)
        session.add(row); session.flush()
        session.add(MaterialRelease(material_id=row.id, enabled=False))
        session.add(AuditEvent(actor_id=actor.account.id, action="material.create", target_id=row.id))
        session.flush()
        return material_out(session, row)

    def release(self, session, actor, mid, body):
        row = session.get(PlanningMaterial, mid)
        if row is None: raise DomainError(404, "not_found", "未找到资料")
        release = session.get(MaterialRelease, mid, with_for_update=True)
        if release.revision != body["expected_revision"]: fail("revision_conflict", "资料状态已更新，请刷新")
        release.enabled, release.revision = body["enabled"], release.revision + 1
        session.add(AuditEvent(actor_id=actor.account.id, action="material.release" if body["enabled"] else "material.withdraw", target_id=mid))
        session.flush()
        return material_out(session, row)


def evidence_snapshot(session, wid):
    rows = session.execute(select(LearningFact, LearningInput).join(LearningInput, LearningFact.input_id == LearningInput.id)
        .where(LearningFact.workspace_id == wid).order_by(LearningFact.created_at, LearningFact.id)).all()
    notes = list(session.scalars(select(MemoryNote).where(MemoryNote.workspace_id == wid).order_by(MemoryNote.created_at, MemoryNote.id)))
    judgments = list(session.scalars(select(FactJudgment).join(LearningFact).where(LearningFact.workspace_id == wid)
        .order_by(FactJudgment.created_at, FactJudgment.id)))
    # 指纹覆盖全量不可变来源；给规划者的上下文只选最近有界切片。
    fingerprint = request_hash({"facts": [fact.id for fact, _ in rows], "self_reports": [row.id for row in notes], "judgments": [row.id for row in judgments]})
    return {"fingerprint": fingerprint, "counts": {"facts": len(rows), "self_reports": len(notes), "judgments": len(judgments)},
        "facts": [{"fact_id": fact.id, "source_kind": fact.source_kind, "kind": fact.kind, "stage": fact.payload["stage"],
            "status": fact.payload["status"], "task_id": fact.task_id, "content_version": fact.content_version,
            "answer": entry.payload.get("answer", ""), "reasoning": entry.payload.get("reasoning", "")[:600],
            "reflection": entry.payload.get("reflection", "")[:600], "created_at": fact.created_at} for fact, entry in rows[-100:]],
        "self_reports": [{"note_id": row.id, "fact_id": row.fact_id, "text": row.text} for row in notes[-100:]],
        "judgments": [{"judgment_id": row.id, "fact_id": row.fact_id, "conclusion": row.conclusion, "note": row.note} for row in judgments[-100:]],
        "limitation": "各类最近最多100条；指纹与总数覆盖全量。数值结果、自述、人工判断分开，不输入 L3 作为新事实。"}


def material_snapshot(session, ids):
    result = []
    for mid in sorted(ids):
        release = session.get(MaterialRelease, mid, with_for_update=True)
        if release is None or not release.enabled: fail("material_unavailable", "选定资料已停用或不可用，请重新选择")
        result.append(material_out(session, session.get(PlanningMaterial, mid)))
    return result


def skill_text(policy):
    from app.modules.planning.compiler import compile_policy
    return compile_policy(policy)['markdown']


def version_valid(session, version):
    if version.hard_rules_hash != registry_hash(): fail("rules_changed", "教学规则版本变化，请重新生成提案")
    try:
        SoftPolicy.model_validate(version.policy)
    except ValidationError:
        fail("version_invalid", "策略字段不符合受限配置")
    if request_hash({"bundle": version.bundle, "policy": version.policy, "rules": version.hard_rules_hash}) != version.content_hash:
        fail("version_invalid", "策略版本校验失败")
    if version.bundle.get('skill_compilation'):
        from app.modules.planning.compiler import compile_policy
        if version.bundle['skill_compilation']!=compile_policy(version.policy):fail('version_invalid','Skill编译产物与策略不一致')
    if version.bundle.get('evolution_reference'):
        from app.modules.skill_evolution.service import validate_reference
        validate_reference(session,version.bundle['evolution_reference'],version.policy)
    for material in version.bundle["materials"]:
        release = session.get(MaterialRelease, material["material_id"], with_for_update=True)
        if release is None or not release.enabled: fail("material_unavailable", "策略引用的资料已停用")


def require_fresh(session, run, bundle, base_revision, expires_at):
    if bundle["workspace_id"] != run.workspace_id: fail("scope_conflict", "来源与提案项目不一致")
    if now() >= expires_at: fail("proposal_expired", "提案已过期，请重新生成")
    if bundle["rules_hash"] != registry_hash(): fail("rules_changed", "教学规则已更新，请重新生成")
    if bundle.get('course_baseline') is not None:
        from app.modules.planning.runtime import course_baseline
        if course_baseline(session,run.workspace_id)!=bundle['course_baseline']:fail('course_changed','课程绑定已变化，请重新生成策略')
    if bundle.get('evolution_reference'):
        from app.modules.skill_evolution.service import validate_reference
        validate_reference(session,bundle['evolution_reference'],bundle['skill_compilation']['policy'])
    current = session.get(ActiveStrategy, run.workspace_id)
    if (current.revision if current else 0) != base_revision: fail("strategy_base_changed", "当前生效版本已变化，请重新生成")
    if evidence_snapshot(session, run.workspace_id)["fingerprint"] != bundle["evidence"]["fingerprint"]:
        fail("evidence_changed", "学习来源已更新，请结合新记录重新生成提案")
    if bundle.get('global_summary'):
        from app.modules.planning.model import global_snapshot
        owner = session.get(Account, session.get(Workspace, run.workspace_id).owner_id)
        if global_snapshot(session, owner)['fingerprint'] != bundle['global_summary']['fingerprint']:
            fail('global_evidence_changed', '全局来源计数已变化，请重新生成提案')
    current_materials = material_snapshot(session, [row["material_id"] for row in bundle["materials"]])
    if current_materials != sorted(bundle["materials"], key=lambda row: row["material_id"]):
        fail("material_changed", "资料发布版本已变化，请重新生成提案")


def effective_policy(session, wid):
    pointer = session.get(ActiveStrategy, wid)
    if not pointer or pointer.version_id is None: return None, DEFAULT.copy(), None
    version = session.get(StrategyVersion, pointer.version_id)
    try:
        if version is None or version.workspace_id != wid: fail("version_invalid", "策略范围无效")
        owner=session.get(Account,session.get(Workspace,wid).owner_id)
        if owner.source_kind=='real_student' or owner.source_kind!=version.bundle['source_kind']:fail('source_changed','来源变化，开发策略不生效')
        version_valid(session, version)
    except DomainError as error:
        return None, DEFAULT.copy(), error.code
    return version.id, version.policy, None


def public_strategy(session, run, detail=True):
    version = session.get(StrategyVersion, run.id)
    from app.infrastructure.db.models import ModelOperation, ModelConfiguration
    op = session.scalar(select(ModelOperation).where(ModelOperation.run_id == run.id).order_by(ModelOperation.run_revision.desc()).limit(1))
    config = session.get(ModelConfiguration, op.configuration_id) if op else None
    generation = {'operation_id': op.id, 'status': op.status, 'vendor': config.vendor, 'simulated': config.vendor == 'fake',
        'prompt_version': op.request['prompt_version'], 'error_code': op.error_code, 'usage': op.usage, 'elapsed_ms': op.elapsed_ms} if op else None
    pending_bundle = None
    if op and not version and detail:
        pending_bundle = session.get(LearningInput, op.input_id).payload['bundle']
    return {"run_id": run.id, **{key: getattr(run, key) for key in ("workspace_id", "status", "revision", "gate_id", "error_code", "created_at", "view")},
        "workspace_title": session.get(Workspace, run.workspace_id).title,
        "workspace_status": session.get(Workspace, run.workspace_id).status,
        "compilation": version.bundle.get('skill_compilation') if version and detail else None,
        "version_hash": version.content_hash if version else None, "bundle": version.bundle if version and detail else pending_bundle,
        'generation': generation,
        "expires_at": version.expires_at if version else None, "skill_markdown": skill_text(version.policy) if version and detail else None,
        "receipts": [{"actor_id": row.actor_id, "action": row.action, "note": row.note, "created_at": row.created_at}
            for row in session.scalars(select(StrategyReceipt).where(StrategyReceipt.run_id == run.id).order_by(StrategyReceipt.created_at, StrategyReceipt.id))] if detail else []}


class Planning(Learning):
    def owned(self, session, actor, wid, rid, active=False):
        return super().owned(session, actor, wid, rid, active, kind="strategy")

    def admin_run(self, session, rid):
        wid = session.scalar(select(AgentRun.workspace_id).where(AgentRun.id == rid, AgentRun.kind == "strategy"))
        if wid is None: raise DomainError(404, "not_found", "未找到策略提案")
        session.get(Workspace, wid, with_for_update=True)
        return session.get(AgentRun, rid, with_for_update=True)

    def list(self, session, actor, wid, offset=0):
        self.spaces.owned(session, actor, wid)
        rows = session.scalars(select(AgentRun).where(AgentRun.workspace_id == wid, AgentRun.kind == "strategy")
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).offset(offset).limit(20))
        return {"items": [public_strategy(session, row, False) for row in rows]}

    def admin_list(self, session, offset=0):
        rows = session.scalars(select(AgentRun).where(AgentRun.kind == "strategy")
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).offset(offset).limit(20))
        return {"items": [public_strategy(session, row, False) for row in rows]}

    def start(self, session, actor, wid, body, key, *, evolution_reference=None):
        body = dict(body)
        if evolution_reference:body['_evolution_reference']=evolution_reference
        if not body.get('request_model'):
            body.pop('request_model', None)
        workspace = self.spaces.owned(session, actor, wid)
        existing = session.scalar(select(AgentRun).where(AgentRun.workspace_id == wid, AgentRun.creation_key == key))
        if existing:
            if existing.kind != "strategy" or existing.creation_hash != request_hash(body): fail("idempotency_conflict", "标识已用于不同请求")
            return public_strategy(session, existing)
        self.spaces.owned(session, actor, wid, active=True)
        if workspace.kind != "project": fail("project_required", "策略规划只在正式项目中使用，临时记录不会自动带入")
        if actor.account.source_kind == "real_student": fail("development_only", "当前提案为开发模板，暂不面向真实学生")
        if body.get('request_model'):
            from app.modules.learning.activity_service import require_idle
            require_idle(session, wid, ['tutor', 'teach', 'roundtable'])
        pending = session.scalar(select(AgentRun.id).where(AgentRun.workspace_id == wid, AgentRun.kind == "strategy",
            AgentRun.status.in_(["queued", "awaiting_input"])).limit(1))
        if pending: fail("strategy_pending", "当前项目已有待处理策略，请继续或取消")
        snapshot = material_snapshot(session, body["material_ids"])
        pointer = session.get(ActiveStrategy, wid)
        bundle = {"workspace_id": wid, "rules_hash": registry_hash(), "student_experience": body["learning_experience"],
            "evidence": evidence_snapshot(session, wid), "materials": snapshot, "source_kind": actor.account.source_kind,
            "base_version_id": pointer.version_id if pointer else None, "created_at": now()}
        from app.modules.evidence.insights import report as learning_insights
        bundle['learning_observations']=learning_insights(session,actor,wid,days=30,source=actor.account.source_kind)['planning_summary']
        if body.get('request_model'):
            from app.modules.planning.model import global_snapshot
            bundle['global_summary'] = global_snapshot(session, actor.account)
        conversation = self.spaces.create_conversation(session, actor, wid, "tutor")
        run = AgentRun(workspace_id=wid, conversation_id=conversation.id, kind="strategy", version=VERSION,
            creation_key=key, creation_hash=request_hash(body))
        session.add(run); session.flush()
        candidate = SoftPolicy(daily_minutes=body["daily_minutes"], reflection_focus=body["reflection_focus"],execution=body.get('execution')).model_dump()
        if evolution_reference:
            from app.modules.skill_evolution.service import validate_reference
            validate_reference(session,evolution_reference,candidate)
            bundle['evolution_reference']=evolution_reference
        if candidate.get('execution'):
            from app.modules.planning.compiler import compile_policy,changes
            from app.modules.planning.runtime import course_baseline
            _,base,_=effective_policy(session,wid)
            bundle.update(skill_compilation=compile_policy(candidate),policy_base=base,policy_changes=changes(base,candidate),course_baseline=course_baseline(session,wid))
        entry = self.enqueue(session, run, actor.account.id, "start:" + run.id, {"operation": "strategy_start", "bundle": bundle,
            "candidate": candidate, "base_revision": pointer.revision if pointer else 0, "expires_at": now() + 7*86400,
            **({'request_model': True} if body.get('request_model') else {})})
        if body.get('request_model'):
            from app.infrastructure.runtime.model_tasks import reserve
            reserve(session, actor.account, run, entry, '', planning_bundle=bundle)
        session.add(AuditEvent(actor_id=actor.account.id, action="strategy.start", target_id=run.id))
        return public_strategy(session, run)

    def enqueue(self, session, run, actor_id, key, payload, note=None):
        workspace = session.get(Workspace, run.workspace_id)
        owner = session.get(Account, workspace.owner_id)
        conversation = session.get(Conversation, run.conversation_id)
        operation = {**payload, "run_id": run.id}
        entry = LearningInput(workspace_id=run.workspace_id, conversation_id=run.conversation_id,
            account_id=owner.id, idempotency_key=key, request_hash=request_hash(operation), source_kind=owner.source_kind, payload=operation)
        session.add(entry); session.flush()
        session.add(OutboxJob(input_id=entry.id))
        conversation.revision += 1
        if note is not None:
            session.add(StrategyReceipt(run_id=run.id, input_id=entry.id, actor_id=actor_id, action=payload["action"], note=note))
        run.status, run.gate_id = "queued", None
        event(session, run, "run.queued")
        session.flush()
        return entry

    def decide(self, session, actor, run, body, key, admin=False):
        operation = {**body, "run_id": run.id, "actor_id": actor.account.id, "operation": "strategy_decision"}
        existing = session.scalar(select(LearningInput).where(LearningInput.conversation_id == run.conversation_id, LearningInput.idempotency_key == key))
        if existing:
            if existing.payload != operation: fail("idempotency_conflict", "标识已用于不同请求")
            return public_strategy(session, run)
        workspace = session.get(Workspace, run.workspace_id)
        if workspace.status != "active": fail("workspace_archived", "归档项目不能继续审核或确认")
        expected_phase = "admin_review" if admin else "student_confirm"
        allowed = {"approve", "reject"} if admin else {"confirm", "decline"}
        if run.status != "awaiting_input" or not run.view or run.view["phase"] != expected_phase or body["action"] not in allowed:
            fail("gate_conflict", "当前身份不能处理此关口")
        if body["gate_id"] != run.gate_id or body["expected_revision"] != run.revision: fail("gate_conflict", "提案已更新，请刷新")
        if run.version != VERSION: fail("version_conflict", "流程版本已更新，请重新发起")
        version = session.get(StrategyVersion, run.id)
        # 退回/拒绝仍可收尾过期提案；批准/确认必须来源与基线仍有效。
        if body["action"] in {"approve", "confirm"}:
            version_valid(session, version)
            require_fresh(session, run, version.bundle, version.base_revision, version.expires_at)
        run.revision += 1
        self.enqueue(session, run, actor.account.id, key, {key: value for key, value in operation.items() if key != "run_id"}, body["note"])
        session.add(AuditEvent(actor_id=actor.account.id, action="strategy." + body["action"], target_id=run.id))
        return public_strategy(session, run)

    def cancel(self, session, actor, wid, rid, revision):
        if self.owned(session, actor, wid, rid).status != "cancelled":
            self.spaces.owned(session, actor, wid, active=True)
        super().cancel(session, actor, wid, rid, revision)
        from app.infrastructure.db.models import ModelOperation
        from app.infrastructure.runtime.model_tasks import ACTIVE, cancel
        run = self.owned(session, actor, wid, rid)
        for op in session.scalars(select(ModelOperation).where(ModelOperation.run_id == rid, ModelOperation.status.in_(ACTIVE))):
            cancel(session, run, op.id)
        return public_strategy(session, self.owned(session, actor, wid, rid))

    def active(self, session, actor, wid):
        self.spaces.owned(session, actor, wid)
        pointer = session.get(ActiveStrategy, wid)
        version_id, policy, reason = effective_policy(session, wid)
        history = session.scalars(select(StrategyActivation).where(StrategyActivation.workspace_id == wid)
            .order_by(StrategyActivation.created_at.desc(), StrategyActivation.id.desc()).limit(50))
        return {"workspace_id": wid, "version_id": pointer.version_id if pointer else None,
            "revision": pointer.revision if pointer else 0, "effective": version_id is not None,
            "unavailable_reason": reason, "policy": policy,
            "history": [{"from_version": row.from_version, "to_version": row.to_version, "kind": row.kind,
                         "note": row.note, "created_at": row.created_at} for row in history]}

    def rollback(self, session, actor, wid, body, key):
        self.spaces.owned(session, actor, wid)
        signature = request_hash(body)
        existing = session.scalar(select(StrategyActivation).where(StrategyActivation.workspace_id == wid, StrategyActivation.idempotency_key == key))
        if existing:
            if existing.request_hash != signature: fail("idempotency_conflict", "标识已用于不同回滚")
            return self.active(session, actor, wid)
        self.spaces.owned(session, actor, wid, active=True)
        pointer = session.get(ActiveStrategy, wid)
        if pointer is None or pointer.revision != body["expected_revision"]: fail("revision_conflict", "策略指针已更新")
        target = body["target_version"]
        if pointer.version_id == target: fail("no_change", "当前已经是此版本")
        if target:
            version = session.get(StrategyVersion, target)
            used = session.scalar(select(StrategyActivation.id).where(StrategyActivation.workspace_id == wid, StrategyActivation.to_version == target))
            if version is None or version.workspace_id != wid or not used: fail("invalid_rollback", "只能回滚本项目曾激活的有效版本")
            version_valid(session, version)
        session.add(StrategyActivation(workspace_id=wid, from_version=pointer.version_id, to_version=target,
            actor_id=actor.account.id, kind="rollback", note=body["note"], idempotency_key=key, request_hash=signature))
        pointer.version_id, pointer.revision = target, pointer.revision + 1
        session.add(AuditEvent(actor_id=actor.account.id, action="strategy.rollback", target_id=wid))
        session.flush()
        return self.active(session, actor, wid)


def queue_model_candidate(session, op):
    """Same transaction as model completion; enqueue once, never activate a candidate here."""
    body = session.get(LearningInput, op.input_id).payload
    run = session.get(AgentRun, op.run_id)
    bundle = {**body['bundle'], 'model_generation': {'operation_id': op.id, 'prompt_version': op.request['prompt_version'],
        'requested_policy': body['candidate'], 'citations': op.result['citations'],
        'source_refs': op.request['source_refs'], 'source_excerpts': op.request['sources'],
        'limitation': '只读取所列有界片段与全局计数，引用命中不代表正确理解来源或教学有效。'}}
    if bundle.get('skill_compilation'):
        from app.modules.planning.compiler import compile_policy,changes
        bundle['skill_compilation']=compile_policy(op.result['policy'])
        bundle['policy_changes']=changes(bundle['policy_base'],op.result['policy'])
    run.revision += 1
    Planning().enqueue(session, run, op.account_id, 'model-ready:' + op.id, {'operation': 'strategy_model_ready',
        'bundle': bundle, 'candidate': op.result['policy'], 'base_revision': body['base_revision'],
        'expires_at': body['expires_at'], 'model_operation_id': op.id})


def process_strategy(session, run, entry, account, workspace):
    """worker 的短事务 Adapter；不进行外部 I/O。"""
    try:
        if not account.enabled: fail("account_disabled", "账号已停用")
        if workspace.status != "active": fail("workspace_archived", "项目已归档")
        if run.version != VERSION: fail("version_conflict", "流程版本已变化")
        checkpoint = session.get(AgentCheckpoint, run.id)
        version = session.get(StrategyVersion, run.id)
        source = version.bundle["source_kind"] if version else entry.payload["bundle"]["source_kind"]
        if account.source_kind != source or source == "real_student": fail("source_changed", "数据来源已变化，开发提案暂不能继续")
        if version is None:
            body = entry.payload
            SoftPolicy.model_validate(body["candidate"])
            require_fresh(session, run, body["bundle"], body["base_revision"], body["expires_at"])
            if body.get('request_model') or body['operation'] == 'strategy_model_ready':
                from app.modules.learning.activity_service import require_idle
                require_idle(session, run.workspace_id, ['tutor', 'teach', 'roundtable'])
            if body.get('request_model'):
                run.view = {'phase': 'model_pending', 'policy': body['candidate'], 'checks': ['来源已冻结，模型候选尚未生成；不能审核或激活。'], 'source': 'model_candidate'}
                run.status, run.gate_id = 'awaiting_input', None
                run.revision += 1
                from app.infrastructure.runtime.model_tasks import activate
                activate(session, run, entry)
                event(session, run, 'strategy.model_pending')
                return
            if body['operation'] == 'strategy_model_ready':
                from app.infrastructure.db.models import ModelOperation
                op = session.get(ModelOperation, body['model_operation_id'])
                if (not op or op.run_id != run.id or op.status != 'succeeded' or op.result['policy'] != body['candidate']
                        or op.result['citations'] != body['bundle']['model_generation']['citations']):
                    fail('candidate_changed', '模型候选与冻结结果不一致')
        else:
            receipt = session.scalar(select(StrategyReceipt).where(StrategyReceipt.input_id == entry.id, StrategyReceipt.run_id == run.id))
            if receipt is None or receipt.action != entry.payload["action"]: fail("receipt_missing", "缺少已授权回执")
            if entry.payload["action"] in {"approve", "confirm"}:
                version_valid(session, version)
                require_fresh(session, run, version.bundle, version.base_revision, version.expires_at)
        state, image, paused = advance_strategy(run.id, entry.payload, checkpoint.image if checkpoint else None)
        SoftPolicy.model_validate(state["view"]["policy"])
        if version and (state["view"]["policy"] != version.policy or state["view"]["checks"] != version.checks):
            fail("candidate_changed", "冻结候选与图状态不一致，请重新生成")
        if version is None:
            version = StrategyVersion(id=run.id, workspace_id=run.workspace_id, bundle=body["bundle"], policy=state["view"]["policy"],
                checks=state["view"]["checks"], hard_rules_hash=registry_hash(), base_revision=body["base_revision"], expires_at=body["expires_at"],
                content_hash=request_hash({"bundle": body["bundle"], "policy": state["view"]["policy"], "rules": registry_hash()}))
            session.add(version); session.flush()
        if checkpoint: checkpoint.image = image
        else: session.add(AgentCheckpoint(run_id=run.id, image=image))
        run.view = state["view"]
        if run.view["phase"] == "approved":
            pointer = session.get(ActiveStrategy, run.workspace_id)
            if pointer is None:
                pointer = ActiveStrategy(workspace_id=run.workspace_id, revision=0)
                session.add(pointer)
            session.add(StrategyActivation(workspace_id=run.workspace_id, from_version=pointer.version_id, to_version=version.id,
                actor_id=receipt.actor_id, kind="activate", note=receipt.note, idempotency_key="activate:" + run.id,
                request_hash=request_hash({"version": version.id})))
            pointer.version_id, pointer.revision = version.id, pointer.revision + 1
            session.add(AuditEvent(actor_id=receipt.actor_id, action="strategy.activate", target_id=version.id))
        run.status, run.gate_id = ("awaiting_input", state["gate_id"]) if paused else ("completed", None)
        kind = "strategy.updated" if paused else "strategy.completed"
    except (DomainError, ValidationError) as error:
        run.status, run.gate_id, run.error_code = "failed", None, error.code if isinstance(error, DomainError) else "candidate_invalid"
        kind = "run.failed"
    run.revision += 1
    event(session, run, kind)
