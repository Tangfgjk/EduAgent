"""Teacher-reviewed rubric evidence as an append-only correction."""
from __future__ import annotations

import hmac
import hashlib
import json
from typing import Literal
from functools import wraps
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.learning.assets import load_catalog
from app.learning.qualitative import RubricReview, score_review
from app.learning.service import ConsentDenied, EvidenceConflict, LearningService
from app.learning.schema import LearningEvidence
from app.learning.project_scope import ProjectScopeError, require_project
from app.learning.workspace import WorkspaceService
from app.core.schema import utcnow


class QualitativeReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(min_length=1)
    rubric_id: str = Field(min_length=1)
    rubric_version: str = Field(min_length=1)
    dimension_scores: dict[str, float]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=4000)
    assessor_version: str = Field(min_length=1)
    correction_id: str = Field(default_factory=lambda: uuid4().hex)


class ArtifactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment_id: str = Field(min_length=1)
    assessment_version: str = "1.0.0"
    attempt_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=8000)
    assistance_mode: Literal["none", "hint", "answer"] = "none"
    hint_level: int = Field(default=0, ge=0, le=3)
    answer_exposed: bool = False
    project_id: str | None = None
    task_id: str | None = Field(default=None, min_length=1, max_length=100)


def install_qualitative_routes(app: FastAPI, store, settings) -> None:
    service = LearningService(store)
    catalog = load_catalog(settings)
    def locked(method):
        @wraps(method)
        def guarded(*args, **kwargs):
            with store.lock:
                return method(*args, **kwargs)
        return guarded
    with store.lock:
        store.conn.execute("CREATE TABLE IF NOT EXISTS qualitative_artifacts(artifact_id TEXT PRIMARY KEY,learner_id TEXT NOT NULL,payload TEXT NOT NULL)")
        store.conn.commit()

    @app.post("/api/learning/qualitative/{learner_id}/artifacts")
    def submit_artifact(learner_id: str, body: ArtifactIn):
        if learner_id != settings.learner_id:
            raise HTTPException(403, "Local single-user workspace only")
        asset = next((a for a in catalog.assessments if a.ref.asset_id==body.assessment_id and a.ref.version==body.assessment_version), None)
        if asset is None or asset.kind != "explanation":
            raise HTTPException(400, "A configured explanation assessment is required")
        with store.lock:
            service._authorize(learner_id)
            try:
                require_project(store, learner_id, body.project_id, writable=True)
            except ProjectScopeError as exc:
                raise HTTPException(404, str(exc)) from exc
            if body.task_id:
                if not body.project_id:
                    raise HTTPException(400, "Project task artifacts require project_id")
                try:
                    task = WorkspaceService(store, catalog).task_for_project(learner_id, body.project_id, body.task_id)
                except ValueError as exc:
                    raise HTTPException(404, str(exc)) from exc
                if task.status in {"done", "skipped"}:
                    raise HTTPException(409, "This project task is already closed")
            fingerprint = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
            key = "qualitative-artifact:" + body.attempt_id
            old = store.conn.execute("SELECT content_hash,payload FROM learning_api_receipts WHERE learner_id=? AND attempt_id=?",(learner_id,key)).fetchone()
            if old:
                if old[0] != fingerprint: raise HTTPException(409, "Attempt content conflict")
                return json.loads(old[1])
            identity = hashlib.sha256(f"{learner_id}:{key}".encode()).hexdigest()
            consent = service.consent(learner_id)
            evidence = LearningEvidence(evidence_id="qualitative-artifact:"+identity,learner_id=learner_id,
                project_id=body.project_id,project_task_id=body.task_id,
                session_id="qualitative-workspace",kc_refs=[ref.asset_id for ref in asset.kc_refs],attempt_id=key,
                artifact_ref="artifact:"+identity,verdict_ref="pending-review:"+identity,verdict_status="unverifiable",
                verifier_version="teacher-review-pending-v1",confidence=0,occurred_at=utcnow(),consent_scope=consent["scopes"],
                consent_version=consent["version"],authorization_source=consent["source"],hint_level=body.hint_level,
                assistance_mode=body.assistance_mode,answer_exposed=body.answer_exposed,assessment_id=asset.ref.asset_id,
                assessment_version=asset.ref.version,assessment_kind="practice",rubric_version=asset.rubric_ref.version,
                difficulty_band=asset.difficulty_band,verifier_details=dict(source="submitted-artifact-awaiting-review",
                    assessment_provenance=asset.provenance,comparison_group=asset.comparison_group,rubric_ref=asset.rubric_ref.key))
            def result_for(transition):
                return dict(evidence_id=evidence.evidence_id,artifact_ref=evidence.artifact_ref,verdict_status="unverifiable",
                            pending_teacher_review=True,transition=transition)
            def save(stage):
                if stage=="receipt":
                    store.conn.execute("INSERT INTO qualitative_artifacts VALUES (?,?,?)",(evidence.artifact_ref,learner_id,
                        json.dumps(dict(content=body.content,assessment_ref=asset.ref.key,content_sha256=hashlib.sha256(body.content.encode()).hexdigest()),ensure_ascii=False)))
            try:
                transition = service.consume(evidence,receipt=(key,fingerprint,result_for),fault=save)
                return result_for(transition)
            except EvidenceConflict as exc:
                # A second connection can lose the receipt race after constructing
                # its wall-clock evidence. Re-read the stable API receipt before
                # surfacing a conflict, making same-attempt retries idempotent.
                with store.lock:
                    service._authorize(learner_id)
                    prior = store.conn.execute("SELECT content_hash,payload FROM learning_api_receipts WHERE learner_id=? AND attempt_id=?", (learner_id,key)).fetchone()
                if prior and prior[0] == fingerprint:
                    return json.loads(prior[1])
                raise HTTPException(409, str(exc)) from exc

    @app.get("/api/learning/qualitative/{learner_id}/artifacts/{evidence_id}")
    def artifact(learner_id: str, evidence_id: str, x_teacher_token: str = Header(default="")):
        if learner_id!=settings.learner_id or not settings.local_teacher_token or not hmac.compare_digest(x_teacher_token,settings.local_teacher_token):
            raise HTTPException(403,"Authorized local teacher token required")
        with store.lock:
            evidence=next((e for e in service.evidences(learner_id) if e.evidence_id==evidence_id),None)
            if evidence is None: raise HTTPException(404,"Unknown learner evidence")
            row=store.conn.execute("SELECT payload FROM qualitative_artifacts WHERE learner_id=? AND artifact_id=?",(learner_id,evidence.artifact_ref)).fetchone()
            if row is None: raise HTTPException(404,"No submitted qualitative artifact")
            return dict(evidence_id=evidence_id,artifact_ref=evidence.artifact_ref,**json.loads(row[0]))

    @app.post("/api/learning/qualitative/{learner_id}/reviews")
    @locked
    def review(learner_id: str, body: QualitativeReviewIn, x_teacher_token: str = Header(default="")):
        if learner_id != settings.learner_id:
            raise HTTPException(403, "Local single-user workspace only")
        if not settings.local_teacher_token or not hmac.compare_digest(x_teacher_token, settings.local_teacher_token):
            raise HTTPException(403, "Authorized local teacher token required")
        try:
            values = service.evidences(learner_id)
        except ConsentDenied as exc:
            raise HTTPException(403, str(exc)) from exc
        original = next((e for e in values if e.evidence_id == body.evidence_id), None)
        if original is None:
            raise HTTPException(404, "Unknown learner evidence")
        rubric = next((r for r in catalog.rubrics if r.ref.asset_id == body.rubric_id and r.ref.version == body.rubric_version), None)
        if rubric is None:
            raise HTTPException(404, "Unknown rubric asset version")
        asset=next((a for a in catalog.assessments if a.ref.asset_id==original.assessment_id and a.ref.version==original.assessment_version),None)
        if asset is None or asset.rubric_ref!=rubric.ref or asset.kind!="explanation" or rubric.assessor_policy not in {"teacher","reviewed_llm"}:
            raise HTTPException(400,"Rubric must match the configured qualitative assessment")
        digest=hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        key="qualitative-review:"+body.correction_id
        with store.lock:
            service._authorize(learner_id)
            prior=store.conn.execute("SELECT content_hash,payload FROM learning_api_receipts WHERE learner_id=? AND attempt_id=?",(learner_id,key)).fetchone()
            if prior:
                if prior[0]!=digest: raise HTTPException(409,"Review identity content conflict")
                return json.loads(prior[1])
        if any(e.supersedes == original.evidence_id for e in values):
            raise HTTPException(409, "Review the current evidence revision")
        try:
            scored = score_review(rubric, RubricReview(dimension_scores=body.dimension_scores,
                confidence=body.confidence, reason=body.reason, assessor_version=body.assessor_version), assessor_type="teacher")
            correction = original.model_copy(update=dict(evidence_id=f"qualitative:{learner_id}:{body.correction_id}",
                supersedes=original.evidence_id, verdict_ref=f"teacher-rubric:{body.correction_id}",
                verdict_status=scored["status"], score=scored["score"], qualitative_pass=scored["qualitative_pass"],
                confidence=body.confidence, verifier_version="teacher-rubric-v1", verifier_details=dict(
                    rubric=scored["rubric"], rubric_ref=scored["rubric_ref"], assessor_type="teacher",
                    assessment_provenance=asset.provenance,comparison_group=asset.comparison_group,
                    assessor_version=body.assessor_version, reason=body.reason, source="authorized-local-teacher"),
                event_seq=None, ingested_at=None))
            def result_for(transition):
                return dict(evidence_id=correction.evidence_id, transition=transition,
                            rubric=scored, source="teacher_review", candidate_only=False)
            result = service.consume(correction,receipt=(key,digest,result_for))
            return result_for(result)
        except (ValueError, EvidenceConflict) as exc:
            raise HTTPException(409, str(exc)) from exc
