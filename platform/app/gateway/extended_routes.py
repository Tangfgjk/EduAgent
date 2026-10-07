"""Governed memory, development measures, and appeal recovery endpoints."""
from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.core.schema import utcnow
from app.learning.development_metrics import autonomy_behaviors, cognitive_change
from app.learning.memory import MemoryService
from app.learning.recovery import RecoveryJobs
from app.learning.service import ConsentDenied, LearningService
from app.learning.assets import load_catalog
from app.learning.project_scope import ProjectScopeError, require_project


class AppealIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=4000)
    appeal_id: str | None = None


class RecoveryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correction_id: str = Field(min_length=1)
    as_of: AwareDatetime | None = None


def install_extended_routes(app: FastAPI, store, settings) -> None:
    learning = LearningService(store)
    memory = MemoryService(store)
    jobs = RecoveryJobs(store)
    catalog = load_catalog(settings)

    @app.exception_handler(ConsentDenied)
    async def denied(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    def authorize(learner_id: str):
        if learner_id != settings.learner_id:
            raise HTTPException(403, "Local single-user workspace only")
        try:
            learning._authorize(learner_id)
        except ConsentDenied as exc:
            raise HTTPException(403, str(exc)) from exc

    @app.post("/api/learning/memory/{learner_id}/rebuild")
    def rebuild_memory(learner_id: str, as_of: AwareDatetime | None = None,
                       project_id: str | None = None):
        authorize(learner_id)
        try:
            require_project(store, learner_id, project_id)
        except ProjectScopeError as exc:
            raise HTTPException(404, str(exc)) from exc
        return memory.rebuild(learner_id, as_of or utcnow(), project_id)

    @app.get("/api/learning/memory/{learner_id}/{view_id}")
    def get_memory(learner_id: str, view_id: str, project_id: str | None = None):
        authorize(learner_id)
        try:
            require_project(store, learner_id, project_id)
            return memory.get(learner_id, view_id, project_id)
        except ProjectScopeError as exc:
            raise HTTPException(404, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/learning/recovery/{learner_id}")
    def pending_recovery(learner_id: str):
        authorize(learner_id)
        return dict(jobs=jobs.pending(learner_id))

    @app.post("/api/learning/recovery/{learner_id}/reconcile")
    def reconcile_recovery(learner_id: str):
        authorize(learner_id)
        return dict(jobs=jobs.reconcile(learner_id))

    @app.get("/api/learning/metrics/development/{learner_id}")
    def development_metrics(learner_id: str, split_at: AwareDatetime,
                            as_of: AwareDatetime | None = None, project_id: str | None = None):
        authorize(learner_id)
        try:
            require_project(store, learner_id, project_id)
        except ProjectScopeError as exc:
            raise HTTPException(404, str(exc)) from exc
        as_of = as_of or utcnow()
        if split_at >= as_of:
            raise HTTPException(400, "split_at must precede as_of")
        evidence, events = memory.sources(learner_id, as_of, project_id)
        return dict(cognitive=cognitive_change(evidence, learner_id=learner_id, as_of=as_of, split_at=split_at),
                    autonomy=autonomy_behaviors(evidence, events, learner_id=learner_id, as_of=as_of),
                    source_refs=dict(evidence=[e.evidence_id for e in evidence], events=[e["event_id"] for e in events]),
                    not_an_effect_claim=True)

    @app.post("/api/learning/appeals/{learner_id}")
    def create_appeal(learner_id: str, body: AppealIn):
        authorize(learner_id)
        try:
            return jobs.appeal(learner_id, body.appeal_id or "appeal:" + uuid4().hex, body.evidence_id, body.reason)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/learning/recovery/{learner_id}")
    def enqueue_recovery(learner_id: str, body: RecoveryIn):
        authorize(learner_id)
        try:
            return jobs.enqueue(learner_id, body.correction_id, as_of=body.as_of)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/learning/recovery/{learner_id}/{job_id}")
    def recovery_status(learner_id: str, job_id: str):
        authorize(learner_id)
        try:
            return jobs.get(learner_id, job_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/learning/recovery/{learner_id}/{job_id}/run")
    def run_recovery(learner_id: str, job_id: str):
        authorize(learner_id)
        try:
            graph = {asset.ref.asset_id:[ref.asset_id for ref in asset.prerequisite_refs] for asset in catalog.knowledge}
            return jobs.run(learner_id, job_id, graph)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/learning/recovery/{learner_id}/run-pending")
    def run_pending_recovery(learner_id: str):
        authorize(learner_id)
        graph={asset.ref.asset_id:[ref.asset_id for ref in asset.prerequisite_refs] for asset in catalog.knowledge}
        results=[]
        for job in jobs.pending(learner_id)[:20]:
            try:
                results.append(jobs.run(learner_id,job["job_id"],graph))
            except (KeyError,ValueError):
                results.append(jobs.get(learner_id,job["job_id"]))
        return dict(jobs=results,max_batch=20,automatically_signed=False)
