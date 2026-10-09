"""API 网关（docs/07 §9 接口面）。v1 提供 JSON 端点 + 轻量 SSE 流式回合。

注意：本模块不能使用 `from __future__ import annotations`——闭包内定义的
Pydantic 请求模型会被 FastAPI 的类型解析降级为 query 参数。
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

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
    from app.learning.goal_scope import goal_supported, goal_target_kcs, plan_has_curriculum_scope, unsupported_goal_message
    catalog = load_catalog(settings)
    from app.learning.course_requests import CourseRequestService
    course_requests = CourseRequestService(store, catalog, Path(__file__).resolve().parents[2] / "seeds" / "knowledge")
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
    from app.learning.workspace import WorkspaceService
    workspace = WorkspaceService(store, catalog)

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
        return FileResponse(WEB_INDEX.with_name("learning.html"))

    @app.get("/prototype")
    def prototype() -> FileResponse:
        # Keep the legacy URL but never serve prototype's hard-coded scores.
        return FileResponse(WEB_INDEX.with_name("learning.html"))

    from app.gateway.workbench_routes import install_workbench_routes
    install_workbench_routes(app, store, settings)

    # ---------- 学习契约（docs/02 §3.1） ----------

    @app.get("/api/goals/coverage")
    def goal_coverage(goal_text: str, learner_id: str | None = None) -> dict:
        learner_id = learner_id or settings.learner_id
        require_purpose(learner_id)
        if not 2 <= len(goal_text.strip()) <= 120:
            raise HTTPException(422, "学习目标需为 2 至 120 个字符")
        targets = goal_target_kcs(goal_text, catalog)
        titles = {item.ref.asset_id: item.title for item in catalog.knowledge}
        return {"supported": bool(targets), "target_kcs": list(targets),
                "target_titles": [titles[kc] for kc in targets],
                "next_action": "create_contract" if targets else "request_course",
                "message": "已找到当前课程中的对应知识点" if targets else unsupported_goal_message(goal_text, catalog)}

    class CourseRequestIn(BaseModel):
        goal_text: str
        learner_id: str | None = None

    @app.post("/api/course-requests")
    def request_course(body: CourseRequestIn) -> dict:
        learner_id = body.learner_id or settings.learner_id
        require_purpose(learner_id)
        try:
            return course_requests.submit(learner_id, body.goal_text)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/course-requests")
    def list_course_requests(learner_id: str | None = None) -> dict:
        learner_id = learner_id or settings.learner_id
        require_purpose(learner_id)
        return {"requests": course_requests.list_for(learner_id)}

    class ContractIn(BaseModel):
        goal_text: str
        learner_id: str | None = None
        deadline_title: str | None = None
        deadline_at: str | None = None   # ISO 日期

    @app.post("/api/contracts")
    def create_contract(body: ContractIn) -> dict:
        learner_id = body.learner_id or settings.learner_id
        require_purpose(learner_id)
        target_kcs = goal_target_kcs(body.goal_text, catalog)
        if not target_kcs:
            raise HTTPException(422, unsupported_goal_message(body.goal_text, catalog))
        contract = GoalContract(
            learner_id=learner_id,
            goal_statement=GoalStatement(text=body.goal_text.strip(), authored_by="student"),
            success_criteria=[SuccessCriterion(kind="post_test", threshold=0.8,
                                               kc_refs=list(target_kcs))],
        )
        if body.deadline_title and body.deadline_at:
            contract.external_deadline_refs.append(ExternalDeadline(
                source="exam_calendar", title=body.deadline_title,
                due_at=utcnow() if not body.deadline_at else body.deadline_at))
        contract.status = "active"
        store.ensure_learner(learner_id)
        store.save_contract(contract)
        plan = PlanVersion(goal_contract_id=contract.goal_contract_id, status="draft",
                           change_reason="学习目标建立后的初始计划草案",
                           content={"bank_scope": list(target_kcs),
                                    "cadence": "每天 2 题 + 1 次阶段测试/周"},
                           confirmed_at=None)
        contract.plan_version_refs = [plan.version_id]
        store.save_plan_version(plan)
        store.save_contract(contract)
        return {"contract": contract.model_dump(), "plan_version": plan.model_dump()}

    @app.get("/api/contracts/latest")
    def latest_contract(learner_id: str | None = None) -> dict:
        require_purpose(learner_id or settings.learner_id)
        contract = store.latest_contract(learner_id or settings.learner_id)
        if contract is None:
            raise HTTPException(404, "尚未签署学习契约")
        return contract.model_dump()

    # ---------- 会话 ----------

    class SessionIn(BaseModel):
        session_type: str = "explore"     # explore | checkpoint
        learner_id: str | None = None
        contract_id: str | None = None
        project_id: str | None = None

    @app.post("/api/sessions")
    def create_session(body: SessionIn) -> dict:
        learner_id = body.learner_id or settings.learner_id
        require_purpose(learner_id)
        contract = store.get_contract(body.contract_id) if body.contract_id \
            else store.latest_contract(learner_id)
        if contract is not None and contract.learner_id != learner_id:
            raise HTTPException(403,"学习契约不属于当前学习者")
        if body.session_type == "checkpoint" and contract is None:
            raise HTTPException(400, "阶段测试需要先签署学习契约")
        if body.project_id:
            try:
                project = workspace.latest(learner_id, "project", body.project_id)
            except KeyError as exc:
                raise HTTPException(404, "Unknown project") from exc
            if project["content"]["archived"]:
                raise HTTPException(409, "Archived project")
        result = runtime_call(runtime.create, learner_id, contract, body.session_type)
        if body.project_id:
            with store.lock:
                workspace.attach_session_in_transaction(learner_id, body.project_id, result["session_id"])
        return result

    class MessageIn(BaseModel):
        text: str = ""
        answer: str | None = None
        attempt_id: str | None = None

    def _session(sid: str) -> TutorSession:
        require_purpose(settings.learner_id)
        return runtime_call(runtime.load, sid, settings.learner_id)

    @app.get("/api/sessions/{sid}")
    def session_state(sid: str):
        require_purpose(settings.learner_id)
        return runtime_call(runtime.public_state, sid, settings.learner_id)

    @app.post("/api/sessions/{sid}/messages")
    def send_message(sid: str, body: MessageIn) -> dict:
        require_purpose(settings.learner_id)
        return runtime_call(runtime.message, sid, settings.learner_id, text=body.text,
                            answer=body.answer, attempt_id=body.attempt_id)

    @app.post("/api/sessions/{sid}/messages/stream")
    async def send_message_stream(sid: str, body: MessageIn) -> StreamingResponse:
        require_purpose(_session(sid).learner_id)

        def sse(event: str, data: str) -> str:
            return f"event: {event}\ndata: {data}\n\n"

        async def gen():
            yield sse("status", "感知学生状态中…")
            result = await run_in_threadpool(send_message, sid, body)
            yield sse("status", "规则引擎裁决完成")
            import json as _json

            yield sse("reply", _json.dumps({
                "reply": result["reply"], "ui": result["ui"], "denial": result["denial"],
            }, ensure_ascii=False))

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/sessions/{sid}/reflection")
    def submit_reflection(sid: str, payload: dict) -> dict:
        require_purpose(settings.learner_id)
        return runtime_call(runtime.reflection, sid, settings.learner_id, payload)

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
    def path_recommend(learner_id: str | None = None) -> dict:
        from app.learning.decisions import recommend

        learner_id = learner_id or settings.learner_id
        local_learner(learner_id)
        if learning.consent(learner_id) is None:
            raise HTTPException(403,"先建立学习目标并授权教学用途")
        contract = store.latest_contract(learner_id)
        if contract is not None and not goal_supported(contract.goal_statement.text, catalog):
            raise HTTPException(422, unsupported_goal_message(contract.goal_statement.text, catalog))
        try:
            target_kcs = set(goal_target_kcs(contract.goal_statement.text, catalog)) if contract else None
            graph = {asset.ref.asset_id: [ref.asset_id for ref in asset.prerequisite_refs]
                     for asset in catalog.knowledge if target_kcs is None or asset.ref.asset_id in target_kcs}
            return recommend(learning, learner_id, utcnow(), prerequisites=graph, allowed_kcs=target_kcs)
        except ConsentDenied as exc:
            raise HTTPException(403,str(exc))

    class PathAcceptIn(BaseModel):
        learner_id: str
        path: dict

    @app.post("/api/path/accept")
    def path_accept(body: PathAcceptIn) -> dict:
        local_learner(body.learner_id)
        contract = store.latest_contract(body.learner_id)
        if contract is None:
            raise HTTPException(404, "无学习契约：路径必须挂在目标契约下（R-02）")
        if not goal_supported(contract.goal_statement.text, catalog):
            raise HTTPException(422, unsupported_goal_message(contract.goal_statement.text, catalog))
        if not isinstance(body.path, dict):
            raise HTTPException(422, "path must be an object")
        # Keep the exact client proposal, including an intentionally empty
        # proposal used by older clients; never silently replace it with a
        # freshly computed recommendation.
        proposed = plan_call(plans.propose,body.learner_id,contract.goal_contract_id,{"path":body.path},utcnow())
        plan = plan_call(plans.accept,body.learner_id,proposed.version_id,utcnow())
        return {"ok": True,"version_id":plan.version_id,"status":plan.status,"diff":plan.diff}

    class PlanActionIn(BaseModel):
        learner_id: str
        content: dict | None = None
        reason: str = "学习者修改"

    @app.get("/api/plans/{version_id}")
    def get_plan(version_id: str, learner_id: str | None = None):
        learner_id = learner_id or settings.learner_id
        local_learner(learner_id)
        return plan_call(plans.get,learner_id,version_id).model_dump(mode="json")

    @app.post("/api/plans/{version_id}/sign")
    def sign_plan(version_id: str, body: PlanActionIn):
        local_learner(body.learner_id)
        try:
            learning._authorize(body.learner_id)
        except ConsentDenied as exc:
            raise HTTPException(403,str(exc))
        contract = store.latest_contract(body.learner_id)
        plan = plan_call(plans.get, body.learner_id, version_id)
        if (contract is not None and plan.goal_contract_id == contract.goal_contract_id
                and not goal_supported(contract.goal_statement.text, catalog)
                and plan_has_curriculum_scope(plan.content)):
            raise HTTPException(422, unsupported_goal_message(contract.goal_statement.text, catalog))
        return plan_call(plans.sign,body.learner_id,version_id,utcnow()).model_dump(mode="json")

    @app.post("/api/plans/{version_id}/modify")
    def modify_plan(version_id: str, body: PlanActionIn):
        local_learner(body.learner_id)
        require_purpose(body.learner_id)
        return plan_call(plans.modify,body.learner_id,version_id,body.content or {},body.reason,utcnow()).model_dump(mode="json")

    @app.post("/api/plans/{version_id}/reject")
    def reject_plan(version_id: str, body: PlanActionIn):
        local_learner(body.learner_id)
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
    from app.gateway.workspace_feature_routes import install_workspace_feature_routes
    install_workspace_feature_routes(app,store,settings,catalog,llm)
    return app
