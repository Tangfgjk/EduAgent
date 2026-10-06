"""Separate restricted school API; no legacy high-privilege endpoints are mounted."""
from __future__ import annotations

import hashlib
import threading
from collections import Counter
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.gateway.learning_routes import AssessmentIn, ConsentIn, DeliveryIn, HintIn, install_learning_routes
from app.learning.service import ConsentDenied, LearningService
from app.learning.workspace import WorkspaceService
from app.gateway.dashboard_routes import ShareIn
from app.school.identity import SchoolDenied, SchoolDirectory
from app.storage.db import Store


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=6, max_length=256)


class LearnerInstances:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.instances = {}

    def get(self, tenant_id, learner_id):
        key = (tenant_id, learner_id)
        with self.lock:
            if key not in self.instances:
                filename = hashlib.sha256((tenant_id + "\0" + learner_id).encode()).hexdigest() + ".sqlite3"
                path = self.root / filename
                store = Store(str(path))
                internal = FastAPI()
                settings = Settings(db_path=str(path), learner_id=learner_id, assessment_require_ticket=True)
                install_learning_routes(internal, store, settings)
                self.instances[key] = (store, internal)
            return self.instances[key]

    def close(self):
        with self.lock:
            for store, _ in self.instances.values():
                store.close()
            self.instances.clear()


def create_school_app(directory: SchoolDirectory, learner_root: Path | str) -> FastAPI:
    instances = LearnerInstances(Path(learner_root))
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            instances.close()
    app = FastAPI(title="EduAgent isolated school research API", lifespan=lifespan)
    app.state.school_directory, app.state.learner_instances = directory, instances

    def principal(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "school bearer required")
        try:
            return directory.authenticate(authorization[7:])
        except SchoolDenied:
            raise HTTPException(401, "invalid school session") from None

    def require(actor, learner_id, operation):
        try:
            directory.require(actor, learner_id, operation)
        except SchoolDenied:
            raise HTTPException(403, "learner assignment or server role required") from None

    def learning(actor, learner_id):
        require(actor, learner_id, "read")
        store, _ = instances.get(actor.tenant_id, learner_id)
        service = LearningService(store)
        try:
            service._authorize(learner_id)
        except ConsentDenied:
            raise HTTPException(403, "current teaching authorization required") from None
        return service

    @contextmanager
    def reading(actor, learner_id):
        require(actor, learner_id, "read")
        store, _ = instances.get(actor.tenant_id, learner_id)
        from app.learning.assets import load_catalog
        workspace = WorkspaceService(store, load_catalog(Settings()))
        with store.lock:
            store.conn.execute("BEGIN IMMEDIATE")
            try:
                service = LearningService(store)
                service._authorize(learner_id)
                if actor.role != "student":
                    workspace.require_sharing(learner_id, actor.role)
                yield service
                store.conn.commit()
            except (PermissionError, KeyError):
                store.conn.rollback()
                raise HTTPException(403, "current teaching and explicit audience sharing required") from None
            except Exception:
                store.conn.rollback()
                raise

    @app.post("/api/school/login")
    def login(body: LoginIn):
        try:
            token = directory.login(body.tenant_id, body.username, body.password)
            actor = directory.authenticate(token)
        except SchoolDenied:
            raise HTTPException(401, "credentials invalid or rate limited") from None
        return dict(access_token=token, token_type="bearer", expires_in=28800, role=actor.role)

    @app.post("/api/school/logout")
    def logout(actor=Depends(principal), authorization: str = Header()):
        directory.revoke(authorization[7:])
        return {"revoked": True}

    @app.get("/api/school/learners")
    def learners(actor=Depends(principal)):
        return dict(learners=directory.visible_learners(actor), role=actor.role, tenant_id=actor.tenant_id)

    @app.get("/api/school/learners/{learner_id}/state")
    def state(learner_id: str, actor=Depends(principal)):
        with reading(actor, learner_id) as service:
            if actor.role == "parent":
                return dict(learner_id=learner_id, summary_only=True,
                    kc_summaries=[dict(kc_id=row.kc_id, p_mastery=row.p_mastery, confidence=row.confidence)
                                  for row in service.masteries(learner_id)])
            return dict(learner_id=learner_id, mastery=[row.model_dump(mode="json") for row in service.masteries(learner_id)],
                        retention=[row.model_dump(mode="json") for row in service.retentions(learner_id)])

    @app.get("/api/school/learners/{learner_id}/evidence")
    def evidence(learner_id: str, actor=Depends(principal)):
        with reading(actor, learner_id) as service:
            rows = service.evidences(learner_id)
            if actor.role == "parent":
                return dict(learner_id=learner_id, summary_only=True, evidence_count=len(rows),
                    verdict_counts=dict(Counter(row.verdict_status for row in rows)),
                    assisted_attempt_count=sum(bool(row.hint_level or row.assistance_mode != "none" or row.answer_exposed) for row in rows))
            return dict(learner_id=learner_id, evidence=[row.model_dump(mode="json") for row in rows])

    @app.post("/api/school/learners/{learner_id}/sharing")
    def sharing(learner_id: str, body: ShareIn, actor=Depends(principal)):
        require(actor, learner_id, "learn")
        service = learning(actor, learner_id)
        from app.learning.assets import load_catalog
        try:
            return WorkspaceService(service.store, load_catalog(Settings())).share(learner_id, body.audiences, body.expected_revision)
        except ValueError:
            raise HTTPException(409, "sharing revision conflict") from None

    @app.get("/api/school/learners/{learner_id}/assessments")
    def assessments(learner_id: str, actor=Depends(principal)):
        require(actor, learner_id, "learn")
        learning(actor, learner_id)
        from app.learning.assets import load_catalog
        catalog = load_catalog(Settings())
        return {"assessments": [row.model_dump(mode="json", exclude={"answer", "stem"}) for row in catalog.assessments]}

    async def forward(actor, learner_id, operation, endpoint, body):
        require(actor, learner_id, operation)
        if endpoint == "consent":
            if any(scope != "teaching" for scope in body.scopes):
                raise HTTPException(403, "school app does not grant research or evolution consent")
            # Collection authorization_source is server-derived, not client identity.
            body = body.model_copy(update={"source": "school-account:" + actor.account_id})
        else:
            learning(actor, learner_id)
        _, internal = instances.get(actor.tenant_id, learner_id)
        transport = httpx.ASGITransport(app=internal)
        async with httpx.AsyncClient(transport=transport, base_url="http://isolated-school-internal") as client:
            response = await client.post(f"/api/learning/{endpoint}/{learner_id}" if endpoint == "consent"
                else f"/api/learning/assessment/{learner_id}/{endpoint}", json=body.model_dump(mode="json"))
        return JSONResponse(response.json(), status_code=response.status_code,
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/school/learners/{learner_id}/consent")
    async def consent(learner_id: str, body: ConsentIn, actor=Depends(principal)):
        return await forward(actor, learner_id, "consent", "consent", body)

    @app.post("/api/school/learners/{learner_id}/issue")
    async def issue(learner_id: str, body: DeliveryIn, actor=Depends(principal)):
        return await forward(actor, learner_id, "learn", "issue", body)

    @app.post("/api/school/learners/{learner_id}/hint")
    async def hint(learner_id: str, body: HintIn, actor=Depends(principal)):
        return await forward(actor, learner_id, "learn", "hint", body)

    @app.post("/api/school/learners/{learner_id}/submit")
    async def submit(learner_id: str, body: AssessmentIn, actor=Depends(principal)):
        return await forward(actor, learner_id, "learn", "submit", body)

    @app.middleware("http")
    async def safety_boundary(request, call_next):
        from urllib.parse import urlsplit
        host = request.headers.get("host", "")
        try:
            hostname = urlsplit("http://" + host).hostname
        except ValueError:
            hostname = None
        if hostname not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": "loopback host required"}, status_code=403,
                                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        origin = request.headers.get("origin")
        if (origin and origin != f"{request.url.scheme}://{host}") or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "same-origin request required"}, status_code=403,
                                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        if not request.url.path.startswith("/api/school/"):
            return JSONResponse({"detail": "legacy and high-privilege endpoints disabled"}, status_code=403,
                                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    return app
