from uuid import UUID
from fastapi import APIRouter, Depends, Header
from pydantic import Field
from sqlalchemy import select
from app.infrastructure.db.models import EvaluationStudy
from app.modules.evidence import effect_studies as study
from app.modules.learning.contracts import Contract
from app.modules.errors import DomainError


class Freeze(Contract):
    expected_revision:int=Field(ge=1,strict=True)


def study_router(admin):
    router=APIRouter(prefix='/api/v1/admin/effect-studies')
    def owned(ctx,sid):
        row=ctx.session.get(EvaluationStudy,sid,with_for_update=True)
        if not row:raise DomainError(404,'not_found','未找到评价协议')
        return row
    @router.get('')
    def listing(ctx=Depends(admin,scope='function')):
        return {'items':[study.view(r) for r in ctx.session.scalars(select(EvaluationStudy).order_by(EvaluationStudy.created_at.desc()).limit(100))]}
    @router.post('',status_code=201)
    def create(body:study.StudyIn,ctx=Depends(admin,scope='function')):return study.create(ctx.session,ctx.actor,body.model_dump())
    @router.post('/{sid}/freeze')
    def freeze(sid:UUID,body:Freeze,ctx=Depends(admin,scope='function')):return study.freeze(ctx.session,ctx.actor,owned(ctx,str(sid)),body.expected_revision)
    @router.get('/{sid}/report')
    def report(sid:UUID,ctx=Depends(admin,scope='function')):return study.report(ctx.session,ctx.actor,owned(ctx,str(sid)))
    @router.post('/{sid}/observations',status_code=201)
    def observe(sid:UUID,body:study.ObservationIn,ctx=Depends(admin,scope='function'),key:str=Header(alias='Idempotency-Key',min_length=1,max_length=128)):
        return study.record(ctx.session,ctx.actor,owned(ctx,str(sid)),body,key)
    return router
