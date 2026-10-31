from uuid import UUID
from fastapi import APIRouter, Depends, Header
from pydantic import Field
from sqlalchemy import select
from app.infrastructure.db.models import ResourceArtifact, ResourceProgress, CourseBuild, CourseBinding, AuditEvent, PlaybackReceipt, now
from app.modules.learning.contracts import Contract
from app.modules.workspaces.service import Workspaces
from app.modules.errors import DomainError
from app.modules.resources.classroom import check_question


class Playback(Contract):
    expected_revision: int = Field(ge=0, strict=True)
    resource_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    scene: int = Field(ge=0, le=29, strict=True)
    action: int = Field(ge=0, le=300, strict=True)
    media_seconds: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False)
    media_positions: dict[str,float] = Field(default_factory=dict,max_length=50)
    question_id: str | None = Field(default=None, max_length=100)
    answer: str = Field(default='', max_length=1500)


def classroom_router(student):
    router=APIRouter(prefix='/api/v1')
    def owned(ctx,wid,rid):
        Workspaces().owned(ctx.session,ctx.actor,wid)
        from app.modules.generation.service import independent_active
        from app.modules.knowledge.service import check_snapshot
        if independent_active(ctx.session,wid):raise DomainError(409,'independent_check','独立检测期间不开放辅助课堂')
        row=ctx.session.get(ResourceArtifact,rid)
        binding=ctx.session.get(CourseBinding,wid)
        build=ctx.session.get(CourseBuild,row.build_id) if row else None
        if not row or row.workspace_id!=wid or row.status!='approved' or not build or build.status!='ready' or not binding or binding.active_unit_id!=build.unit_id:
            raise DomainError(404,'not_found','当前课程下没有此可用课堂')
        check_snapshot(ctx.session,build.sources)
        return row
    def progress(ctx,rid):
        return ctx.session.scalar(select(ResourceProgress).where(ResourceProgress.account_id==ctx.actor.account.id,
            ResourceProgress.resource_id==rid).with_for_update())
    root='/student/workspaces/{wid}/classrooms/{rid}/progress'
    @router.get(root)
    def get_progress(wid:UUID,rid:UUID,ctx=Depends(student,scope='function')):
        resource=owned(ctx,str(wid),str(rid));row=progress(ctx,str(rid))
        return {'revision':row.revision if row else 0,'resource_hash':resource.sha256,'body':row.body if row and row.body.get('resource_hash')==resource.sha256 else None}
    @router.put(root)
    def save_progress(wid:UUID,rid:UUID,body:Playback,ctx=Depends(student,scope='function'),key:str=Header(alias='Idempotency-Key',min_length=1,max_length=128)):
        Workspaces().owned(ctx.session,ctx.actor,str(wid),active=True)
        resource=owned(ctx,str(wid),str(rid));row=progress(ctx,str(rid))
        data=body.model_dump();from app.modules.learning.content_repository import content_hash
        receipt=ctx.session.scalar(select(PlaybackReceipt).where(PlaybackReceipt.account_id==ctx.actor.account.id,PlaybackReceipt.resource_id==str(rid),PlaybackReceipt.creation_key==key))
        if receipt:
            if receipt.request_hash!=content_hash(data):raise DomainError(409,'idempotency_conflict','标识已用于其他课堂记录')
            return receipt.response
        if row and row.body.get('last_key')==key:
            if row.body.get('request_hash')!=content_hash(data):raise DomainError(409,'idempotency_conflict','标识已用于不同播放记录')
            return {'revision':row.revision,'resource_hash':resource.sha256,'body':row.body}
        if resource.sha256!=body.resource_hash:raise DomainError(409,'resource_changed','课堂已更换版本，请重新载入')
        if (row.revision if row else 0)!=body.expected_revision:raise DomainError(409,'revision_conflict','另一页面已更新播放进度，请刷新')
        manifest=resource.body.get('classroom')
        if not manifest or body.scene>=len(manifest['scenes']) or body.action>len(manifest['scenes'][body.scene]['actions']):
            raise DomainError(422,'invalid_playback','播放位置超出课堂范围')
        from math import isfinite
        if any(not 1<=len(k)<=200 or not isfinite(v) or not 0<=v<=86400 for k,v in body.media_positions.items()):
            raise DomainError(422,'invalid_playback','媒体进度超出支持范围')
        result=({**check_question(manifest,body.scene,body.question_id,body.answer),'scene':body.scene,'question_id':body.question_id} if body.question_id else (row.body.get('check') if row else None))
        saved={**{k:v for k,v in data.items() if k not in {'expected_revision','answer'}},'updated_at':now(),
            'last_key':key,'request_hash':content_hash(data),'student_answer':body.answer if body.question_id else (row.body.get('student_answer','') if row else ''), 'check':result,'learning_effect_validated':False}
        if not row:row=ResourceProgress(account_id=ctx.actor.account.id,resource_id=str(rid),body=saved,revision=0);ctx.session.add(row)
        row.body=saved;row.revision+=1;row.updated_at=now()
        ctx.session.add(AuditEvent(actor_id=ctx.actor.account.id,action='classroom.progress',target_id=str(rid)))
        ctx.session.flush()
        response={'revision':row.revision,'resource_hash':resource.sha256,'body':saved}
        ctx.session.add(PlaybackReceipt(account_id=ctx.actor.account.id,resource_id=str(rid),creation_key=key,request_hash=content_hash(data),response=response))
        return response
    @router.post('/student/workspaces/{wid}/classrooms/prepare',status_code=201)
    def prepare(wid:UUID,ctx=Depends(student,scope='function')):
        Workspaces().owned(ctx.session,ctx.actor,str(wid),active=True)
        from app.modules.generation.service import independent_active
        from app.modules.knowledge.service import check_snapshot
        from app.modules.resources.storyboard import compile_lesson
        from app.modules.learning.content_repository import content_hash
        if independent_active(ctx.session,str(wid)):raise DomainError(409,'independent_check','独立检测期间不准备辅助课堂')
        binding=ctx.session.get(CourseBinding,str(wid))
        build=ctx.session.scalar(select(CourseBuild).where(CourseBuild.workspace_id==str(wid),CourseBuild.unit_id==(binding.active_unit_id if binding else ''),CourseBuild.status=='ready'))
        if not build:raise DomainError(409,'course_required','请先确认本项目的动态生成课程')
        check_snapshot(ctx.session,build.sources)
        body=compile_lesson(build);digest=content_hash(body)
        row=ctx.session.scalar(select(ResourceArtifact).where(ResourceArtifact.workspace_id==str(wid),ResourceArtifact.build_id==build.id,ResourceArtifact.sha256==digest))
        if not row:
            row=ResourceArtifact(workspace_id=str(wid),build_id=build.id,body=body,sha256=digest,status='approved')
            ctx.session.add(row);ctx.session.flush()
            ctx.session.add(AuditEvent(actor_id=ctx.actor.account.id,action='classroom.verified_compile',target_id=row.id))
        return {'resource_id':row.id,'math_verified':True,'external_calls':0}
    return router
