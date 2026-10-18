from fastapi import APIRouter, Depends, Query
from uuid import UUID
from app.modules.learning import courses
from app.modules.learning.content_contracts import GeneratedContentSpec, RegisteredContentOut, RegisteredContentsOut
from app.modules.learning.content_repository import ContentRepository
from app.infrastructure.db.models import GeneratedContentVersion
from sqlalchemy import select


def course_router(student, admin):
    router = APIRouter(prefix='/api/v1')
    student_auth, admin_auth = Depends(student, scope='function'), Depends(admin, scope='function')

    @router.get('/student/workspaces/{wid}/course', response_model=courses.CourseContext)
    def course(wid: UUID, ctx=student_auth):
        return courses.context(ctx.session, ctx.actor, str(wid))

    @router.post('/student/workspaces/{wid}/course', response_model=courses.CourseContext)
    def bind(wid: UUID, body: courses.BindCourse, ctx=student_auth):
        return courses.bind(ctx.session, ctx.actor, str(wid), body)

    @router.post('/student/workspaces/{wid}/course/active-unit', response_model=courses.CourseContext)
    def active(wid: UUID, body: courses.SelectUnit, ctx=student_auth):
        return courses.select_unit(ctx.session, ctx.actor, str(wid), body)

    @router.post('/admin/dynamic-content', status_code=201, response_model=RegisteredContentOut)
    def register(body: GeneratedContentSpec, ctx=admin_auth):
        return ContentRepository(ctx.session).register(ctx.actor, body)

    @router.get('/admin/dynamic-content', response_model=RegisteredContentsOut)
    def registered(ctx=admin_auth, offset: int = Query(0, ge=0)):
        repo = ContentRepository(ctx.session)
        rows = ctx.session.scalars(select(GeneratedContentVersion).order_by(GeneratedContentVersion.created_at.desc(), GeneratedContentVersion.unit_id).offset(offset).limit(20))
        return {'items': [repo.inspect(row.unit_id) for row in rows]}

    return router
