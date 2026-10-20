import hashlib
from uuid import UUID
from fastapi import APIRouter,Depends
from fastapi.responses import FileResponse
from pydantic import Field
from sqlalchemy import select
from app.modules.learning.contracts import Contract
from app.modules.resources import media
from app.infrastructure.db.models import ResourceArtifact,MediaBudget,MediaAsset,MediaOperation,CourseBinding,CourseBuild
from app.modules.workspaces.service import Workspaces
from app.modules.errors import DomainError


class MediaApproval(Contract):
    expected_revision:int=Field(ge=0,strict=True)
    enabled:bool=Field(strict=True)
    acknowledge_five_yuan_and_verified_prices:bool=Field(default=False,strict=True)


class MediaSlot(Contract):
    kind:str=Field(pattern=r'^(image|audio|video)$')
    index:int=Field(ge=0,le=2,strict=True)


def media_router(student,admin):
    router=APIRouter(prefix='/api/v1');sa=Depends(student,scope='function');aa=Depends(admin,scope='function')
    def budget(ctx,bid,wid=None):
        row=ctx.session.get(MediaBudget,bid,with_for_update=True)
        if not row or wid and row.workspace_id!=wid:raise DomainError(404,'not_found','未找到媒体预算')
        if wid:Workspaces().owned(ctx.session,ctx.actor,wid)
        return row
    @router.get('/admin/media-budgets')
    def admin_list(ctx=aa):return {'items':[media.public(ctx.session,r) for r in ctx.session.scalars(select(MediaBudget).order_by(MediaBudget.created_at.desc()).limit(100))]}
    @router.post('/admin/resources/{rid}/media-budget',status_code=201)
    def propose(rid:UUID,ctx=aa):
        resource=ctx.session.get(ResourceArtifact,str(rid))
        if not resource:raise DomainError(404,'not_found','未找到课堂资源')
        return media.propose(ctx.session,ctx.actor,resource)
    @router.put('/admin/media-budgets/{bid}')
    def authorize(bid:UUID,body:MediaApproval,ctx=aa):
        return media.authorize(ctx.session,ctx.actor,budget(ctx,str(bid)),body.expected_revision,body.enabled,body.acknowledge_five_yuan_and_verified_prices)
    @router.get('/student/workspaces/{wid}/media-budgets')
    def owned_list(wid:UUID,ctx=sa):
        Workspaces().owned(ctx.session,ctx.actor,str(wid))
        return {'items':[media.public(ctx.session,r) for r in ctx.session.scalars(select(MediaBudget).where(MediaBudget.workspace_id==str(wid)).order_by(MediaBudget.created_at.desc()).limit(20))]}
    @router.post('/student/workspaces/{wid}/media-budgets/{bid}/jobs',status_code=202)
    def generate(wid:UUID,bid:UUID,body:MediaSlot,ctx=sa):
        row=budget(ctx,str(bid),str(wid));resource,owner=media.sources(ctx.session,row)
        binding=ctx.session.get(CourseBinding,str(wid));build=ctx.session.get(CourseBuild,resource.build_id)
        if not binding or binding.active_unit_id!=build.unit_id:raise DomainError(409,'course_scope_conflict','只能为当前确认的课程生成媒体')
        return media.reserve(ctx.session,ctx.actor,row,body.kind,body.index)
    @router.get('/student/workspaces/{wid}/media-assets/{aid}')
    def asset(wid:UUID,aid:UUID,ctx=sa):
        Workspaces().owned(ctx.session,ctx.actor,str(wid))
        op=ctx.session.get(MediaOperation,str(aid));row=ctx.session.get(MediaAsset,str(aid))
        if not op or not row:raise DomainError(404,'not_found','未找到课堂媒体')
        allocation=budget(ctx,op.budget_id,str(wid));resource,_=media.sources(ctx.session,allocation)
        binding=ctx.session.get(CourseBinding,str(wid));build=ctx.session.get(CourseBuild,resource.build_id)
        from app.modules.generation.service import independent_active
        if independent_active(ctx.session,str(wid)):raise DomainError(409,'independent_check','独立检测期间不开放辅助媒体')
        if not binding or binding.active_unit_id!=build.unit_id:raise DomainError(404,'not_found','当前课程无此媒体')
        path=(media.ROOT/row.body['file']).resolve()
        if not path.is_relative_to(media.ROOT.resolve()) or not path.is_file():raise DomainError(404,'not_found','媒体文件暂不可用')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=row.sha256:raise DomainError(409,'media_integrity_error','媒体完整性校验失败')
        return FileResponse(path,media_type=row.body['mime'],headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; sandbox"})
    return router
