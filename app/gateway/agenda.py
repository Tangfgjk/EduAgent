from uuid import UUID
from fastapi import APIRouter, Depends
from app.modules.learning.agenda import NextAction, next_action


def agenda_router(student):
    router = APIRouter(prefix='/api/v1')
    @router.get('/student/workspaces/{wid}/next-action', response_model=NextAction)
    def get_next(wid: UUID, ctx=Depends(student, scope='function')):
        return next_action(ctx.session, ctx.actor, str(wid))
    return router
