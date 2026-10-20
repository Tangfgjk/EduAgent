import json
import asyncio
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response,StreamingResponse

from app.modules.learning.contracts import EventOut, RunRevision
from app.modules.learning.content_service import Content, catalog, catalog_unit
from app.modules.learning.content_catalog import get_pack
from app.modules.learning.tutor_contracts import CatalogOut, CatalogUnit, ContentOut, ContentToggle, ReviewDecision, ReviewOut, ReviewsOut, StartTutor, TutorInput, TutorRunOut, TutorRunsOut
from app.modules.learning.tutor_service import Tutoring, public_tutor
from app.infrastructure.runtime.model_tasks import cancel as cancel_model
from app.modules.learning.tutor_history import history as tutor_history
from app.modules.learning.tutor_contracts import TutorHistoryOut


def tutoring_router(student, admin,db):
    router, tutoring, content = APIRouter(prefix="/api/v1"), Tutoring(), Content()
    student_auth, admin_auth = Depends(student, scope="function"), Depends(admin, scope="function")
    key_header = Header(alias="Idempotency-Key", min_length=1, max_length=128)
    prefix = "/student/workspaces/{wid}/tutoring"

    @router.get("/student/content-catalog", response_model=CatalogOut)
    def student_catalog(ctx=student_auth, workspace_id: UUID | None = None):
        if workspace_id:
            from app.modules.workspaces.service import Workspaces
            Workspaces().owned(ctx.session, ctx.actor, str(workspace_id))
        return catalog(ctx.session, workspace_id=str(workspace_id) if workspace_id else None)

    @router.get("/admin/content-catalog", response_model=CatalogOut)
    def admin_catalog(ctx=admin_auth):
        return catalog(ctx.session, admin=True)

    @router.post("/admin/content-catalog/{unit_id}/development-release", response_model=CatalogUnit)
    def release_unit(unit_id: str, body: ContentToggle, ctx=admin_auth):
        item = get_pack(unit_id, ctx.session)
        Content(item.unit_id, item.title, item.version, item.tasks).toggle(ctx.session, ctx.actor, body.model_dump())
        return catalog_unit(ctx.session, item, admin=True)

    @router.get("/student/content", response_model=ContentOut)
    def available(ctx=student_auth):
        return content.view(ctx.session)

    @router.get("/admin/content", response_model=ContentOut)
    def inspect_content(ctx=admin_auth):
        return content.view(ctx.session, admin=True)

    @router.post("/admin/content/development-release", response_model=ContentOut)
    def toggle(body: ContentToggle, ctx=admin_auth):
        return content.toggle(ctx.session, ctx.actor, body.model_dump())

    @router.get("/admin/answer-reviews", response_model=ReviewsOut)
    def reviews(ctx=admin_auth, offset: int = Query(0, ge=0), status: Literal["pending", "resolved"] = "pending"):
        return content.reviews(ctx.session, offset, status)

    @router.post("/admin/answer-reviews/{review_id}/resolve", response_model=ReviewOut)
    def resolve(review_id: UUID, body: ReviewDecision, ctx=admin_auth):
        return content.resolve(ctx.session, ctx.actor, str(review_id), body.model_dump())

    @router.post(prefix, response_model=TutorRunOut, status_code=202)
    def start(wid: UUID, body: StartTutor, ctx=student_auth, key: str = key_header):
        return tutoring.start(ctx.session, ctx.actor, str(wid), body.model_dump(), key)

    @router.get(prefix, response_model=TutorRunsOut)
    def runs(wid: UUID, ctx=student_auth, offset: int = Query(0, ge=0)):
        return tutoring.list(ctx.session, ctx.actor, str(wid), offset)

    @router.get(prefix + "/{rid}", response_model=TutorRunOut)
    def run(wid: UUID, rid: UUID, ctx=student_auth):
        return public_tutor(ctx.session, tutoring.owned(ctx.session, ctx.actor, str(wid), str(rid)))

    @router.post(prefix + "/{rid}/inputs", response_model=TutorRunOut, status_code=202)
    def submit(wid: UUID, rid: UUID, body: TutorInput, ctx=student_auth, key: str = key_header):
        return tutoring.submit(ctx.session, ctx.actor, str(wid), str(rid), body.model_dump(mode="json"), key)

    @router.get(prefix + '/{rid}/history', response_model=TutorHistoryOut)
    def learning_history(wid: UUID, rid: UUID, ctx=student_auth,
                         after: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=50)):
        run = tutoring.owned(ctx.session, ctx.actor, str(wid), str(rid))
        return tutor_history(ctx.session, ctx.actor.account, run, after, limit)

    @router.get(prefix+'/{rid}/live')
    async def live(wid:UUID,rid:UUID,request:Request,ctx=student_auth,follow:bool=True):
        tutoring.owned(ctx.session,ctx.actor,str(wid),str(rid))
        actor_id=ctx.actor.account.id
        cookie='wenjin_student_session' if request.headers.get('x-wenjin-client')=='student' else 'wenjin_session'
        token=request.cookies.get(cookie,'')
        from app.modules.identity.service import Identity
        from app.modules.learning.content_repository import content_hash
        from app.modules.errors import DomainError
        identity=Identity(db)
        def read():
            with db.transaction() as session:
                actor=identity.authorize(session,token,None,'student',False)
                if actor.account.id!=actor_id:raise DomainError(401,'session_changed','登录已变化')
                run=tutoring.owned(session,actor,str(wid),str(rid))
                return {'revision':run.revision,'status':run.status,'history':tutor_history(session,actor.account,run,0,30)}
        async def stream():
            previous=None
            for _ in range(40 if follow else 1):
                if await request.is_disconnected():return
                try:value=await asyncio.to_thread(read)
                except DomainError:
                    yield 'event: closed\ndata: {"reason":"session_or_scope_changed"}\n\n';return
                stamp=content_hash(value)
                if stamp!=previous:
                    yield 'id: '+stamp+'\nevent: snapshot\ndata: '+json.dumps(value,ensure_ascii=False)+'\n\n'
                    previous=stamp
                else:yield ': heartbeat\n\n'
                if follow:await asyncio.sleep(.5)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})

    @router.post(prefix + "/{rid}/cancel", response_model=TutorRunOut)
    def cancel(wid: UUID, rid: UUID, body: RunRevision, ctx=student_auth):
        return tutoring.cancel(ctx.session, ctx.actor, str(wid), str(rid), body.expected_revision)

    @router.post(prefix + "/{rid}/model-operations/{oid}/cancel", response_model=TutorRunOut)
    def cancel_coaching(wid: UUID, rid: UUID, oid: UUID, ctx=student_auth):
        run = tutoring.owned(ctx.session, ctx.actor, str(wid), str(rid))
        cancel_model(ctx.session, run, str(oid))
        ctx.session.flush()
        return public_tutor(ctx.session, run)

    @router.get(prefix + "/{rid}/events", response_class=Response,
        responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}})
    def events(wid: UUID, rid: UUID, ctx=student_auth,
               after: Annotated[int, Header(alias="Last-Event-ID", ge=0)] = 0):
        batch = tutoring.events(ctx.session, ctx.actor, str(wid), str(rid), after)
        text = "".join(f"id: {row['seq']}\nevent: {row['kind']}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n" for row in batch)
        return Response(text or ": keep-alive\n\n", media_type="text/event-stream")

    @router.get(prefix + "/{rid}/event-history", response_model=list[EventOut])
    def history(wid: UUID, rid: UUID, ctx=student_auth, after: int = Query(0, ge=0)):
        return tutoring.events(ctx.session, ctx.actor, str(wid), str(rid), after)

    return router
