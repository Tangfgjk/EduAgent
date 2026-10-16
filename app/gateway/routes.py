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
               store: Store | None = None) -> FastAPI:
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
    sessions: dict[str, TutorSession] = {}

    app = FastAPI(title="RSI 教育智能体平台", version="0.1.0 (M0+L0)")

    # ---------- 静态单页 ----------

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_INDEX)

    # ---------- 学习契约（docs/02 §3.1） ----------

    class ContractIn(BaseModel):
        goal_text: str
        learner_id: str | None = None
        deadline_title: str | None = None
        deadline_at: str | None = None   # ISO 日期

    @app.post("/api/contracts")
    def create_contract(body: ContractIn) -> dict:
        learner_id = body.learner_id or settings.learner_id
        contract = GoalContract(
            learner_id=learner_id,
            goal_statement=GoalStatement(text=body.goal_text.strip(), authored_by="student"),
            success_criteria=[SuccessCriterion(kind="post_test", threshold=0.8,
                                               kc_refs=["MATH.G7.EQ.SOLVE",
                                                        "MATH.G7.EQ.APPLY"])],
        )
        if body.deadline_title and body.deadline_at:
            contract.external_deadline_refs.append(ExternalDeadline(
                source="exam_calendar", title=body.deadline_title,
                due_at=utcnow() if not body.deadline_at else body.deadline_at))
        contract.status = "active"
        store.ensure_learner(learner_id)
        store.save_contract(contract)
        plan = PlanVersion(goal_contract_id=contract.goal_contract_id, status="confirmed",
                           change_reason="契约签署时的初始计划",
                           content={"bank_scope": ["MATH.G7.EQ.SOLVE", "MATH.G7.EQ.SETUP",
                                                   "MATH.G7.EQ.APPLY"],
                                    "cadence": "每天 2 题 + 1 次阶段测试/周"},
                           confirmed_at=utcnow())
        contract.plan_version_refs = [plan.version_id]
        store.save_plan_version(plan)
        store.save_contract(contract)
        return {"contract": contract.model_dump(), "plan_version": plan.model_dump()}

    @app.get("/api/contracts/latest")
    def latest_contract(learner_id: str | None = None) -> dict:
        contract = store.latest_contract(learner_id or settings.learner_id)
        if contract is None:
            raise HTTPException(404, "尚未签署学习契约")
        return contract.model_dump()

    # ---------- 会话 ----------

    class SessionIn(BaseModel):
        session_type: str = "explore"     # explore | checkpoint
        learner_id: str | None = None
        contract_id: str | None = None

    @app.post("/api/sessions")
    def create_session(body: SessionIn) -> dict:
        learner_id = body.learner_id or settings.learner_id
        contract = store.get_contract(body.contract_id) if body.contract_id \
            else store.latest_contract(learner_id)
        if body.session_type == "checkpoint" and contract is None:
            raise HTTPException(400, "阶段测试需要先签署学习契约")
        session = TutorSession(store, llm, learner_id, contract=contract,
                               session_type=body.session_type)
        sessions[session.session_id] = session
        result = session.start()
        return {"session_id": session.session_id, "reply": result.reply,
                "ui": result.ui, "denial": result.denial}

    class MessageIn(BaseModel):
        text: str = ""
        answer: str | None = None

    def _session(sid: str) -> TutorSession:
        session = sessions.get(sid)
        if session is None:
            raise HTTPException(404, "会话不存在（内存注册表）")
        return session

    @app.post("/api/sessions/{sid}/messages")
    def send_message(sid: str, body: MessageIn) -> dict:
        session = _session(sid)
        result = session.handle_turn(text=body.text, answer=body.answer)
        return {"reply": result.reply, "ui": result.ui,
                "denial": result.denial, "events": len(result.events)}

    @app.post("/api/sessions/{sid}/messages/stream")
    async def send_message_stream(sid: str, body: MessageIn) -> StreamingResponse:
        session = _session(sid)

        def sse(event: str, data: str) -> str:
            return f"event: {event}\ndata: {data}\n\n"

        async def gen():
            yield sse("status", "感知学生状态中…")
            result = await run_in_threadpool(session.handle_turn, body.text, body.answer)
            yield sse("status", "规则引擎裁决完成")
            import json as _json

            yield sse("reply", _json.dumps({
                "reply": result.reply, "ui": result.ui, "denial": result.denial,
            }, ensure_ascii=False))

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/sessions/{sid}/reflection")
    def submit_reflection(sid: str, payload: dict) -> dict:
        return _session(sid).submit_reflection(payload)

    # ---------- 我的镜子（open learner model） ----------

    @app.get("/api/mirror/{learner_id}")
    def mirror(learner_id: str) -> dict:
        for session in sessions.values():
            if session.learner_id == learner_id:
                return session.mirror()
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
        return [e.model_dump(mode="json") for e in store.events_for_learner(learner_id, limit)]

    @app.get("/api/governance/hard-rules")
    def hard_rules() -> dict:
        from app.core.rules import HARD_RULES, registry_hash

        return {"hash": registry_hash(), "rules": HARD_RULES}

    # ---------- 学习路径推荐（docs/11 §6.5 v1 规则版） ----------

    @app.get("/api/path/recommend")
    def path_recommend(learner_id: str | None = None) -> dict:
        from app.learning.path import recommend_path

        learner_id = learner_id or settings.learner_id
        snapshot = store.latest_snapshot(learner_id)
        if snapshot is None:
            raise HTTPException(404, "该学习者尚无状态快照：先创建会话产生一次交互")
        contract = store.latest_contract(learner_id)
        return recommend_path(snapshot, contract)

    class PathAcceptIn(BaseModel):
        learner_id: str
        path: dict

    @app.post("/api/path/accept")
    def path_accept(body: PathAcceptIn) -> dict:
        contract = store.latest_contract(body.learner_id)
        if contract is None:
            raise HTTPException(404, "无学习契约：路径必须挂在目标契约下（R-02）")
        plan = PlanVersion(
            goal_contract_id=contract.goal_contract_id,
            status="confirmed", change_reason="采纳学习路径推荐（PathRecommendation@1）",
            content={"path": body.path}, confirmed_at=utcnow(),
        )
        store.save_plan_version(plan)
        return {"ok": True, "version_id": plan.version_id, "status": plan.status}

    # ---------- 成长证据（docs/11 §6 v1 聚合版） ----------

    @app.get("/api/evidence/{learner_id}")
    def evidence(learner_id: str) -> dict:
        from app.learning.evidence import evidence_report

        return evidence_report(store, learner_id)

    # ---------- L0 进化演示 ----------

    @app.post("/api/evolution/digest")
    def run_digest() -> dict:
        from evolution.l0_digest import build_digest

        digest = build_digest(store)
        store.save_digest(digest)
        return digest

    @app.get("/api/evolution/digest/latest")
    def latest_digest() -> dict:
        digest = store.latest_digest()
        if digest is None:
            raise HTTPException(404, "尚无沉淀报告，先运行一次 digest")
        return digest

    return app
