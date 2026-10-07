"""API 网关（docs/07 §9 接口面）。v1 提供 JSON 端点 + 轻量 SSE 流式回合。

注意：本模块不能使用 `from __future__ import annotations`——闭包内定义的
Pydantic 请求模型会被 FastAPI 的类型解析降级为 query 参数。
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.config import Settings
from app.core.schema import (
    ExternalDeadline, GoalContract, GoalStatement, PlanVersion, SuccessCriterion, utcnow,
)
from app.llm.client import BaseLLM, FakeLLM, OpenAICompatClient
from app.orchestration.session import TutorSession
from app.storage.db import Store

WEB_INDEX = Path(__file__).resolve().parent.parent.parent / "web" / "index.html"


def create_app(settings: Settings | None = None, llm: BaseLLM | None = None,
               store: Store | None = None, *, policy_factory=None, clock=None, bank_factory=None) -> FastAPI:
    settings = settings or Settings.load()
    if llm is None:
        extra_body: dict | None = None
        if settings.llm_extra_body_json:
            import json as _json
            try:
                extra_body = _json.loads(settings.llm_extra_body_json)
            except ValueError:
                extra_body = None   # 配置写坏时宁可用默认行为，不让网关起不来
        llm = (OpenAICompatClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model,
                                  extra_body=extra_body)
               if settings.llm_api_key else FakeLLM())
    store = store or Store(settings.db_path)
    from app.learning.service import LearningService, ConsentDenied, EvidenceConflict
    learning = LearningService(store)
    from app.orchestration.runtime import SessionRuntime, RuntimeConflict
    from app.learning.assets import load_catalog
    catalog = load_catalog(settings)
    runtime = SessionRuntime(store, llm, catalog=catalog, policy_factory=policy_factory, clock=clock, bank_factory=bank_factory)

    app = FastAPI(title="桂子问津 Wenjin", version="4.0.0-local-mvp")
    from app.gateway.security import install_local_identity
    install_local_identity(app, settings)
    def local_learner(learner_id):
        if learner_id != settings.learner_id:
            raise HTTPException(403, "本地工作台仅允许配置的学习者")

    def require_purpose(learner_id, purpose="teaching"):
        local_learner(learner_id)
        try:
            learning._authorize(learner_id,purpose)
        except ConsentDenied as exc:
            raise HTTPException(403,str(exc))

    from app.learning.plans import PlanService, PlanConflict
    plans = PlanService(store)

    def runtime_call(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except (RuntimeConflict, EvidenceConflict) as exc:
            raise HTTPException(409, str(exc)) from exc

    def plan_call(operation, *args):
        try:
            return operation(*args)
        except KeyError as exc:
            raise HTTPException(404,str(exc))
        except PermissionError as exc:
            raise HTTPException(403,str(exc))
        except PlanConflict as exc:
            raise HTTPException(409,str(exc))

    # ---------- 静态单页 ----------

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_INDEX.with_name("learning.html"), headers={"Cache-Control": "no-store"})

    @app.get("/prototype")
    def prototype() -> FileResponse:
        # Keep the legacy URL but never serve prototype's hard-coded scores.
        return FileResponse(WEB_INDEX.with_name("learning.html"), headers={"Cache-Control": "no-store"})

    from app.gateway.workbench_routes import install_workbench_routes
    install_workbench_routes(app, store, settings)

    # ---------- 学习契约（docs/02 §3.1） ----------

    class ContractIn(BaseModel):
        goal_text: str
        learner_id: str | None = None
        project_id: str
        success_criteria: list[str] = Field(default_factory=list, max_length=12)
        deadline_title: str | None = None
        deadline_at: str | None = None   # ISO 日期

    def contract_criteria(values: list[str]) -> list[SuccessCriterion]:
        cleaned = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if cleaned:
            return [SuccessCriterion(description=value, kind="post_test", threshold=0.8,
                                     kc_refs=["MATH.G7.EQ.SOLVE", "MATH.G7.EQ.APPLY"])
                    for value in cleaned]
        return [SuccessCriterion(description="独立完成项目中的后测任务，正确率达到 80%",
                                 kind="post_test", threshold=0.8,
                                 kc_refs=["MATH.G7.EQ.SOLVE", "MATH.G7.EQ.APPLY"])]

    def contract_deadlines(title: str | None, at: str | None, existing=()):
        retained = [deadline for deadline in existing if deadline.source != "exam_calendar"]
        if title and at:
            retained.append(ExternalDeadline(source="exam_calendar", title=title, due_at=at))
        return retained

    @app.post("/api/contracts")
    def create_contract(body: ContractIn) -> dict:
        learner_id = body.learner_id or settings.learner_id
        require_purpose(learner_id)
        from app.learning.project_scope import require_project, ProjectScopeError
        try:
            require_project(store, learner_id, body.project_id, writable=True, required=True)
        except ProjectScopeError as exc:
            raise HTTPException(404, str(exc)) from exc
        existing = store.latest_contract(learner_id, body.project_id)
        if existing is not None and existing.status in {"drafting", "active", "revised"}:
            raise HTTPException(409, "This project already has an active goal contract")
        contract = GoalContract(
            learner_id=learner_id, project_id=body.project_id,
            goal_statement=GoalStatement(text=body.goal_text.strip(), authored_by="student"),
            success_criteria=contract_criteria(body.success_criteria),
        )
        contract.external_deadline_refs = contract_deadlines(body.deadline_title, body.deadline_at)
        contract.status = "active"
        store.ensure_learner(learner_id)
        store.save_contract(contract)
        from app.learning.workspace import WorkspaceService
        initial_project_plan = WorkspaceService(store, catalog).plan_snapshot(learner_id, body.project_id)
        plan = PlanVersion(goal_contract_id=contract.goal_contract_id,
                           project_id=body.project_id or "default", status="draft",
                           change_reason="学习目标建立后的初始计划草案",
                           content={"bank_scope": ["MATH.G7.EQ.SOLVE", "MATH.G7.EQ.SETUP",
                                                   "MATH.G7.EQ.APPLY"],
                                    "cadence": "每天 2 题 + 1 次阶段测试/周",
                                    "deadline_at": body.deadline_at,
                                    "project_plan": initial_project_plan},
                           confirmed_at=None)
        contract.plan_version_refs = [plan.version_id]
        store.save_plan_version(plan)
        store.save_contract(contract)
        return {"contract": contract.model_dump(), "plan_version": plan.model_dump()}

    class ContractUpdateIn(BaseModel):
        project_id: str
        goal_text: str
        success_criteria: list[str] = Field(default_factory=list, max_length=12)
        deadline_title: str | None = None
        deadline_at: str | None = None

    @app.put("/api/contracts/{contract_id}")
    def update_contract(contract_id: str, body: ContractUpdateIn) -> dict:
        learner_id = settings.learner_id
        require_purpose(learner_id)
        from app.learning.project_scope import require_project, ProjectScopeError
        try:
            require_project(store, learner_id, body.project_id, writable=True, required=True)
        except ProjectScopeError as exc:
            raise HTTPException(409, str(exc)) from exc
        current = store.get_contract(contract_id)
        if current is None:
            raise HTTPException(404, "Unknown goal contract")
        if current.learner_id != learner_id or current.project_id != body.project_id:
            raise HTTPException(404, "Goal contract is not in the selected project")
        updated = current.model_copy(update={
            "goal_statement": GoalStatement(text=body.goal_text.strip(), authored_by="student"),
            "success_criteria": contract_criteria(body.success_criteria),
            "external_deadline_refs": contract_deadlines(body.deadline_title, body.deadline_at,
                                                          current.external_deadline_refs),
            "status": "active",
        })
        store.save_contract(updated)
        with store.lock:
            learning._audit(learner_id, "goal_contract_updated", {
                "goal_contract_id": contract_id, "project_id": body.project_id,
            })
            store.conn.commit()
        return {"contract": updated.model_dump()}

    @app.get("/api/contracts/latest")
    def latest_contract(learner_id: str | None = None, project_id: str | None = None) -> dict:
        owner = learner_id or settings.learner_id
        require_purpose(owner)
        if project_id is not None:
            from app.learning.project_scope import require_project, ProjectScopeError
            try:
                require_project(store, owner, project_id)
            except ProjectScopeError as exc:
                raise HTTPException(404, str(exc)) from exc
        contract = store.latest_contract(owner, project_id)
        if contract is None:
            raise HTTPException(404, "尚未签署学习契约")
        return contract.model_dump()

    # ---------- 会话 ----------

    class SessionIn(BaseModel):
        session_type: str = "explore"     # explore | checkpoint
        learner_id: str | None = None
        contract_id: str | None = None
        project_id: str
        task_id: str | None = Field(default=None, min_length=1, max_length=100)

    @app.post("/api/sessions")
    def create_session(body: SessionIn) -> dict:
        learner_id = body.learner_id or settings.learner_id
        require_purpose(learner_id)
        from app.learning.project_scope import require_project, ProjectScopeError
        try:
            require_project(store, learner_id, body.project_id, writable=True, required=True)
        except ProjectScopeError as exc:
            raise HTTPException(404, str(exc)) from exc
        contract = store.get_contract(body.contract_id) if body.contract_id \
            else store.latest_contract(learner_id, body.project_id)
        if contract is not None and contract.learner_id != learner_id:
            raise HTTPException(403,"学习契约不属于当前学习者")
        if body.project_id and contract is not None and contract.project_id != body.project_id:
            raise HTTPException(403, "Learning contract belongs to another project")
        if body.session_type == "checkpoint" and contract is None:
            raise HTTPException(400, "阶段测试需要先签署学习契约")
        if body.task_id:
            from app.learning.workspace import WorkspaceService
            try:
                WorkspaceService(store, catalog).task_for_project(learner_id, body.project_id, body.task_id)
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc
        return runtime_call(runtime.create, learner_id, contract, body.session_type,
                            project_id=body.project_id, task_id=body.task_id)

    class MessageIn(BaseModel):
        text: str = ""
        answer: str | None = None
        attempt_id: str | None = None

    def _session(sid: str, project_id: str | None = None, *, writable: bool = False) -> TutorSession:
        require_purpose(settings.learner_id)
        session = runtime_call(runtime.load, sid, settings.learner_id)
        if project_id is not None and session.project_id != project_id:
            raise HTTPException(404, "Session is not in the selected project")
        if writable and session.project_id is not None:
            from app.learning.project_scope import require_project, ProjectScopeError
            try:
                require_project(store, session.learner_id, session.project_id, writable=True)
            except ProjectScopeError as exc:
                raise HTTPException(409, str(exc)) from exc
        return session

    @app.get("/api/sessions/{sid}")
    def session_state(sid: str, project_id: str | None = None):
        require_purpose(settings.learner_id)
        _session(sid, project_id)
        return runtime_call(runtime.public_state, sid, settings.learner_id)

    @app.post("/api/sessions/{sid}/messages")
    def send_message(sid: str, body: MessageIn, project_id: str | None = None) -> dict:
        require_purpose(settings.learner_id)
        _session(sid, project_id, writable=True)
        return runtime_call(runtime.message, sid, settings.learner_id, text=body.text,
                            answer=body.answer, attempt_id=body.attempt_id)

    @app.post("/api/sessions/{sid}/messages/stream")
    async def send_message_stream(sid: str, body: MessageIn, project_id: str | None = None) -> StreamingResponse:
        require_purpose(_session(sid, project_id, writable=True).learner_id)

        def sse(event: str, data: str) -> str:
            return f"event: {event}\ndata: {data}\n\n"

        async def gen():
            yield sse("status", "感知学生状态中…")
            result = await run_in_threadpool(send_message, sid, body, project_id)
            yield sse("status", "规则引擎裁决完成")
            import json as _json

            yield sse("reply", _json.dumps({
                "reply": result["reply"], "ui": result["ui"], "denial": result["denial"],
            }, ensure_ascii=False))

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/sessions/{sid}/reflection")
    def submit_reflection(sid: str, payload: dict, project_id: str | None = None) -> dict:
        require_purpose(settings.learner_id)
        _session(sid, project_id, writable=True)
        return runtime_call(runtime.reflection, sid, settings.learner_id, payload)

    @app.get("/api/learning/projects/{project_id}/sessions")
    def project_sessions(project_id: str) -> dict:
        require_purpose(settings.learner_id)
        from app.learning.project_scope import require_project, ProjectScopeError
        try:
            require_project(store, settings.learner_id, project_id)
        except ProjectScopeError as exc:
            raise HTTPException(404, str(exc)) from exc
        with store.lock:
            rows = store.conn.execute(
                "SELECT s.session_id,s.session_type,s.created_at,json_extract(r.payload,'$.task_id') AS task_id "
                "FROM sessions s JOIN runtime_sessions r ON r.session_id=s.session_id "
                "WHERE s.learner_id=? AND json_extract(r.payload,'$.project_id')=? "
                "ORDER BY s.created_at DESC", (settings.learner_id, project_id)
            ).fetchall()
        return {"sessions": [dict(row) for row in rows]}

    # ---------- 我的镜子（open learner model） ----------

    @app.get("/api/mirror/{learner_id}")
    def mirror(learner_id: str) -> dict:
        local_learner(learner_id)
        try:
            learning._authorize(learner_id)
        except ConsentDenied as exc:
            raise HTTPException(403,str(exc))
        with store.lock:
            row = store.conn.execute("SELECT session_id FROM runtime_sessions WHERE learner_id=? ORDER BY rowid DESC LIMIT 1", (learner_id,)).fetchone()
        if row:
            return runtime_call(runtime.load, row["session_id"], learner_id).mirror()
        snapshot = store.latest_snapshot(learner_id)
        if snapshot is None:
            raise HTTPException(404, "未找到学习者状态")
        return {"learner_id": learner_id, "snapshot_id": snapshot.snapshot_id,
                "mastery": [m.model_dump() for m in snapshot.knowledge_state.kc_masteries],
                "misconceptions": [s.model_dump() for s in snapshot.misconception_hypotheses],
                "affect": snapshot.affect_motivation.model_dump(),
                "autonomy": snapshot.autonomy_index.model_dump(),
                "metacognition": snapshot.metacognition.model_dump(),
                "strategies": [t.model_dump() for t in snapshot.strategy_profile.tried_strategies],
                "evidence_refs": {}, "session": None}

    # ---------- 事件与治理 ----------

    @app.get("/api/events")
    def events(learner_id: str | None = None, limit: int = 200) -> list[dict]:
        learner_id = learner_id or settings.learner_id
        local_learner(learner_id)
        try:
            learning._authorize(learner_id)
        except ConsentDenied as exc:
            raise HTTPException(403,str(exc))
        return [e.model_dump(mode="json") for e in store.events_for_learner(learner_id, limit)]

    @app.get("/api/governance/hard-rules")
    def hard_rules() -> dict:
        from app.core.rules import HARD_RULES, registry_hash

        return {"hash": registry_hash(), "rules": HARD_RULES}

    # ---------- 学习路径推荐（docs/11 §6.5 v1 规则版） ----------

    @app.get("/api/path/recommend")
    def path_recommend(project_id: str, learner_id: str | None = None) -> dict:
        from app.learning.decisions import recommend

        learner_id = learner_id or settings.learner_id
        local_learner(learner_id)
        from app.learning.project_scope import require_project, ProjectScopeError
        try:
            require_project(store, learner_id, project_id, required=True)
        except ProjectScopeError as exc:
            raise HTTPException(404, str(exc)) from exc
        if learning.consent(learner_id) is None:
            raise HTTPException(403,"先建立学习目标并授权教学用途")
        try:
            graph = {asset.ref.asset_id: [ref.asset_id for ref in asset.prerequisite_refs] for asset in catalog.knowledge}
            return recommend(learning, learner_id, utcnow(), prerequisites=graph)
        except ConsentDenied as exc:
            raise HTTPException(403,str(exc))

    class PathAcceptIn(BaseModel):
        learner_id: str
        path: dict
        project_id: str

    @app.post("/api/path/propose")
    def path_propose(body: PathAcceptIn) -> dict:
        """Persist a recommendation as a reviewable proposal, never a draft."""
        local_learner(body.learner_id)
        from app.learning.project_scope import require_project, ProjectScopeError
        try:
            require_project(store, body.learner_id, body.project_id, writable=True, required=True)
        except ProjectScopeError as exc:
            raise HTTPException(409, str(exc)) from exc
        contract = store.latest_contract(body.learner_id, body.project_id)
        if contract is None:
            raise HTTPException(404, "无学习契约：路径必须挂在目标契约下（R-02）")
        proposed = plan_call(plans.propose, body.learner_id, contract.goal_contract_id,
                             {"path": body.path}, utcnow())
        return proposed.model_dump(mode="json")

    @app.post("/api/path/accept")
    def path_accept(body: PathAcceptIn) -> dict:
        local_learner(body.learner_id)
        from app.learning.project_scope import require_project, ProjectScopeError
        try:
            require_project(store, body.learner_id, body.project_id, writable=True, required=True)
        except ProjectScopeError as exc:
            raise HTTPException(409, str(exc)) from exc
        contract = store.latest_contract(body.learner_id, body.project_id)
        if contract is None:
            raise HTTPException(404, "无学习契约：路径必须挂在目标契约下（R-02）")
        # The learner may revise the recommendation before accepting it.  Keep
        # that submitted version in the draft and let its diff show the choice.
        proposed = plan_call(plans.propose, body.learner_id, contract.goal_contract_id,
                             {"path": body.path}, utcnow())
        plan = plan_call(plans.accept,body.learner_id,proposed.version_id,utcnow())
        from app.learning.workspace import WorkspaceService
        generated_task_ids = WorkspaceService(store, catalog).add_plan_tasks(
            body.learner_id, body.project_id, body.path
        )
        return {"ok": True,"version_id":plan.version_id,"status":plan.status,"diff":plan.diff,
                "generated_task_ids": generated_task_ids}

    class PlanActionIn(BaseModel):
        learner_id: str
        content: dict | None = None
        reason: str = "学习者修改"
        project_id: str

    def require_plan_project(learner_id: str, version_id: str, project_id: str | None, *, writable: bool = False):
        plan = plan_call(plans.get, learner_id, version_id)
        if project_id is not None and plan.project_id != project_id:
            raise HTTPException(404, "Plan is not in the selected project")
        if writable:
            from app.learning.project_scope import require_project, ProjectScopeError
            try:
                require_project(store, learner_id, plan.project_id, writable=True)
            except ProjectScopeError as exc:
                raise HTTPException(409, str(exc)) from exc
        return plan

    @app.get("/api/plans/{version_id}")
    def get_plan(version_id: str, learner_id: str | None = None, project_id: str | None = None):
        learner_id = learner_id or settings.learner_id
        local_learner(learner_id)
        return require_plan_project(learner_id, version_id, project_id).model_dump(mode="json")

    @app.post("/api/plans/{version_id}/sign")
    def sign_plan(version_id: str, body: PlanActionIn):
        local_learner(body.learner_id)
        candidate = require_plan_project(body.learner_id, version_id, body.project_id, writable=True)
        try:
            learning._authorize(body.learner_id)
        except ConsentDenied as exc:
            raise HTTPException(403,str(exc))
        from app.learning.workspace import WorkspaceService
        workspace = WorkspaceService(store, catalog)
        snapshot = candidate.content.get("project_plan")
        if snapshot is not None:
            try:
                workspace.validate_plan_snapshot(body.learner_id, candidate.project_id, snapshot)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
        signed = plan_call(plans.sign, body.learner_id, version_id, utcnow())
        try:
            applied_project = (workspace.apply_plan_snapshot(body.learner_id, signed.project_id, snapshot)
                               if snapshot is not None else None)
            planned_deadline = signed.content.get("deadline_at")
            if planned_deadline:
                contract = store.get_contract(signed.goal_contract_id)
                if contract is not None:
                    store.save_contract(contract.model_copy(update={
                        "external_deadline_refs": contract_deadlines("项目截止日期", planned_deadline,
                                                                     contract.external_deadline_refs)
                    }))
        except (ValueError, EvidenceConflict) as exc:
            # The signed version is durable and visible for recovery; the
            # snapshot has already been validated, so this is only a concurrent
            # project-write conflict rather than an implicit unsigned change.
            raise HTTPException(409, "Signed plan recorded; project arrangement needs refresh: " + str(exc)) from exc
        result = signed.model_dump(mode="json")
        if applied_project is not None:
            result["applied_project_revision"] = applied_project["revision"]
        return result

    @app.post("/api/plans/{version_id}/modify")
    def modify_plan(version_id: str, body: PlanActionIn):
        local_learner(body.learner_id)
        require_plan_project(body.learner_id, version_id, body.project_id, writable=True)
        require_purpose(body.learner_id)
        return plan_call(plans.modify,body.learner_id,version_id,body.content or {},body.reason,utcnow()).model_dump(mode="json")

    @app.post("/api/plans/{version_id}/reject")
    def reject_plan(version_id: str, body: PlanActionIn):
        local_learner(body.learner_id)
        require_plan_project(body.learner_id, version_id, body.project_id, writable=True)
        require_purpose(body.learner_id)
        return plan_call(plans.reject,body.learner_id,version_id,body.reason,utcnow()).model_dump(mode="json")

    # ---------- 成长证据（docs/11 §6 v1 聚合版） ----------

    @app.get("/api/evidence/{learner_id}")
    def evidence(learner_id: str) -> dict:
        local_learner(learner_id)
        try:
            learning._authorize(learner_id)
        except ConsentDenied as exc:
            raise HTTPException(403,str(exc))
        from app.learning.evidence import evidence_report

        return evidence_report(store, learner_id)

    # ---------- L0 进化演示 ----------

    @app.post("/api/evolution/digest")
    def run_digest() -> dict:
        require_purpose(settings.learner_id,"evolution")
        from evolution.l0_digest import build_learning_digest
        digest = build_learning_digest(learning,settings.learner_id)
        store.save_digest(digest)
        return digest

    @app.get("/api/evolution/digest/latest")
    def latest_digest() -> dict:
        require_purpose(settings.learner_id,"evolution")
        digest = store.latest_digest()
        if digest is None:
            raise HTTPException(404, "尚无沉淀报告，先运行一次 digest")
        return digest

    from app.gateway.learning_routes import install_learning_routes
    install_learning_routes(app,store,settings)
    from app.gateway.external_routes import install_external_routes
    install_external_routes(app,store,settings)
    from app.gateway.extended_routes import install_extended_routes
    install_extended_routes(app,store,settings)
    from app.gateway.dashboard_routes import install_dashboard_routes
    install_dashboard_routes(app,store,settings,catalog)
    from app.gateway.qualitative_routes import install_qualitative_routes
    install_qualitative_routes(app,store,settings)
    from app.gateway.governance_routes import install_governance_routes
    install_governance_routes(app,store,settings)
    return app
