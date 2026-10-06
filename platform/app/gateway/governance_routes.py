"""Local governance API; roles come from server configuration, not JSON claims."""
from __future__ import annotations

import hmac
from pathlib import Path
from typing import Callable, Literal

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.actions import ActionEnvelope
from app.core.rules import ActionGovernor, GovernorContext
from app.core.schema import MentalStateSnapshot
from app.governance.authority import AuthorizationDenied, Principal
from app.governance.provenance import GroundingVerifier, SourceReference
from app.governance.service import GovernanceService
from app.learning.assets import load_catalog
from app.learning.retrieval import LocalRetrieval
from app.learning.service import ConsentDenied, LearningService


class PrivacyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["withdraw", "quarantine", "request_deletion", "restore"]
    reason_code: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_.:-]+$")


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["reject", "request_revision", "approve_candidate"]
    rationale_code: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_.:-]+$")


class SourceReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approve", "reject"]
    rationale_code: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_.:-]+$")


class ProvenanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: ActionEnvelope
    references: list[SourceReference] = Field(max_length=20)


def install_governance_routes(app, store, settings, *,
                              principal_resolver: Callable[[Request], Principal] | None = None):
    service = GovernanceService(store)
    root = Path(__file__).resolve().parents[2] / "seeds" / "knowledge"
    catalog = load_catalog(settings)
    known_kcs = {item.ref.asset_id for item in catalog.knowledge}
    app.state.governance = service

    def principal(request):
        if principal_resolver is not None:
            return principal_resolver(request)
        header = request.headers.get("authorization", "")
        if header:
            expected = settings.local_teacher_token
            if not expected or not hmac.compare_digest(header, "Bearer " + expected):
                raise AuthorizationDenied("Invalid server-configured local teacher token")
            return Principal("local-teacher", "teacher", frozenset({settings.learner_id}))
        return Principal("local-learner", "learner", frozenset({settings.learner_id}))

    def authorize(request, learner_id, operation="read"):
        try:
            actor = principal(request)
            actor.require(learner_id, operation)
            return actor
        except AuthorizationDenied as exc:
            raise HTTPException(403, str(exc)) from exc

    def invoke(call):
        try:
            return call()
        except AuthorizationDenied as exc:
            raise HTTPException(403, str(exc)) from exc
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.middleware("http")
    async def privacy_boundary(request: Request, call_next):
        path = request.url.path
        exceptions = path.startswith("/api/governance/") or path.startswith("/api/auth/")
        if path.startswith("/api/") and not exceptions and service.blocked(settings.learner_id):
            return JSONResponse({"detail": "Workspace withdrawn, quarantined or awaiting deletion"}, status_code=403)
        return await call_next(request)

    @app.get("/api/governance/privacy/{learner_id}")
    def privacy_status(learner_id: str, request: Request):
        actor = authorize(request, learner_id)
        return dict(privacy=service.privacy(actor, learner_id), identity_mode="local-assigned-role-port",
                    production_identity_verified=False, deletion_execution_publicly_enabled=False)

    @app.post("/api/governance/privacy/{learner_id}")
    def privacy_change(learner_id: str, body: PrivacyIn, request: Request):
        actor = authorize(request, learner_id, body.operation)
        return invoke(lambda: service.change_privacy(actor, learner_id, body.operation, reason_code=body.reason_code))

    @app.get("/api/governance/privacy/{learner_id}/deletion-plan")
    def deletion_plan(learner_id: str, request: Request):
        return service.deletion_plan(authorize(request, learner_id), learner_id)

    @app.get("/api/governance/reviews/{learner_id}")
    def reviews(learner_id: str, request: Request):
        return dict(reviews=service.reviews(authorize(request, learner_id, "review"), learner_id))

    @app.post("/api/governance/reviews/{learner_id}/{review_id}")
    def review(learner_id: str, review_id: str, body: ReviewIn, request: Request):
        actor = authorize(request, learner_id, "review")
        return invoke(lambda: service.resolve_review(actor, learner_id, review_id,
                      decision=body.decision, rationale_code=body.rationale_code))

    def retrieval():
        return LocalRetrieval(root, index_connection=store.conn, lock=store.lock)

    @app.post("/api/governance/sources/{learner_id}/{source_id}/review")
    def source_review(learner_id: str, source_id: str, body: SourceReviewIn, request: Request):
        actor = authorize(request, learner_id, "review")
        index = retrieval()
        source = next((item for item in index.sources() if item.source_id == source_id), None)
        if source is None or source.source_version != body.source_version:
            raise HTTPException(409, "Indexed source revision unavailable")
        if not index.validate_source_revision(source_id, body.source_version):
            raise HTTPException(409, "Indexed source unavailable or changed")
        return invoke(lambda: service.review_source(actor, learner_id, source_id, body.source_version,
                      kc_refs=source.kc_refs, decision=body.decision, rationale_code=body.rationale_code))

    @app.post("/api/governance/provenance/{learner_id}/check")
    def provenance_check(learner_id: str, body: ProvenanceIn, request: Request):
        authorize(request, learner_id)
        if service.blocked(learner_id):
            raise HTTPException(403, "Learner workspace is blocked")
        try:
            LearningService(store)._authorize(learner_id)
        except ConsentDenied as exc:
            raise HTTPException(403, str(exc)) from exc
        index = retrieval()
        verifier = GroundingVerifier(index, known_kcs=known_kcs,
            reviewed_source_versions=service.reviewed_sources(learner_id),
            reviewed_source_kcs=service.reviewed_source_kcs(learner_id))
        checked = verifier.check_references(body.action, body.references)
        if not checked.allowed:
            return dict(allowed=False, reason=checked.reason, candidate_only=True, executed=False)
        # Only this candidate instance receives a server-held receipt; never return it.
        bound = verifier.bind_action(body.action, body.references)
        context = GovernorContext(snapshot=MentalStateSnapshot(learner_id=learner_id), ladder_pos=0,
            hints_used=0, hint_budget=3, frustration_streak=0, grounding_validator=verifier.validate_action,
            safety_review_sink=lambda action, reason: service.enqueue_safety_review(learner_id, action, "R-05"))
        outcome = ActionGovernor().decide(bound, context)
        return dict(allowed=outcome.decision == "allow", reason=outcome.reason, rule_id=outcome.rule_id,
                    candidate_only=True, executed=False, semantic_truth_verified=False)
