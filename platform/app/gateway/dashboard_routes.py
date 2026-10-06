"""Explicitly shared teacher/parent summaries and versioned project workspace."""
from __future__ import annotations

import hmac
import json
from typing import Literal
from functools import wraps

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.learning.service import ConsentDenied, LearningService
from app.learning.workspace import ProjectContent, WorkspaceService
from app.core.schema import utcnow


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: ProjectContent
    record_id: str | None = None
    expected_revision: int = Field(default=0, ge=0)


class ShareIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audiences: list[Literal["teacher", "parent"]]
    expected_revision: int = Field(default=0, ge=0)


def install_dashboard_routes(app: FastAPI, store, settings, catalog) -> None:
    learning = LearningService(store)
    workspace = WorkspaceService(store, catalog)

    def locked(method):
        @wraps(method)
        def guarded(*args, **kwargs):
            with store.lock:
                return method(*args, **kwargs)
        return guarded

    def shared_snapshot(method):
        @wraps(method)
        def guarded(*args, **kwargs):
            with store.lock:
                # Serialize authorization and the complete shared response against
                # revocations through other connections, not only this Store lock.
                store.conn.execute("BEGIN IMMEDIATE")
                try:
                    result = method(*args, **kwargs)
                    store.conn.commit()
                    return result
                except Exception:
                    store.conn.rollback()
                    raise
        return guarded

    @app.exception_handler(ConsentDenied)
    async def denied(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    def local(learner_id):
        if learner_id != settings.learner_id:
            raise HTTPException(403, "Local single-user workspace only")

    def teaching(learner_id):
        local(learner_id)
        try:
            return learning.evidences(learner_id)
        except ConsentDenied as exc:
            raise HTTPException(403, str(exc)) from exc

    def token(value, expected, message):
        if not expected or not value or not hmac.compare_digest(value, expected):
            raise HTTPException(403, message)

    def shared(learner_id, audience):
        try:
            workspace.require_sharing(learner_id, audience)
        except KeyError:
            raise HTTPException(403, "Explicit current audience sharing required")
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    @app.get("/api/learning/projects/{learner_id}")
    def projects(learner_id: str):
        local(learner_id)
        try:
            return dict(projects=workspace.latest(learner_id, "project"))
        except ConsentDenied as exc:
            raise HTTPException(403, str(exc)) from exc

    @app.post("/api/learning/projects/{learner_id}")
    def save_project(learner_id: str, body: ProjectIn):
        local(learner_id)
        try:
            return workspace.project(learner_id, body.content, record_id=body.record_id,
                                    expected_revision=body.expected_revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ConsentDenied as exc:
            raise HTTPException(403, str(exc)) from exc

    @app.post("/api/learning/sharing/{learner_id}")
    @locked
    def share(learner_id: str, body: ShareIn):
        local(learner_id)
        try:
            return workspace.share(learner_id, body.audiences, body.expected_revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/learning/sharing/{learner_id}")
    def sharing(learner_id: str):
        local(learner_id)
        try:
            return workspace.latest(learner_id, "sharing", "sharing:" + learner_id)
        except KeyError:
            raise HTTPException(404, "No explicit audience sharing record")

    @app.get("/api/learning/dashboard/{learner_id}")
    @shared_snapshot
    def dashboard(learner_id: str, audience: Literal["teacher", "parent"] = Query(...),
                  x_teacher_token: str = Header(default=""), x_parent_token: str = Header(default="")):
        if audience == "teacher":
            token(x_teacher_token, settings.local_teacher_token, "Authorized local teacher token required")
        else:
            token(x_parent_token, getattr(settings, "local_parent_token", ""), "Authorized local parent token required")
        values = teaching(learner_id)
        shared(learner_id, audience)
        states = learning.masteries(learner_id)
        retentions = learning.retentions(learner_id)
        projects_value = workspace.latest(learner_id, "project")
        base = dict(audience=audience, learner_id=learner_id, source_kind="synthetic_or_local_observations",
                    not_an_effect_claim=True, project_count=len(projects_value),
                    mastery_summary=[dict(kc_id=s.kc_id, state_version=s.state_version,
                                         p_mastery=s.p_mastery, confidence=s.confidence) for s in states],
                    review_due_count=sum(r.next_review_at is not None and r.next_review_at <= utcnow() for r in retentions),
                    evidence_count=len(values))
        if audience == "parent":
            base["evidence_count"] = len(values)
            base["active_project_titles"] = [p["content"]["title"] for p in projects_value if not p["content"].get("archived")]
            return base
        appeals = []
        with store.lock:
            for row in store.conn.execute("SELECT payload FROM learning_appeals WHERE learner_id=? ORDER BY rowid", (learner_id,)):
                appeals.append(json.loads(row[0]))
        base.update(evidence_refs=[e.evidence_id for e in values],
                    evidence_summary=[dict(evidence_id=e.evidence_id, kc_refs=e.kc_refs,
                                          verdict_status=e.verdict_status, confidence=e.confidence,
                                          occurred_at=e.occurred_at.isoformat()) for e in values],
                    appeals=appeals, projects=projects_value)
        return base

    @app.get("/api/learning/teacher/{learner_id}/evidence/{evidence_id}")
    @shared_snapshot
    def teacher_evidence(learner_id: str, evidence_id: str, x_teacher_token: str = Header(default="")):
        token(x_teacher_token,settings.local_teacher_token,"Authorized local teacher token required")
        values=teaching(learner_id)
        shared(learner_id,"teacher")
        evidence=next((e for e in values if e.evidence_id==evidence_id),None)
        if evidence is None: raise HTTPException(404,"Unknown authorized learner evidence")
        return evidence.model_dump(mode="json")
