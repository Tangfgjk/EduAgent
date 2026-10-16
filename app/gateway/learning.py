"""学生计划流程路由；SSE 使用有界批次，断开后用 Last-Event-ID 续传。"""
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response

from app.modules.learning.contracts import EventOut, PlanDecision, RunOut, RunRevision, RunsOut, StartPlan
from app.modules.learning.service import Learning, public_run


def learning_router(student):
    router, service = APIRouter(prefix="/api/v1/student/workspaces/{wid}"), Learning()
    auth = Depends(student, scope="function")
    key_header = Header(alias="Idempotency-Key", min_length=1, max_length=128)

    @router.post("/runs", response_model=RunOut, status_code=202)
    def start(wid: UUID, body: StartPlan, ctx=auth, key: str = key_header):
        return service.start(ctx.session, ctx.actor, str(wid), body.model_dump(mode="json"), key)

    @router.get("/runs", response_model=RunsOut)
    def runs(wid: UUID, ctx=auth, offset: int = Query(0, ge=0)):
        return service.list(ctx.session, ctx.actor, str(wid), offset)

    @router.get("/runs/{rid}", response_model=RunOut)
    def run(wid: UUID, rid: UUID, ctx=auth):
        return public_run(service.owned(ctx.session, ctx.actor, str(wid), str(rid)))

    @router.post("/runs/{rid}/resume", response_model=RunOut, status_code=202)
    def resume(wid: UUID, rid: UUID, body: PlanDecision, ctx=auth, key: str = key_header):
        return service.resume(ctx.session, ctx.actor, str(wid), str(rid), body.model_dump(mode="json"), key)

    @router.post("/runs/{rid}/cancel", response_model=RunOut)
    def cancel(wid: UUID, rid: UUID, body: RunRevision, ctx=auth):
        return service.cancel(ctx.session, ctx.actor, str(wid), str(rid), body.expected_revision)

    @router.get("/runs/{rid}/events", response_class=Response,
        responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}})
    def events(wid: UUID, rid: UUID, ctx=auth,
               after: Annotated[int, Header(alias="Last-Event-ID", ge=0)] = 0):
        batch = service.events(ctx.session, ctx.actor, str(wid), str(rid), after)
        content = "".join(f"id: {row['seq']}\nevent: {row['kind']}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n" for row in batch)
        return Response(content or ": keep-alive\n\n", media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @router.get("/runs/{rid}/event-history", response_model=list[EventOut])
    def history(wid: UUID, rid: UUID, ctx=auth, after: int = Query(0, ge=0)):
        return service.events(ctx.session, ctx.actor, str(wid), str(rid), after)

    return router
