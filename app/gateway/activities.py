import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response

from app.modules.learning.activity_content import UNIT_ID, TITLE, CONTENT_VERSION, admin_tasks
from app.modules.learning.activity_contracts import ActivityCatalogOut, ActivityContentOut, ActivityInput, ActivityRunOut, ActivityRunsOut, StartActivity
from app.modules.learning.activity_catalog import ACTIVITY_PACKS, get_activity
from app.modules.learning.activity_service import Activities, public_activity
from app.modules.learning.content_service import Content
from app.modules.learning.contracts import EventOut, RunRevision
from app.modules.learning.tutor_contracts import ContentOut, ContentToggle


def activity_router(student, admin):
    router = APIRouter(prefix="/api/v1")
    student_auth, admin_auth = Depends(student, scope="function"), Depends(admin, scope="function")
    content = Content(UNIT_ID, TITLE, CONTENT_VERSION, admin_tasks())

    def catalog_item(session, item, admin=False):
        return {**Content(item.unit_id, item.title, item.version, item.admin_tasks()).view(session, admin), "parent_unit_id": item.parent_unit_id, 'flow': item.flow}

    @router.get("/student/activity-catalog", response_model=ActivityCatalogOut)
    def student_catalog(ctx=student_auth):
        return {"items": [catalog_item(ctx.session, item) for item in ACTIVITY_PACKS]}

    @router.get("/admin/activity-catalog", response_model=ActivityCatalogOut)
    def admin_catalog(ctx=admin_auth):
        return {"items": [catalog_item(ctx.session, item, True) for item in ACTIVITY_PACKS]}

    @router.post("/admin/activity-catalog/{unit_id}/development-release", response_model=ActivityContentOut)
    def release(unit_id: str, body: ContentToggle, ctx=admin_auth):
        item = get_activity(unit_id)
        Content(item.unit_id, item.title, item.version, item.admin_tasks()).toggle(ctx.session, ctx.actor, body.model_dump())
        return catalog_item(ctx.session, item, True)

    @router.get("/student/activity-content", response_model=ContentOut)
    def available(ctx=student_auth):
        return content.view(ctx.session)

    @router.get("/admin/activity-content", response_model=ContentOut)
    def inspect_content(ctx=admin_auth):
        return content.view(ctx.session, admin=True)

    @router.post("/admin/activity-content/development-release", response_model=ContentOut)
    def toggle(body: ContentToggle, ctx=admin_auth):
        return content.toggle(ctx.session, ctx.actor, body.model_dump())

    # 工厂隔离闭包；不能让循环最后一个 kind 影响另一条路由。
    def mount(kind):
        service = Activities(kind)
        prefix = "/student/workspaces/{wid}/" + kind

        @router.get(prefix, response_model=ActivityRunsOut, name=kind + "_list")
        def runs(wid: UUID, ctx=student_auth, offset: int = Query(0, ge=0)):
            return service.list(ctx.session, ctx.actor, str(wid), offset)

        @router.post(prefix, response_model=ActivityRunOut, status_code=202, name=kind + "_start")
        def start(wid: UUID, body: StartActivity, ctx=student_auth,
                  key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128)):
            return service.start(ctx.session, ctx.actor, str(wid), body.model_dump(), key)

        @router.get(prefix + "/{rid}", response_model=ActivityRunOut, name=kind + "_get")
        def run(wid: UUID, rid: UUID, ctx=student_auth):
            return public_activity(ctx.session, service.owned(ctx.session, ctx.actor, str(wid), str(rid)))

        @router.post(prefix + "/{rid}/inputs", response_model=ActivityRunOut, status_code=202, name=kind + "_input")
        def submit(wid: UUID, rid: UUID, body: ActivityInput, ctx=student_auth,
                   key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128)):
            return service.submit(ctx.session, ctx.actor, str(wid), str(rid), body.model_dump(mode="json"), key)

        @router.post(prefix + "/{rid}/cancel", response_model=ActivityRunOut, name=kind + "_cancel")
        def cancel(wid: UUID, rid: UUID, body: RunRevision, ctx=student_auth):
            return service.cancel(ctx.session, ctx.actor, str(wid), str(rid), body.expected_revision)

        @router.post(prefix + '/{rid}/model-operations/{oid}/cancel', response_model=ActivityRunOut, name=kind + '_model_cancel')
        def cancel_model(wid: UUID, rid: UUID, oid: UUID, ctx=student_auth):
            from app.infrastructure.runtime import model_tasks
            run = service.owned(ctx.session, ctx.actor, str(wid), str(rid))
            model_tasks.cancel(ctx.session, run, str(oid))
            ctx.session.flush()
            return public_activity(ctx.session, run)

        @router.get(prefix + "/{rid}/event-history", response_model=list[EventOut], name=kind + "_history")
        def history(wid: UUID, rid: UUID, ctx=student_auth, after: int = Query(0, ge=0)):
            return service.events(ctx.session, ctx.actor, str(wid), str(rid), after)

        @router.get(prefix + "/{rid}/events", response_class=Response, name=kind + "_events",
                    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}})
        def events(wid: UUID, rid: UUID, ctx=student_auth,
                   after: Annotated[int, Header(alias="Last-Event-ID", ge=0)] = 0):
            batch = service.events(ctx.session, ctx.actor, str(wid), str(rid), after)
            text = "".join(f"id: {row['seq']}\nevent: {row['kind']}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n" for row in batch)
            return Response(text or ": keep-alive\n\n", media_type="text/event-stream")

    mount("roundtable")
    mount("teach")
    return router
