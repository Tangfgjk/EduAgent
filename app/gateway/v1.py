"""正式入口仅提供经过鉴权的 S02 功能；不挂载无认证的旧演示路由。"""
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.gateway.frontend import mount_frontends
from app.gateway.learning import learning_router
from app.gateway.tutoring import tutoring_router
from app.gateway.memory import memory_router
from app.gateway.activities import activity_router
from app.gateway.evidence import evidence_router
from app.gateway.planning import planning_router
from app.gateway.skill_evolution import evolution_router
from app.gateway.model_runtime import model_runtime_router
from app.gateway.development import development_router
from app.gateway.courses import course_router
from app.gateway.generation import generation_router
from app.modules.resources.proxy import proxy_router
from app.infrastructure.db.database import Database
from app.infrastructure.db.models import Account
from app.modules.errors import DomainError
from app.modules.identity.security import STUDENT_PASSWORD_MIN_LENGTH
from app.modules.identity.service import Actor, Identity, public_account
from app.modules.workspaces.service import Workspaces, public_workspace

SESSION_COOKIE = "wenjin_session"
CSRF_COOKIE = "wenjin_csrf"


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(Body):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128, repr=False)


class StudentIn(Body):
    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=STUDENT_PASSWORD_MIN_LENGTH, max_length=128, repr=False)

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value):
        if not value.strip():
            raise ValueError("姓名不能为空")
        return value.strip()


class PasswordIn(Body):
    password: str = Field(min_length=STUDENT_PASSWORD_MIN_LENGTH, max_length=128, repr=False)


class WorkspaceIn(Body):
    kind: Literal["project", "temporary"]
    title: str = Field(min_length=1, max_length=120)


class RevisionIn(Body):
    expected_revision: int = Field(ge=1, strict=True)


class RenameIn(RevisionIn):
    title: str = Field(min_length=1, max_length=120)


class AccountOut(Body):
    id: UUID
    username: str
    display_name: str
    role: Literal["admin", "student"]
    learner_id: UUID | None
    enabled: bool


class AuthOut(Body):
    account: AccountOut
    csrf_token: str


class MeOut(Body):
    account: AccountOut


class StudentsOut(Body):
    items: list[AccountOut]


class WorkspaceOut(Body):
    workspace_id: UUID
    kind: Literal["project", "temporary"]
    project_id: UUID | None
    title: str
    status: Literal["active", "archived"]
    revision: int


class WorkspacesOut(Body):
    items: list[WorkspaceOut]


class ErrorDetail(Body):
    code: str
    message: str
    request_id: str
    retryable: bool


class ErrorOut(Body):
    error: ErrorDetail


@dataclass
class Context:
    session: Session
    actor: Actor


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or Settings.load()
    if settings.data_mode not in {"synthetic", "adult_test", "real_student"}:
        raise ValueError("无效 RSI_DATA_MODE")
    if database is None and not settings.database_url:
        raise ValueError("请配置 RSI_DATABASE_URL；本地开发见 scripts/dev_postgres.py")
    db = database or Database(settings.database_url)
    identity, spaces = Identity(db, data_mode=settings.data_mode), Workspaces()
    allowed_origins = {item.strip() for item in settings.allowed_origins.split(",") if item.strip()}

    @asynccontextmanager
    async def lifespan(app):
        db.assert_ready()
        try:
            yield
        finally:
            if database is None:
                db.close()

    app = FastAPI(title="桂子问津", version="0.19.0", lifespan=lifespan,
                  responses={status: {"model": ErrorOut} for status in (401, 403, 409, 422, 429, 503)})

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def error_response(request, status, code, message):
        return JSONResponse(status_code=status, content={"error": {
            "code": code, "message": message, "request_id": request.state.request_id,
            "retryable": status in {429, 503}}})

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return error_response(request, error.status, error.code, error.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # FastAPI 默认错误含 input，密码和学生原文不得回显或进入错误日志。
        return error_response(request, 422, "invalid_input", "请求字段不符合要求")

    @app.exception_handler(IntegrityError)
    async def conflict_error(request, error):
        return error_response(request, 409, "conflict", "记录已变更或重复，请刷新后重试")

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, error):
        return error_response(request, 503, "database_unavailable", "数据服务暂不可用，请稍后重试")

    def check_origin(request: Request):
        origin = request.headers.get("origin")
        if origin and origin not in allowed_origins:
            raise DomainError(403, "origin_forbidden", "请求来源不被允许")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise DomainError(403, "origin_forbidden", "请求来源不被允许")

    def client_role(request: Request):
        value = request.headers.get("x-wenjin-client")
        if value not in {None, "student", "admin"}:
            raise DomainError(400, "invalid_client", "未知登录端")
        return value

    def cookie_names(role):
        # 无客户端标识的维护/API 调用保留旧协议；浏览器双端各自持有会话。
        return (f"wenjin_{role}_session", f"wenjin_{role}_csrf") if role else (SESSION_COOKIE, CSRF_COOKIE)

    def context_for(role=None):
        def dependency(request: Request):
            write = request.method not in {"GET", "HEAD", "OPTIONS"}
            if write:
                check_origin(request)
            ui_role = client_role(request)
            session_cookie, _ = cookie_names(ui_role)
            with db.transaction() as session:
                actor = identity.authorize(session, request.cookies.get(session_cookie, ""),
                                           request.headers.get("x-csrf-token"), role or ui_role, write)
                yield Context(session, actor)
        return dependency

    anyone, student, admin = context_for(), context_for("student"), context_for("admin")

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        db.assert_ready()
        return {"status": "ready"}

    @app.post("/api/v1/auth/login", response_model=AuthOut)
    def login(body: LoginIn, request: Request, response: Response):
        check_origin(request)
        ui_role = client_role(request)
        session_cookie, csrf_cookie = cookie_names(ui_role)
        account, token, csrf = identity.login(body.username, body.password,
                                              request.client.host if request.client else "unknown", expected_role=ui_role)
        response.set_cookie(session_cookie, token, httponly=True, secure=settings.cookie_secure,
                            samesite="strict", path="/api/v1", max_age=identity.ttl)
        response.set_cookie(csrf_cookie, csrf, httponly=False, secure=settings.cookie_secure,
                            samesite="strict", path="/", max_age=identity.ttl)
        if ui_role:
            # 成功升级到分端会话后清理本浏览器遗留凭据，避免旧 Cookie 隐式保留登录。
            response.delete_cookie(SESSION_COOKIE, path="/api/v1")
            response.delete_cookie(CSRF_COOKIE, path="/")
        return {"account": account, "csrf_token": csrf}

    @app.get("/api/v1/auth/me", response_model=MeOut)
    def me(ctx: Annotated[Context, Depends(anyone, scope="function")]):
        return {"account": public_account(ctx.actor.account)}

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(request: Request, response: Response, ctx: Annotated[Context, Depends(anyone, scope="function")]):
        identity.logout(ctx.session, ctx.actor)
        session_cookie, csrf_cookie = cookie_names(client_role(request))
        response.delete_cookie(session_cookie, path="/api/v1")
        response.delete_cookie(csrf_cookie, path="/")

    @app.get("/api/v1/admin/students", response_model=StudentsOut)
    def students(ctx: Annotated[Context, Depends(admin, scope="function")], offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        rows = ctx.session.scalars(select(Account).where(Account.role == "student").order_by(Account.created_at, Account.id).offset(offset).limit(limit))
        return {"items": [public_account(row) for row in rows]}

    @app.post("/api/v1/admin/students", status_code=201, response_model=AccountOut)
    def create_student(body: StudentIn, ctx: Annotated[Context, Depends(admin, scope="function")]):
        return identity.create_student(ctx.session, ctx.actor, body.username, body.password, body.display_name)

    @app.post("/api/v1/admin/students/{account_id}/reset-password", response_model=AccountOut)
    def reset_password(account_id: UUID, body: PasswordIn, ctx: Annotated[Context, Depends(admin, scope="function")]):
        return identity.update_student(ctx.session, ctx.actor, str(account_id), body.password)

    @app.post("/api/v1/admin/students/{account_id}/disable", response_model=AccountOut)
    def disable(account_id: UUID, ctx: Annotated[Context, Depends(admin, scope="function")]):
        return identity.update_student(ctx.session, ctx.actor, str(account_id))

    @app.post("/api/v1/student/workspaces", status_code=201, response_model=WorkspaceOut)
    def create_workspace(body: WorkspaceIn, ctx: Annotated[Context, Depends(student, scope="function")],
                         key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]):
        return spaces.create(ctx.session, ctx.actor, body.kind, body.title, key)

    @app.get("/api/v1/student/workspaces", response_model=WorkspacesOut)
    def workspaces(ctx: Annotated[Context, Depends(student, scope="function")], status: Literal["active", "archived"] | None = None,
                   offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        return {"items": spaces.list(ctx.session, ctx.actor, status, offset, limit)}

    @app.get("/api/v1/student/workspaces/{wid}", response_model=WorkspaceOut)
    def workspace(wid: UUID, ctx: Annotated[Context, Depends(student, scope="function")]):
        return public_workspace(spaces.owned(ctx.session, ctx.actor, str(wid)))

    @app.patch("/api/v1/student/workspaces/{wid}", response_model=WorkspaceOut)
    def rename(wid: UUID, body: RenameIn, ctx: Annotated[Context, Depends(student, scope="function")]):
        return spaces.change(ctx.session, ctx.actor, str(wid), body.expected_revision, title=body.title)

    @app.post("/api/v1/student/workspaces/{wid}/archive", response_model=WorkspaceOut)
    def archive(wid: UUID, body: RevisionIn, ctx: Annotated[Context, Depends(student, scope="function")]):
        return spaces.change(ctx.session, ctx.actor, str(wid), body.expected_revision, status="archived")

    @app.post("/api/v1/student/workspaces/{wid}/restore", response_model=WorkspaceOut)
    def restore(wid: UUID, body: RevisionIn, ctx: Annotated[Context, Depends(student, scope="function")]):
        return spaces.change(ctx.session, ctx.actor, str(wid), body.expected_revision, status="active")

    app.include_router(learning_router(student))
    app.include_router(tutoring_router(student, admin))
    app.include_router(memory_router(student))
    app.include_router(activity_router(student, admin))
    app.include_router(evidence_router(student))
    app.include_router(planning_router(student, admin))
    app.include_router(evolution_router(student,admin))
    app.include_router(model_runtime_router(admin))
    app.include_router(development_router(admin))
    app.include_router(course_router(student, admin))
    app.include_router(generation_router(student, admin))
    app.include_router(proxy_router(db))
    mount_frontends(app, Path(settings.frontend_dir) if settings.frontend_dir else Path(__file__).resolve().parents[2] / "frontend" / "dist")
    return app
