"""Local external-capability endpoints over authorized evidence and fixed sources."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.derived import DerivedRunner, RoleBudget
from app.agents.teach_student import TeachVirtualStudentRunner
from app.core.rules import ActionGovernor, GovernorContext
from app.core.schema import MentalStateSnapshot, utcnow
from app.learning.assets import load_catalog
from app.learning.retrieval import (
    LEXICAL_FEATURE_MODEL, RETRIEVAL_VERSIONS,
    LocalOCR, LocalRetrieval, ParseError, RetrievalContext,
)
from app.learning.service import ConsentDenied, LearningService
from app.learning.strategies import DEFAULT_STRATEGIES
from app.llm.client import BaseLLM, FakeLLM, OpenAICompatClient
from app.llm.registry import ProviderConfig, ProviderRegistry

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_ROOT = ROOT / "seeds" / "knowledge"
SOURCE_MAP = {
    "equation-balance-v1": ("等式性质知识-20261006.md", "MATH.G7.EQ.BALANCE"),
    "equation-solve-v1": ("方程求解知识-20261006.md", "MATH.G7.EQ.SOLVE"),
}
_DEMO_RESPONSES = {
    "prompter": {"observations": ["固定提示模板；不是对作品的模型判断。"],
                 "actions": [{"kind": "hint", "hint_level": 0, "text": "先说明你对等式两边做了什么运算，再检查是否保持相等。"}]},
    "skeptic": {"observations": ["固定怀疑者模板；不是对作品的模型判断。"],
                "actions": [{"kind": "question", "text": "把你得到的值代回原方程，两边相等吗？哪一步需要再次检查？"}]},
    "reviewer": {"observations": ["固定评审模板；不是作品评分或掌握结论。"],
                 "actions": [{"kind": "feedback", "text": "把每一步保持等式相等的理由写下来，再使用代回验算检查这个策略。"}]},
}


class RoleReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    student_work: str = Field(default="", max_length=4000)
    learner_id: str | None = None
    max_turns: int = Field(default=1, ge=1, le=1)
    max_tokens: int = Field(default=2000, ge=1, le=4000)
    deadline_ms: int = Field(default=3000, ge=1, le=30000)
    provider_id: Literal["offline", "configured"] = "offline"

    @model_validator(mode="after")
    def unique_evidence_ids(self):
        if len(set(self.evidence_ids)) != len(self.evidence_ids) or any(not ref.strip() for ref in self.evidence_ids):
            raise ValueError("Unique nonempty evidence IDs required")
        return self


class KnowledgeImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relative_path: str = Field(min_length=1, max_length=200)
    source_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    kc_refs: list[str] = Field(min_length=1, max_length=10)
    enable_ocr: bool = False


class TeachLessonIn(RoleReviewIn):
    lesson: str = Field(min_length=1,max_length=4000)


class RoundtableIn(RoleReviewIn):
    roles: tuple[Literal["prompter", "skeptic", "reviewer"], ...] = ("prompter", "skeptic", "reviewer")

    @model_validator(mode="after")
    def unique_roles(self):
        if not self.roles or len(set(self.roles)) != len(self.roles):
            raise ValueError("Unique nonempty roles required")
        return self


def install_external_routes(app: FastAPI, store, settings) -> None:
    service = LearningService(store)
    catalog = load_catalog(settings)
    known_kcs = {item.ref.asset_id for item in catalog.knowledge}
    retrieval = LocalRetrieval(KNOWLEDGE_ROOT, index_connection=store.conn, lock=store.lock)
    for source_id, (filename, kc) in SOURCE_MAP.items():
        if kc in known_kcs:
            retrieval.import_document(KNOWLEDGE_ROOT / filename, source_id=source_id, kc_refs=[kc])
    with store.lock:
        store.conn.execute("CREATE TABLE IF NOT EXISTS derived_daily_budget(learner_id TEXT,day TEXT,turns INTEGER,tokens INTEGER,hints INTEGER,PRIMARY KEY(learner_id,day))")
        store.conn.commit()
    public_configs = [ProviderConfig(provider_id="offline", kind="fake", model="educational-template-v1")]
    configured_extra_body: dict = {}
    if settings.llm_api_key:
        if settings.llm_extra_body_json:
            try:
                parsed_extra_body = json.loads(settings.llm_extra_body_json)
                if isinstance(parsed_extra_body, dict):
                    configured_extra_body = parsed_extra_body
            except ValueError:
                pass
        public_configs.append(ProviderConfig(provider_id="configured", model=settings.llm_model,
            base_url=settings.llm_base_url, api_key_env="RSI_LLM_API_KEY", timeout_seconds=30,
            fallback_provider="offline"))

    def authorized(learner_id=None):
        learner_id = learner_id or settings.learner_id
        if learner_id != settings.learner_id:
            raise HTTPException(403, "Local single-user workspace only")
        try:
            return learner_id, service.evidences(learner_id)
        except ConsentDenied as exc:
            raise HTTPException(403, str(exc)) from exc

    def audit(learner_id, payload):
        with store.lock:
            authorized(learner_id)
            store.conn.execute("INSERT INTO learning_audit(learner_id,payload) VALUES (?,?)",
                               (learner_id, json.dumps(payload, ensure_ascii=False)))
            store.conn.commit()

    def selected_evidence(body):
        learner_id, evidences = authorized(body.learner_id)
        lookup = {evidence.evidence_id: evidence for evidence in evidences}
        if any(ref not in lookup for ref in body.evidence_ids):
            raise HTTPException(403, "Evidence is unavailable or unauthorized")
        selected = [lookup[ref] for ref in body.evidence_ids]
        replaced = {evidence.supersedes for evidence in evidences if evidence.supersedes}
        if any(evidence.evidence_id in replaced for evidence in selected):
            raise HTTPException(409, "Use the effective evidence revision")
        kc_refs = sorted({kc for evidence in selected for kc in evidence.kc_refs})
        if not set(kc_refs) <= known_kcs:
            raise HTTPException(403, "Evidence KC is outside the local catalog")
        if body.provider_id == "configured" and not settings.llm_api_key:
            raise HTTPException(503, "Configured provider unavailable; choose offline explicitly")
        return learner_id,selected,kc_refs

    def record_usage(learner_id,body,reserve_hint=False):
        day = utcnow().date().isoformat()
        with store.lock:
            authorized(learner_id)
            store.conn.execute("BEGIN IMMEDIATE")
            try:
                current = store.conn.execute("SELECT turns,tokens,hints FROM derived_daily_budget WHERE learner_id=? AND day=?",(learner_id,day)).fetchone()
                turns,tokens,hints = tuple(current) if current else (0,0,0)
                store.conn.execute("INSERT INTO derived_daily_budget VALUES (?,?,?,?,?) ON CONFLICT(learner_id,day) DO UPDATE SET turns=excluded.turns,tokens=excluded.tokens,hints=excluded.hints",(learner_id,day,turns+body.max_turns,tokens+body.max_tokens,hints+int(reserve_hint)))
                store.conn.commit()
            except Exception:
                store.conn.rollback()
                raise
        return day

    def governed_client(body,context,report_id,demo_response):
        registry = ProviderRegistry()
        registry.register(public_configs[0],client=FakeLLM([json.dumps(demo_response,ensure_ascii=False)]))
        if body.provider_id == "configured":
            registry.register(public_configs[1], client=OpenAICompatClient(settings.llm_base_url, settings.llm_api_key,
                              settings.llm_model, timeout=min(30,body.deadline_ms / 1000),
                              extra_body={**configured_extra_body, "max_tokens":body.max_tokens}))
        class RoleClient(BaseLLM):
            last_result = None
            def complete(self,messages,temperature=.2):
                self.last_result=registry.generate(body.provider_id,messages,governor=ActionGovernor(),
                    context=context,request_id=report_id,temperature=temperature)
                return self.last_result.text
        return registry,RoleClient()

    @app.get("/api/providers")
    def providers(learner_id: str | None = None):
        authorized(learner_id)
        return dict(providers=[config.model_dump(mode="json", exclude={"api_key_env"}) for config in public_configs],
                    external_model_smoke="not_executed", role_execution_mode="deterministic_demo")

    @app.get("/api/knowledge/search")
    def knowledge_search(q: str = Query(min_length=1, max_length=1000), kc_id: str = Query(min_length=1),
                         learner_id: str | None = None, mode: Literal["bm25", "hashed_lexical", "vector", "hybrid"] = "bm25"):
        learner_id, _ = authorized(learner_id)
        if kc_id not in known_kcs:
            raise HTTPException(403, "KC is outside the authorized local catalog")
        context = RetrievalContext(kc_refs=[kc_id], mode=mode)
        version = RETRIEVAL_VERSIONS[context.mode]
        results = []
        for item in retrieval.retrieve(q, context):
            if item.grounding_status != "grounded":
                continue
            result = item.model_dump(mode="json")
            result.update(source_url=f"/api/knowledge/source/{item.source_id}?source_version={item.source_version}",
                          teaching_decision="requires_verifier_and_governor")
            results.append(result)
        audit(learner_id, dict(kind="knowledge_retrieval", query_sha256=hashlib.sha256(q.encode()).hexdigest(),
            kc_refs=[kc_id], source_refs=[dict(source_id=item["source_id"], source_version=item["source_version"],
            chunk_id=item["chunk_id"], citation=item["citation"]) for item in results],
            retrieval_version=version, retrieval_mode=context.mode, requested_mode=mode,
            deprecated_mode_alias=mode == "vector", at=utcnow().isoformat(), candidate_only=True))
        return dict(results=results, candidate_only=True, verified_teaching_claim=False,
                    retrieval_mode=context.mode, requested_mode=mode, retrieval_version=version,
                    deprecated_mode_alias=mode == "vector", feature_model=LEXICAL_FEATURE_MODEL,
                    vector_model=LEXICAL_FEATURE_MODEL)

    @app.post("/api/knowledge/import")
    def knowledge_import(body: KnowledgeImportIn):
        nonlocal retrieval
        learner_id, _ = authorized()
        if not set(body.kc_refs) <= known_kcs or len(set(body.kc_refs)) != len(body.kc_refs):
            raise HTTPException(403, "KC is outside the authorized local catalog")
        relative = Path(body.relative_path)
        path = (KNOWLEDGE_ROOT / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(KNOWLEDGE_ROOT.resolve()):
            raise HTTPException(403, "Knowledge source must stay inside the fixed local root")
        previous = next((source for source in retrieval.sources() if source.source_id == body.source_id),None)
        if previous is not None and Path(previous.path).resolve() != path:
            raise HTTPException(409,"Source identity cannot be reassigned to another file")
        importer = LocalRetrieval(KNOWLEDGE_ROOT, index_connection=store.conn, lock=store.lock,
                                  ocr=LocalOCR() if body.enable_ocr else None)
        try:
            imported = importer.import_document(path, source_id=body.source_id, kc_refs=body.kc_refs,
                                                before_publish=lambda: authorized(learner_id))
        except ParseError as exc:
            raise HTTPException(422, str(exc)) from exc
        # Reload the persistent snapshot after successful publication.
        retrieval = LocalRetrieval(KNOWLEDGE_ROOT, index_connection=store.conn, lock=store.lock)
        audit(learner_id, dict(kind="knowledge_import", source=imported.model_dump(mode="json"),
                              kc_refs=body.kc_refs, at=utcnow().isoformat(), candidate_only=True))
        return dict(source=imported.model_dump(mode="json"), candidate_only=True)

    @app.get("/api/knowledge/source/{source_id}")
    def source(source_id: str, learner_id: str | None = None, source_version: str | None = None):
        authorized(learner_id)
        imported = next((item for item in retrieval.sources() if item.source_id == source_id), None)
        if imported is None:
            raise HTTPException(404, "Source is not indexed")
        if source_version is not None and source_version != imported.source_version:
            raise HTTPException(409,"Requested source revision is unavailable; historical content must not be substituted")
        path = Path(imported.path).resolve()
        if not path.is_relative_to(KNOWLEDGE_ROOT.resolve()) or not path.is_file():
            raise HTTPException(404, "Indexed source unavailable")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != imported.source_version:
            raise HTTPException(409, "Source changed; rebuild the knowledge index")
        if path.suffix.lower() == ".pdf":
            from fastapi.responses import Response
            return Response(content, media_type="application/pdf", headers={"X-Source-Version": imported.source_version})
        return PlainTextResponse(content.decode("utf-8"), headers={"X-Source-Version": imported.source_version})

    @app.post("/api/roles/{role}/review")
    def role_review(role: Literal["prompter", "skeptic", "reviewer"], body: RoleReviewIn):
        learner_id,selected,kc_refs=selected_evidence(body)
        day=record_usage(learner_id,body,reserve_hint=role == "prompter")
        snapshot = store.latest_snapshot(learner_id) or MentalStateSnapshot(learner_id=learner_id)
        context = GovernorContext(snapshot=snapshot, ladder_pos=0, hints_used=0,
                                  hint_budget=3, frustration_streak=0, artifact_present=True)
        report_id = uuid4().hex
        registry,role_client=governed_client(body,context,report_id,_DEMO_RESPONSES[role])
        report = DerivedRunner(role_client).run(role, session_id="external-role-review",
                 kc_refs=kc_refs, evidence_refs=body.evidence_ids, student_work=body.student_work,
                 governor=ActionGovernor(), context=context,
                 budget=RoleBudget(max_turns=body.max_turns, max_tokens=body.max_tokens, deadline_ms=body.deadline_ms))
        registry.close()
        source_refs = [dict(source_id=source.source_id, source_version=source.source_version)
                       for source in retrieval.sources() if set(source.kc_refs).intersection(kc_refs)]
        provenance = role_client.last_result.provenance if role_client.last_result else registry.provenance(body.provider_id, report_id)
        execution_mode = "deterministic_demo" if provenance["provider_id"] == "offline" else "configured_provider"
        for action in report.actions:
            action.policy_provenance.update(provenance, source_refs=source_refs,
                source_usage="contextual_reference_only", execution_mode=execution_mode)
        response = dict(report_id=report_id, report=report.model_dump(mode="json"),
                        provenance=provenance, source_refs=source_refs, source_usage="contextual_reference_only",
                        governor_budget_scope="per_report", budget_day=day,
                        execution_mode=execution_mode, external_model_smoke="not_executed")
        audit(learner_id, dict(kind="derived_role_report", learner_id=learner_id, evidence_refs=body.evidence_ids,
                              kc_refs=kc_refs, at=utcnow().isoformat(), **response))
        return response

    @app.get("/api/strategies")
    def strategies(kc_id: str, learner_id: str | None = None):
        authorized(learner_id)
        if kc_id not in known_kcs:
            raise HTTPException(403,"KC is outside the authorized local catalog")
        return dict(cards=[card.model_dump(mode="json") for card in DEFAULT_STRATEGIES.for_kc(kc_id)],
                    candidate_only=True,activation="manual_review_required")

    @app.post("/api/teach-student/lesson")
    def teach_student(body: TeachLessonIn):
        learner_id,selected,kc_refs=selected_evidence(body)
        day=record_usage(learner_id,body)
        snapshot=store.latest_snapshot(learner_id) or MentalStateSnapshot(learner_id=learner_id)
        context=GovernorContext(snapshot=snapshot,ladder_pos=0,hints_used=0,hint_budget=3,
                                 frustration_streak=0,artifact_present=True)
        report_id=uuid4().hex
        demo=dict(misconception="固定虚拟学生模板：只在等式一边运算。",rationale="此为待学习者检验的模拟误区，不是模型评分。",
                  next_question="你能教我为什么必须对等式两边同时做同样的运算吗？")
        registry,client=governed_client(body,context,report_id,demo)
        try:
            report=TeachVirtualStudentRunner(client).run(session_id="teach-virtual-student",kc_refs=kc_refs,
                artifact_refs=[item.artifact_ref for item in selected],lesson=body.lesson,
                governor=ActionGovernor(),context=context,max_output_bytes=body.max_tokens,deadline_ms=body.deadline_ms)
        finally:
            registry.close()
        provenance=client.last_result.provenance if client.last_result else registry.provenance(body.provider_id,report_id)
        mode="deterministic_demo" if provenance["provider_id"] == "offline" else "configured_provider"
        report.provenance.update(provenance,execution_mode=mode,evidence_refs=body.evidence_ids)
        for action in report.actions:
            action.policy_provenance.update(provenance,execution_mode=mode,evidence_refs=body.evidence_ids)
        response=dict(report_id=report_id,report=report.model_dump(mode="json"),provenance=provenance,
            execution_mode=mode,external_model_smoke="not_executed",governor_budget_scope="per_report",budget_day=day,
            no_learning_state_write=True)
        audit(learner_id,dict(kind="teach_virtual_student_report",evidence_refs=body.evidence_ids,kc_refs=kc_refs,
                              at=utcnow().isoformat(),**response))
        return response

    @app.post("/api/roundtable/review")
    def roundtable_review(body: RoundtableIn):
        single = RoleReviewIn.model_validate(body.model_dump(exclude={"roles"}))
        reports = [role_review(role,single) for role in body.roles]
        learner_id,_ = authorized(body.learner_id)
        response = dict(reports=reports,candidate_only=True,no_learning_state_write=True,
                        agreement="unassessed_candidates_require_verification")
        audit(learner_id,dict(kind="roundtable_report",report_ids=[report["report_id"] for report in reports],
                              at=utcnow().isoformat(),candidate_only=True))
        return response
