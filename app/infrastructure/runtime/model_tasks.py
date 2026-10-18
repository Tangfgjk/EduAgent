"""Persistent model task interface: reserve, activate, execute outside transactions, fence results.

Never automatically reissue a remote request after an uncertain outcome. The daily
call allowance is consumed on reservation, including cancellations and failures.
"""
import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from sqlalchemy import select

from app.infrastructure.db.models import (Account, AgentRun, AuditEvent, ModelConfiguration,
    ModelDailyBudget, ModelOperation, ModelPolicy, LearningInput, OutboxJob, Workspace, new_id, now)
from app.llm.provider import (FakeProvider, GenerationRequest, ProviderConfig, ProviderError, create_provider)
from app.modules.errors import DomainError
from app.modules.learning.model_coaching import (PROMPT_VERSION, MULTITURN_PROMPTS, SUPPORTED_PROMPTS,
    UNIT, freeze_request, validate_proposal)
from app.modules.learning.service import request_hash

ACTIVE = {"waiting", "pending", "running"}
BUSINESS_TIMEZONE = timezone(timedelta(hours=8))


def day_key(timestamp=None):
    return datetime.fromtimestamp(now() if timestamp is None else timestamp, BUSINESS_TIMEZONE).strftime("%Y-%m-%d")


def model_key():
    value = os.getenv("WENJIN_DEEPSEEK_KEY", "")
    if not value:
        path = Path(os.getenv("WENJIN_DEEPSEEK_KEY_FILE", ".local/deepseek.key"))
        if path.is_file():
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError:
                return ''
    return value


def remaining_calls(session, account_id, config):
    """Advisory display only; reserve still enforces both budgets under transaction locks."""
    day = day_key()
    remaining = []
    for scope, limit in (("global", config.site_daily_calls), ("account:" + account_id, config.account_daily_calls)):
        budget = session.get(ModelDailyBudget, (day, scope))
        if limit > 0:
            remaining.append(max(0, limit - (budget.calls if budget else 0)))
    return min(remaining) if remaining else None


def public_configuration(config):
    if config is None:
        return None
    return {"configuration_id": config.id, **{name: getattr(config, name) for name in (
        "vendor", "model", "max_output_tokens", "account_daily_calls", "site_daily_calls", "prompt_version", "created_at")}}


def public_operation(op, config, run=None, include_result=True):
    current = run is None or (run.revision == op.run_revision and run.status == "awaiting_input")
    return {"operation_id": op.id, "configuration_id": op.configuration_id,
            "status": op.status, "task_id": op.task_id, "hint_level": op.hint_level,
            "vendor": config.vendor, "model": config.model, "simulated": config.vendor == "fake",
            "current": current, "result": op.result if current and include_result else None,
            "prompt_version": op.request.get("prompt_version", PROMPT_VERSION),
            "context_turns": op.request.get("context_turns", 1),
            "context": op.request.get("context", []) if current and include_result else [],
            "error_code": op.error_code, "usage": op.usage, "elapsed_ms": op.elapsed_ms,
            "billing_unknown": op.billing_unknown}


def runtime_status(session):
    policy = session.get(ModelPolicy, 1)
    config = session.get(ModelConfiguration, policy.active_id) if policy.active_id else None
    ops = list(session.scalars(select(ModelOperation).order_by(ModelOperation.created_at.desc(), ModelOperation.id.desc()).limit(20)))
    day = day_key()
    budget = session.get(ModelDailyBudget, (day, "global"))
    midnight = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=BUSINESS_TIMEZONE).timestamp())
    today = list(session.scalars(select(ModelOperation).where(ModelOperation.created_at >= midnight)))
    return {"revision": policy.revision, "active": public_configuration(config),
            "deepseek_key_configured": bool(model_key()), "budget_day": day, "budget_timezone": "Asia/Shanghai",
            "reserved_calls": budget.calls if budget else 0,
            "input_tokens_known": sum((op.usage or {}).get("input_tokens") or 0 for op in today),
            "output_tokens_known": sum((op.usage or {}).get("output_tokens") or 0 for op in today),
            "missing_usage_calls": sum(not op.usage or any(op.usage.get(k) is None for k in ("input_tokens", "output_tokens")) for op in today),
            "billing_unknown_calls": sum(op.billing_unknown for op in today),
            "recent": [public_operation(op, session.get(ModelConfiguration, op.configuration_id), include_result=False) for op in ops]}


def publish_configuration(session, actor, payload):
    policy = session.get(ModelPolicy, 1, with_for_update=True)
    if policy.revision != payload["expected_revision"]:
        raise DomainError(409, "revision_conflict", "模型配置已更新，请刷新")
    vendor = payload["vendor"]
    prompt_version = payload.get("prompt_version", PROMPT_VERSION)
    if prompt_version not in SUPPORTED_PROMPTS:
        raise DomainError(422, "model_version_conflict", "不支持此模型提示版本")
    if vendor == "deepseek" and not model_key():
        raise DomainError(409, "model_key_missing", "请先配置 DeepSeek 密钥并确认服务进程有读取权限")
    config = ModelConfiguration(vendor=vendor, model="deepseek-flash" if vendor == "deepseek" else "scripted",
        max_output_tokens=payload["max_output_tokens"], account_daily_calls=payload["account_daily_calls"],
        site_daily_calls=payload["site_daily_calls"], prompt_version=prompt_version, actor_id=actor.account.id)
    session.add(config)
    session.flush()
    policy.active_id, policy.revision = config.id, policy.revision + 1
    session.add(AuditEvent(actor_id=actor.account.id, action="model.publish", target_id=config.id))
    session.flush()
    return runtime_status(session)


def pause_configuration(session, actor, revision):
    policy = session.get(ModelPolicy, 1, with_for_update=True)
    if policy.revision != revision:
        raise DomainError(409, "revision_conflict", "模型配置已更新，请刷新")
    policy.active_id, policy.revision = None, policy.revision + 1
    policy.stop_epoch += 1
    session.add(AuditEvent(actor_id=actor.account.id, action="model.pause", target_id="global"))
    session.flush()
    return runtime_status(session)


def collect_context(session, account, run, config, policy):
    """Fetch only completed same-task coaching; never traverse other spaces or full checkpoints."""
    if config.prompt_version not in MULTITURN_PROMPTS:
        return (), ()
    ops = list(session.scalars(select(ModelOperation).join(LearningInput, LearningInput.id == ModelOperation.input_id)
        .where(ModelOperation.account_id == account.id, ModelOperation.workspace_id == run.workspace_id,
            ModelOperation.run_id == run.id, ModelOperation.task_id == run.view["task"]["task_id"],
            ModelOperation.configuration_id == config.id, ModelOperation.policy_epoch == policy.stop_epoch,
            ModelOperation.status == "succeeded", ModelOperation.run_revision < run.revision,
            ModelOperation.hint_level < len(run.view["hints"]), LearningInput.source_kind == account.source_kind,
            LearningInput.account_id == account.id, LearningInput.workspace_id == run.workspace_id,
            LearningInput.conversation_id == run.conversation_id)
        .order_by(ModelOperation.run_revision.desc()).limit(3)))
    history, sources = [], []
    for op in reversed(ops):
        snapshot, result = op.request, op.result
        if (not result or snapshot.get("prompt_version") != config.prompt_version
                or snapshot.get("content_version") != run.view["content_version"]
                or request_hash(snapshot) != op.request_hash):
            continue
        try:
            validated = validate_proposal(json.dumps({"quote": result["quote"], "focus": result["focus"]}), snapshot)
            if result["question"] != validated["question"]:
                continue
            history.append({"student_text": snapshot["student_text"], "focus": validated["focus"],
                            "question": validated["question"]})
            sources.append(op.id)
        except (ProviderError, KeyError, TypeError):
            continue
    return history, sources


def collect_teach_context(session, account, run, config, policy, item):
    from app.modules.learning.teach_coaching import validate_proposal as validate_teach, PROMPT_VERSION as TEACH_V1, MULTITURN_PROMPT_VERSION as TEACH_V2
    ops = session.scalars(select(ModelOperation).join(LearningInput, LearningInput.id == ModelOperation.input_id).where(
        ModelOperation.run_id == run.id, ModelOperation.workspace_id == run.workspace_id,
        ModelOperation.account_id == account.id, ModelOperation.task_id == run.view['task']['task_id'],
        ModelOperation.configuration_id == config.id, ModelOperation.policy_epoch == policy.stop_epoch,
        ModelOperation.run_revision <= run.revision, ModelOperation.status == 'succeeded',
        LearningInput.source_kind == account.source_kind, LearningInput.account_id == account.id,
        LearningInput.workspace_id == run.workspace_id, LearningInput.conversation_id == run.conversation_id)
        .order_by(ModelOperation.run_revision.desc()).limit(2))
    history, sources = [], []
    for op in reversed(list(ops)):
        snapshot = op.request
        if (snapshot.get('purpose') != 'teach' or snapshot.get('prompt_version') not in {TEACH_V1, TEACH_V2}
                or snapshot.get('activity_version') != item.version or snapshot.get('content_version') != item.parent.version
                or snapshot.get('activity_unit_id') != item.unit_id or request_hash(snapshot) != op.request_hash or not op.result):
            continue
        try:
            result = validate_teach(json.dumps({k: op.result[k] for k in ('quote', 'focus', 'question')}, ensure_ascii=False), snapshot)
            history.append({'student_text': snapshot['student_text'], 'focus': result['focus'], 'question': result['question']})
            sources.append(op.id)
        except (ProviderError, KeyError, TypeError):
            continue
    return history, sources


def reserve(session, account, run, entry, text, *, teach_item=None, planning_bundle=None):
    if planning_bundle is not None and (run.kind != 'strategy' or entry.payload.get('operation') != 'strategy_start' or not entry.payload.get('request_model')):
        raise DomainError(409, 'model_scope_forbidden', '仅新规划可请求模型候选')
    if planning_bundle is None and teach_item is None and (run.kind != 'tutor' or run.view["unit_id"] != UNIT or run.view["phase"] != "practice"):
        raise DomainError(409, "model_scope_forbidden", "模型追问目前仅开放概率练习，不用于诊断或独立检测")
    if teach_item is not None:
        from app.modules.learning.teach_coaching import UNIT as TEACH_UNIT
        from app.modules.learning.roundtable_coaching import UNIT as ROUNDTABLE_UNIT
        initial = run.view['phase'] == 'opening' and entry.payload.get('action') == 'contribute'
        followup = (run.view['phase'] == 'challenge' and entry.payload.get('action') == 'refine'
            and run.view.get('model_requested') and max(1, run.view.get('model_rounds', 1)) < 3)
        teach_allowed = run.kind == 'teach' and teach_item.unit_id == TEACH_UNIT and (initial or followup)
        roundtable_allowed = run.kind == 'roundtable' and teach_item.unit_id == ROUNDTABLE_UNIT and initial
        if not (teach_allowed or roundtable_allowed):
            raise DomainError(409, 'model_scope_forbidden', '模型追问仅用于有界概率讲解，不用于另一题验证')
    if account.source_kind not in {"synthetic", "adult_test"}:
        raise DomainError(403, "model_source_forbidden", "当前模型仅用于模拟或成人开发测试")
    policy = session.get(ModelPolicy, 1, with_for_update=True)
    if not policy.active_id:
        raise DomainError(409, "model_paused", "管理员尚未开放模型追问，可继续使用预设提示")
    config = session.get(ModelConfiguration, policy.active_id)
    if config.prompt_version not in SUPPORTED_PROMPTS:
        raise DomainError(409, "model_version_conflict", "模型提示版本已更新，请重新发布配置")
    reservation_time = now()
    day = day_key(reservation_time)
    for scope, limit in (("global", config.site_daily_calls), ("account:" + account.id, config.account_daily_calls)):
        budget = session.get(ModelDailyBudget, (day, scope), with_for_update=True)
        if budget is None:
            budget = ModelDailyBudget(day=day, scope=scope, calls=0)
            session.add(budget)
        if limit > 0 and budget.calls >= limit:
            raise DomainError(429, "model_budget_exhausted", "今日模型调用额度已用完，可继续使用预设提示")
        budget.calls += 1
    if planning_bundle is not None:
        from app.modules.planning.model import freeze_request as freeze_planning
        snapshot = freeze_planning(planning_bundle, entry.payload['candidate'], account, config)
    elif teach_item is None:
        history, sources = collect_context(session, account, run, config, policy)
        snapshot = freeze_request(run.view, text, account, config.max_output_tokens,
            prompt_version=config.prompt_version, history=history, context_sources=sources)
    elif run.kind == 'roundtable':
        from app.modules.learning.roundtable_coaching import freeze_request as freeze_roundtable
        snapshot = freeze_roundtable(teach_item, text, entry.payload['answer'], account,
            config.max_output_tokens, config.prompt_version)
    else:
        from app.modules.learning.teach_coaching import freeze_request as freeze_teach
        context = {}
        if entry.payload.get('action') == 'refine':
            history, sources = collect_teach_context(session, account, run, config, policy, teach_item)
            context = {'history': history, 'context_sources': sources}
        snapshot = freeze_teach(teach_item, text, entry.payload['answer'], account,
            config.max_output_tokens, config.prompt_version, **context)
    op = ModelOperation(input_id=entry.id, run_id=run.id, workspace_id=run.workspace_id, account_id=account.id,
        configuration_id=config.id, run_revision=run.revision + 1, policy_epoch=policy.stop_epoch, task_id='planning.sources.v1' if planning_bundle is not None else run.view["task"]["task_id"],
        hint_level=len((run.view or {}).get("hints", [])), request=snapshot, request_hash=request_hash(snapshot), created_at=reservation_time)
    session.add(op)
    session.flush()
    return op


def activate(session, run, entry):
    op = session.scalar(select(ModelOperation).where(ModelOperation.input_id == entry.id).with_for_update())
    if op is not None and op.status == "waiting":
        op.status = "pending" if run.status == "awaiting_input" and run.revision == op.run_revision else "discarded"


def latest(session, run):
    op = session.scalar(select(ModelOperation).where(ModelOperation.run_id == run.id)
        .order_by(ModelOperation.run_revision.desc()).limit(1))
    return public_operation(op, session.get(ModelConfiguration, op.configuration_id), run) if op else None


def cancel(session, run, operation_id):
    op = session.scalar(select(ModelOperation).where(ModelOperation.id == operation_id,
        ModelOperation.run_id == run.id, ModelOperation.workspace_id == run.workspace_id).with_for_update())
    if op is None:
        raise DomainError(404, "not_found", "未找到此学习任务的模型调用")
    if op.status in ACTIVE:
        op.billing_unknown = op.status == "running"
        op.status, op.error_code, op.finished_at = "cancelled", "cancelled_by_student", now()
        op.lease_token, op.lease_until = None, None
    return op


class ModelTaskRunner:
    def __init__(self, db, provider_factory=None, heartbeat_seconds=3, lease_seconds=20):
        self.db = db
        self.provider_factory = provider_factory or self._provider
        self.heartbeat_seconds, self.lease_seconds = heartbeat_seconds, lease_seconds

    @staticmethod
    def _provider(config, snapshot):
        if config.vendor == "fake":
            if snapshot.get('purpose') == 'planning':
                from app.modules.planning.model import fake_proposal
                return FakeProvider((json.dumps(fake_proposal(snapshot), ensure_ascii=False),))
            if snapshot.get('purpose') == 'roundtable':
                from app.modules.learning.roundtable_coaching import fake_proposal
                return FakeProvider((json.dumps(fake_proposal(snapshot['student_text']), ensure_ascii=False),))
            proposal = {"quote": snapshot["student_text"][:80], "focus": "clarify"}
            if snapshot.get('purpose') == 'teach':
                proposal['question'] = '你能说明讲解中每个基本结果是怎样确定的吗？'
            return FakeProvider((json.dumps(proposal, ensure_ascii=False),))
        return create_provider(ProviderConfig(vendor="deepseek", model=config.model,
            api_key=model_key(), max_output_tokens=config.max_output_tokens, timeout_seconds=30))

    def _locked(self, session, op_id):
        candidate = session.get(ModelOperation, op_id)
        if candidate is None:
            return None
        # Consistent with HTTP: account -> workspace -> run -> policy -> operation.
        account = session.get(Account, candidate.account_id, with_for_update=True)
        workspace = session.get(Workspace, candidate.workspace_id, with_for_update=True)
        run = session.get(AgentRun, candidate.run_id, with_for_update=True)
        policy = session.get(ModelPolicy, 1, with_for_update=True)
        op = session.scalar(select(ModelOperation).where(ModelOperation.id == op_id).with_for_update().execution_options(populate_existing=True))
        reason = ("account_disabled" if not account.enabled else "workspace_archived" if workspace.status != "active"
                  else "model_source_forbidden" if account.source_kind not in {"synthetic", "adult_test"}
                  else "model_paused" if not policy.active_id or policy.stop_epoch != op.policy_epoch else "stale_result"
                  if run.status != "awaiting_input" or run.revision != op.run_revision else None)
        if not reason:
            from app.modules.learning.content_service import require_content
            try:
                if op.request.get('purpose') == 'planning':
                    from app.modules.planning.graph import VERSION as PLANNING_VERSION
                    from app.modules.planning.service import require_fresh
                    from app.modules.learning.activity_service import require_idle
                    body = session.get(LearningInput, op.input_id).payload
                    if (run.kind != 'strategy' or not run.view or run.view['phase'] != 'model_pending' or run.version != PLANNING_VERSION
                            or account.source_kind != body['bundle']['source_kind'] or request_hash(body['bundle']) != op.request.get('bundle_hash')
                            or request_hash(op.request) != op.request_hash):
                        raise DomainError(409, 'stale_result', '规划输入或来源已变化')
                    require_fresh(session, run, body['bundle'], body['base_revision'], body['expires_at'])
                    require_idle(session, run.workspace_id, ['tutor', 'teach', 'roundtable'])
                elif op.request.get('purpose') in {'teach', 'roundtable'}:
                    from app.modules.learning.activity_catalog import activity_for_run
                    from app.modules.learning.content_service import require_activity_content
                    from app.modules.learning.teach_coaching import UNIT as TEACH_UNIT
                    from app.modules.learning.roundtable_coaching import UNIT as ROUNDTABLE_UNIT
                    item = activity_for_run(run)
                    expected_kind = op.request['purpose']
                    expected_unit = TEACH_UNIT if expected_kind == 'teach' else ROUNDTABLE_UNIT
                    if (run.kind != expected_kind or item.unit_id != expected_unit or run.view['phase'] != 'challenge'
                            or item.version != op.request.get('activity_version')
                            or item.unit_id != op.request.get('activity_unit_id')
                            or item.parent.version != op.request.get('content_version')):
                        raise DomainError(409, 'stale_result', '讲解步骤已更新')
                    # Match HTTP/teaching-worker order: activity approval, then parent approval.
                    require_activity_content(session, account.source_kind, item.unit_id)
                else:
                    require_content(session, account.source_kind, UNIT, op.request["content_version"])
            except DomainError as error:
                reason = error.code
        return op, reason

    @staticmethod
    def _end(op, status, error):
        op.status, op.error_code, op.finished_at = status, error, now()
        op.lease_token, op.lease_until = None, None

    def recover(self):
        with self.db.transaction() as session:
            expired = list(session.scalars(select(ModelOperation).where(ModelOperation.status == "running",
                ModelOperation.lease_until <= now()).with_for_update(skip_locked=True)))
            for op in expired:
                op.billing_unknown = True
                self._end(op, "unknown", "worker_interrupted")
            waiting = session.scalars(select(ModelOperation).join(OutboxJob, OutboxJob.input_id == ModelOperation.input_id)
                .where(ModelOperation.status == "waiting", OutboxJob.status == "dead").with_for_update(of=ModelOperation, skip_locked=True))
            for op in waiting:
                self._end(op, "failed", "input_processing_failed")
            stale = session.scalars(select(ModelOperation).join(AgentRun, AgentRun.id == ModelOperation.run_id)
                .where(ModelOperation.status == "waiting", AgentRun.status.in_(["cancelled", "failed", "completed"]))
                .with_for_update(of=ModelOperation, skip_locked=True))
            for op in stale:
                self._end(op, "discarded", "run_ended")

    def claim(self, operation_id=None):
        self.recover()
        # Candidate lookup is unlocked; a short ordered lock transaction fences competing workers.
        with self.db.transaction() as session:
            query = select(ModelOperation.id).where(ModelOperation.status == "pending")
            if operation_id is not None:
                query = query.where(ModelOperation.id == operation_id)
            ids = list(session.scalars(query.order_by(ModelOperation.created_at, ModelOperation.id).limit(8)))
        for op_id in ids:
            with self.db.transaction() as session:
                op, reason = self._locked(session, op_id)
                if op.status != "pending":
                    continue
                if reason:
                    self._end(op, "discarded", reason)
                    continue
                if request_hash(op.request) != op.request_hash:
                    self._end(op, "failed", "snapshot_mismatch")
                    continue
                config = session.get(ModelConfiguration, op.configuration_id)
                if (config.prompt_version not in SUPPORTED_PROMPTS
                        or op.request.get("configuration_prompt_version", op.request.get("prompt_version")) != config.prompt_version):
                    self._end(op, "failed", "model_version_conflict")
                    continue
                op.status, op.lease_token, op.lease_until = "running", new_id(), now() + self.lease_seconds
                # Detached values, no connection or ORM session crosses the model call.
                return {"id": op.id, "token": op.lease_token, "request": op.request, "config": config}
        return None

    def heartbeat(self, claim):
        with self.db.transaction() as session:
            op, reason = self._locked(session, claim["id"])
            if op.status != "running" or op.lease_token != claim["token"] or op.lease_until <= now():
                return False
            if reason:
                op.billing_unknown = True
                self._end(op, "discarded", reason)
                return False
            op.lease_until = now() + self.lease_seconds
            return True

    def complete(self, claim, result=None, error=None):
        with self.db.transaction() as session:
            op, reason = self._locked(session, claim["id"])
            # A stale worker cannot commit any result after cancellation, lease loss or newer input.
            if op.status != "running" or op.lease_token != claim["token"] or op.lease_until <= now():
                return False
            if result:
                op.usage, op.returned_model, op.elapsed_ms = result.usage.model_dump(), result.returned_model, result.elapsed_ms
                op.billing_unknown = not result.simulated and (result.usage.input_tokens is None or result.usage.output_tokens is None)
            if reason:
                self._end(op, "discarded", reason)
                return False
            if error:
                op.billing_unknown = True
                self._end(op, "unknown" if error in {"timeout", "transport_error", "runtime_error"} else "failed", error)
                return True
            try:
                if result.finish_reason != "stop":
                    raise ProviderError("truncated_output")
                if claim['request'].get('purpose') == 'planning':
                    from app.modules.planning.model import validate_proposal as validate_planning
                    proposal = validate_planning(result.text, claim['request'])
                elif claim['request'].get('purpose') == 'roundtable':
                    from app.modules.learning.roundtable_coaching import validate_proposal as validate_roundtable
                    proposal = validate_roundtable(result.text, claim['request'])
                elif claim['request'].get('purpose') == 'teach':
                    from app.modules.learning.teach_coaching import validate_proposal as validate_teach
                    proposal = validate_teach(result.text, claim['request'])
                else:
                    proposal = validate_proposal(result.text, claim["request"])
            except ProviderError as failure:
                self._end(op, "failed", failure.code)
            else:
                op.result = proposal
                self._end(op, "succeeded", None)
                if claim['request'].get('purpose') == 'planning':
                    from app.modules.planning.service import queue_model_candidate
                    queue_model_candidate(session, op)
            return True

    async def _execute(self, claim):
        task = None
        try:
            provider = self.provider_factory(claim["config"], claim["request"])
            async def bounded_generate():
                async with asyncio.timeout(30):
                    return await provider.generate(GenerationRequest.model_validate(claim["request"]["generation"]))
            task = asyncio.create_task(bounded_generate())
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=self.heartbeat_seconds)
                if done:
                    break
                if not self.heartbeat(claim):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    return
            self.complete(claim, result=task.result())
        except ProviderError as error:
            self.complete(claim, error=error.code)
        except TimeoutError:
            self.complete(claim, error="timeout")
        except Exception:
            self.complete(claim, error="runtime_error")
        finally:
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def step(self, operation_id=None):
        claim = self.claim(operation_id)
        if claim is None:
            return False
        asyncio.run(self._execute(claim))
        return True
