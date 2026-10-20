from uuid import UUID
from fastapi import APIRouter,Depends,Header,Query,Body
from pydantic import Field
from sqlalchemy import select
from app.modules.generation import service
from app.modules.learning.contracts import Contract
from app.modules.knowledge import service as knowledge
from app.modules.resources import service as resources
from app.infrastructure.db.models import (CourseBuild,GenerationPolicy,KnowledgeDocument,KnowledgeSourceState,
    ResourceArtifact,ModelOperation,AgentRun,SearchOperation,AuditEvent,ProductionReview,ResourceBudget,OpenMAICJob,Workspace)
from app.modules.learning.content_repository import content_hash
from app.modules.workspaces.service import Workspaces
from app.modules.errors import DomainError


class PolicyIn(Contract):
    private_auto_release: bool = Field(default=False,strict=True)
    expected_revision: int=Field(ge=0,strict=True)
    courses: bool
    interaction: bool
    search: bool=False
    acknowledge_development_only: bool


class PolicyOut(Contract):
    private_auto_release: bool = False
    revision: int
    courses: bool
    interaction: bool
    search: bool
    tavily_key_configured: bool
    limits: dict


class Review(service.Revision):
    approve: bool
    note: str=Field(min_length=10,max_length=2000)


class Activate(service.Revision):
    course_revision: int=Field(ge=0,strict=True)


class SourceState(Contract):
    expected_revision: int=Field(ge=0,strict=True)
    enabled: bool


def generation_router(student,admin):
    router=APIRouter(prefix='/api/v1')
    sa,aa=Depends(student,scope='function'),Depends(admin,scope='function')
    prefix='/student/workspaces/{wid}/course-builds'

    def policy_out(session):
        from app.modules.knowledge.search import key
        row=session.get(GenerationPolicy,1)
        return {'private_auto_release':row.private_auto_release,'revision':row.revision,'courses':row.courses,'interaction':row.interaction,'search':row.search,'tavily_key_configured':bool(key()),
            'limits':{'course_output_tokens':4096,'course_calls':6,'manual_repairs':1,'interaction_output_tokens':512,'interaction_calls':3,
                'search_daily_calls':20,'search_calls_per_topic':2,'automatic_retries':False,'quota':'shared_development_calls'}}

    @router.get('/admin/generation-policy',response_model=PolicyOut)
    def inspect(ctx=aa): return policy_out(ctx.session)

    @router.post('/admin/generation-policy',response_model=PolicyOut)
    def policy(body:PolicyIn,ctx=aa):
        row=ctx.session.get(GenerationPolicy,1,with_for_update=True)
        if row.revision!=body.expected_revision: raise DomainError(409,'revision_conflict','用途配置已变更')
        if not body.acknowledge_development_only: raise DomainError(422,'scope_confirmation_required','请确认开发范围')
        from app.modules.knowledge.search import key
        if body.search and not key(): raise DomainError(409,'search_unconfigured','请在本机配置 Tavily 密钥，不在网页粘贴密钥')
        row.courses,row.interaction,row.search=body.courses,body.interaction,body.search
        row.private_auto_release=body.private_auto_release;row.revision+=1
        ctx.session.add(AuditEvent(actor_id=ctx.actor.account.id,action='generation.configure',target_id='1'))
        ctx.session.flush();return policy_out(ctx.session)

    @router.post(prefix,response_model=service.BuildOut,status_code=201)
    def start(wid:UUID,body:service.TopicIn,ctx=sa,key:str=Header(alias='Idempotency-Key',min_length=1,max_length=128)):
        return service.start(ctx.session,ctx.actor,str(wid),body,key)

    @router.get(prefix,response_model=service.BuildsOut)
    def builds(wid:UUID,ctx=sa,offset:int=Query(0,ge=0)):
        Workspaces().owned(ctx.session,ctx.actor,str(wid))
        return {'items':[service.public(ctx.session,row) for row in ctx.session.scalars(select(CourseBuild).where(CourseBuild.workspace_id==str(wid)).order_by(CourseBuild.created_at.desc(),CourseBuild.id).offset(offset).limit(20))]}

    @router.get(prefix+'/{bid}',response_model=service.BuildOut)
    def build(wid:UUID,bid:UUID,ctx=sa): return service.public(ctx.session,service.owned(ctx.session,ctx.actor,str(wid),str(bid)))

    @router.post(prefix+'/{bid}/diagnostic',response_model=service.BuildOut)
    def diagnostic(wid:UUID,bid:UUID,body:service.DiagnosticIn,ctx=sa): return service.diagnose(ctx.session,ctx.actor,str(wid),str(bid),body)

    @router.post(prefix+'/{bid}/plan',response_model=service.BuildOut)
    def plan(wid:UUID,bid:UUID,body:service.PlanIn,ctx=sa): return service.revise_plan(ctx.session,ctx.actor,str(wid),str(bid),body)

    @router.post(prefix+'/{bid}/generate',response_model=service.BuildOut,status_code=202)
    def generate(wid:UUID,bid:UUID,body:service.Revision,ctx=sa,key:str=Header(alias='Idempotency-Key',min_length=1,max_length=128)):
        return service.generate(ctx.session,ctx.actor,str(wid),str(bid),body,key)

    @router.post(prefix+'/{bid}/repair',response_model=service.BuildOut,status_code=202)
    def repair(wid:UUID,bid:UUID,body:service.Revision,ctx=sa,key:str=Header(alias='Idempotency-Key',min_length=1,max_length=128)):
        return service.generate(ctx.session,ctx.actor,str(wid),str(bid),body,key,True)

    @router.post(prefix+'/{bid}/cancel',response_model=service.BuildOut)
    def cancel(wid:UUID,bid:UUID,body:service.Revision,ctx=sa):
        row=service.owned(ctx.session,ctx.actor,str(wid),str(bid),active=True)
        service.revision(row,body.expected_revision,{'diagnostic','confirm_plan','queued','unknown','failed','pending_review'})
        if row.run_id:
            from app.infrastructure.runtime.model_tasks import cancel
            run=ctx.session.get(AgentRun,row.run_id)
            for op in ctx.session.scalars(select(ModelOperation).where(ModelOperation.run_id==row.run_id)): cancel(ctx.session,run,op.id)
            run.status='cancelled';run.revision+=1
        row.status='cancelled';row.revision+=1;ctx.session.flush();return service.public(ctx.session,row)

    @router.post(prefix+'/{bid}/activate')
    def activate(wid:UUID,bid:UUID,body:Activate,ctx=sa): return service.activate(ctx.session,ctx.actor,str(wid),str(bid),body,body.course_revision)

    @router.post(prefix+'/{bid}/search',status_code=202)
    def search(wid:UUID,bid:UUID,ctx=sa,key:UUID=Header(alias='Idempotency-Key')):
        from app.modules.knowledge.search import reserve
        row=service.owned(ctx.session,ctx.actor,str(wid),str(bid),active=True)
        if service.independent_active(ctx.session,str(wid)): raise DomainError(409,'independent_check','诊断与独立检测期间不提供外部检索辅助')
        op=reserve(ctx.session,row,str(key));return {'operation_id':op.id,'status':op.status}

    @router.get(prefix+'/{bid}/search')
    def searches(wid:UUID,bid:UUID,ctx=sa):
        service.owned(ctx.session,ctx.actor,str(wid),str(bid))
        if service.independent_active(ctx.session,str(wid)): raise DomainError(409,'independent_check','诊断与独立检测期间不提供外部检索辅助')
        return {'items':[{'operation_id':op.id,'status':op.status,'result':op.result if op.status=='succeeded' else None,'credits':op.credits} for op in ctx.session.scalars(select(SearchOperation).where(SearchOperation.build_id==str(bid)).order_by(SearchOperation.created_at))]}

    @router.get('/admin/course-builds',response_model=service.BuildsOut)
    def pending(ctx=aa,offset:int=Query(0,ge=0)):
        return {'items':[service.public(ctx.session,row,True) for row in ctx.session.scalars(select(CourseBuild).order_by(CourseBuild.created_at.desc(),CourseBuild.id).offset(offset).limit(20))]}

    @router.post('/admin/course-builds/{bid}/review',response_model=service.BuildOut)
    def review(bid:UUID,body:Review,ctx=aa):
        row=ctx.session.get(CourseBuild,str(bid),with_for_update=True)
        if not row: raise DomainError(404,'not_found','未找到生成任务')
        ctx.session.add(ProductionReview(target_kind='course',target_id=row.id,revision=body.expected_revision,decision='approve' if body.approve else 'reject',note=body.note,reviewer_id=ctx.actor.account.id))
        if not body.approve:
            service.revision(row,body.expected_revision,{'pending_review','ready'})
            row.status='rejected';row.revision+=1
            if row.unit_id:
                from app.infrastructure.db.models import ContentApproval
                for identifier in [row.unit_id,*[a['unit_id'] for a in row.candidate['spec']['activities']]]:
                    approval=ctx.session.get(ContentApproval,identifier,with_for_update=True)
                    if approval: approval.enabled=False;approval.revision+=1
            ctx.session.add(AuditEvent(actor_id=ctx.actor.account.id,action='course.reject',target_id=row.id))
            ctx.session.flush();return service.public(ctx.session,row,True)
        return service.approve(ctx.session,ctx.actor,row,body.expected_revision,body.note)

    @router.get('/admin/knowledge-sources')
    def sources(ctx=aa):
        def state(identifier):
            row=ctx.session.get(KnowledgeSourceState,identifier)
            return {'enabled':row.enabled if row else True,'revision':row.revision if row else 0}
        rational=service.rational.source()
        return {'knowledge_points':knowledge.NODES,'documents':[{'source_id':'builtin.'+key,'title':node['title']+'（自编开发依据）',**state('builtin.'+key)} for key,node in knowledge.NODES.items()]+[{**rational,**state(rational['source_id'])}]+[{'source_id':row.id,'version':row.version,**row.body,**state(row.id)} for row in ctx.session.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc()).limit(100))]}

    @router.post('/admin/knowledge-sources',status_code=201)
    def source(body:knowledge.SourceIn,ctx=aa):
        if not set(body.knowledge_points).issubset(knowledge.NODES): raise DomainError(422,'unknown_knowledge_point','请使用已登记知识点 ID，候选知识点不能直接标为已确认')
        data=body.model_dump(exclude={'workspace_id','acknowledge_rights'})
        row=KnowledgeDocument(body=data,version=content_hash(data),workspace_id=body.workspace_id,creator_id=ctx.actor.account.id)
        ctx.session.add(row);ctx.session.flush();return {'source_id':row.id,'version':row.version}

    @router.post('/admin/knowledge-sources/{identifier}/state')
    def source_state(identifier:str,body:SourceState,ctx=aa):
        ctx.session.get(GenerationPolicy,1,with_for_update=True)
        if identifier not in {'builtin.'+k for k in knowledge.NODES}|{service.rational.SOURCE_ID} and not ctx.session.get(KnowledgeDocument,identifier): raise DomainError(404,'not_found','未找到来源')
        state=ctx.session.get(KnowledgeSourceState,identifier,with_for_update=True)
        if (state.revision if state else 0)!=body.expected_revision: raise DomainError(409,'revision_conflict','来源状态已变更')
        if not state: state=KnowledgeSourceState(source_id=identifier,enabled=body.enabled,revision=1);ctx.session.add(state)
        else: state.enabled=body.enabled;state.revision+=1
        ctx.session.add(AuditEvent(actor_id=ctx.actor.account.id,action='knowledge.source_state',target_id=identifier));ctx.session.flush()
        return {'revision':state.revision,'enabled':state.enabled}

    @router.get('/student/workspaces/{wid}/resources')
    def resource_list(wid:UUID,ctx=sa):
        Workspaces().owned(ctx.session,ctx.actor,str(wid))
        if service.independent_active(ctx.session,str(wid)): raise DomainError(409,'independent_check','诊断与独立检测期间不展示课程辅助资源')
        rows=ctx.session.scalars(select(ResourceArtifact).where(ResourceArtifact.workspace_id==str(wid),ResourceArtifact.status=='approved').order_by(ResourceArtifact.created_at.desc()).limit(30))
        result=[]
        for row in rows:
            build=ctx.session.get(CourseBuild,row.build_id)
            if build.status!='ready': continue
            try: knowledge.check_snapshot(ctx.session,build.sources)
            except DomainError: continue
            from app.modules.resources.classroom import public_manifest
            from app.modules.resources.media import classroom_overlay
            result.append({'resource_id':row.id,'title':row.body['title'],'sha256':row.sha256,'unit_id':build.unit_id,'html':resources.render(row.body),'cards':row.body.get('cards',[]),
                'classroom':public_manifest(classroom_overlay(ctx.session,row)) if row.body.get('classroom') else None})
        return {'items':result}

    @router.post('/admin/course-builds/{bid}/resources',status_code=201)
    def import_resource(bid:UUID,body:resources.ResourceIn,ctx=aa):
        row=ctx.session.get(CourseBuild,str(bid))
        if not row: raise DomainError(404,'not_found','未找到课程')
        data=body.model_dump();artifact=ResourceArtifact(workspace_id=row.workspace_id,build_id=row.id,body=data,sha256=content_hash(data))
        ctx.session.add(artifact);ctx.session.flush();return {'resource_id':artifact.id,'status':artifact.status}

    @router.post('/admin/course-builds/{bid}/openmaic-import',status_code=201)
    def import_zip(bid:UUID,ctx=aa,data:bytes=Body(media_type='application/zip')):
        row=ctx.session.get(CourseBuild,str(bid))
        if not row: raise DomainError(404,'not_found','未找到课程')
        body=resources.import_classroom(data);artifact=ResourceArtifact(workspace_id=row.workspace_id,build_id=row.id,body=body,sha256=content_hash(body))
        ctx.session.add(artifact);ctx.session.flush();return {'resource_id':artifact.id,'status':artifact.status}

    @router.post('/admin/resources/{rid}/review')
    def review_resource(rid:UUID,body:Review,ctx=aa):
        row=ctx.session.get(ResourceArtifact,str(rid),with_for_update=True)
        if not row: raise DomainError(404,'not_found','未找到资源')
        if row.revision!=body.expected_revision: raise DomainError(409,'revision_conflict','资源审核已变更')
        ctx.session.add(ProductionReview(target_kind='resource',target_id=row.id,revision=row.revision,decision='approve' if body.approve else 'reject',note=body.note,reviewer_id=ctx.actor.account.id))
        row.status='approved' if body.approve else 'rejected';row.revision+=1
        if body.approve and row.body.get('remote_job_id'):
            job=ctx.session.scalar(select(OpenMAICJob).where(OpenMAICJob.build_id==row.build_id,OpenMAICJob.remote_id==row.body['remote_job_id']).with_for_update())
            if job and job.status=='pending_review':job.status='completed'
        ctx.session.add(AuditEvent(actor_id=ctx.actor.account.id,action='resource.review',target_id=row.id));ctx.session.flush()
        return {'status':row.status,'revision':row.revision}

    @router.get('/admin/resources')
    def resources_pending(ctx=aa,offset:int=Query(0,ge=0),build_id:UUID|None=None):
        query=select(ResourceArtifact)
        if build_id is not None: query=query.where(ResourceArtifact.build_id==str(build_id))
        return {'items':[{'resource_id':r.id,'build_id':r.build_id,'workspace_id':r.workspace_id,'body':r.body,'status':r.status,'revision':r.revision,'sha256':r.sha256,'html':resources.render(r.body)} for r in ctx.session.scalars(query.order_by(ResourceArtifact.created_at.desc(),ResourceArtifact.id).offset(offset).limit(20))]}

    @router.post('/admin/course-builds/{bid}/openmaic',status_code=202)
    def openmaic(bid:UUID,ctx=aa):
        import hashlib
        from pathlib import Path
        row=ctx.session.get(CourseBuild,str(bid),with_for_update=True)
        if not row or row.status!='ready': raise DomainError(409,'course_not_ready','请先审核并发布本课程')
        knowledge.check_snapshot(ctx.session,row.sources)
        secret_path=Path('.local/openmaic-proxy.key')
        if not secret_path.is_file(): raise DomainError(409,'resource_budget_unconfigured','需先通过本机维护流程确认资源总预算并配置代理')
        secret=secret_path.read_text(encoding='utf-8').strip()
        budget=ctx.session.scalar(select(ResourceBudget).where(ResourceBudget.secret_hash==hashlib.sha256(secret.encode()).hexdigest()).with_for_update())
        if not budget or not budget.enabled: raise DomainError(409,'resource_budget_paused','资源总预算尚未开放或已停用')
        prior=ctx.session.scalar(select(OpenMAICJob).where(OpenMAICJob.budget_id==budget.id))
        if prior:
            if prior.build_id!=row.id: raise DomainError(429,'resource_course_budget_exhausted','首次预算仅允许一门课堂，不自动追加')
            return {'job_id':prior.id,'status':prior.status}
        job=OpenMAICJob(workspace_id=row.workspace_id,build_id=row.id,budget_id=budget.id)
        ctx.session.add(job);ctx.session.add(AuditEvent(actor_id=ctx.actor.account.id,action='resource.generate',target_id=row.id));ctx.session.flush()
        return {'job_id':job.id,'status':job.status}

    @router.post('/student/workspaces/{wid}/course-builds/{bid}/openmaic',status_code=202)
    def student_openmaic(wid:UUID,bid:UUID,ctx=sa):
        row=service.owned(ctx.session,ctx.actor,str(wid),str(bid),active=True)
        from app.modules.learning.courses import require_bound_unit
        require_bound_unit(ctx.session,str(wid),row.unit_id)
        if not ctx.session.get(GenerationPolicy,1).private_auto_release:raise DomainError(409,'resource_autonomy_paused','平台尚未开放私有课堂自动检查，可继续现有课程')
        if service.independent_active(ctx.session,str(wid)):raise DomainError(409,'independent_check','独立尝试期间不生成辅助课堂')
        return openmaic(bid,ctx)

    @router.get('/student/workspaces/{wid}/course-builds/{bid}/openmaic')
    def student_openmaic_status(wid:UUID,bid:UUID,ctx=sa):
        service.owned(ctx.session,ctx.actor,str(wid),str(bid))
        rows=ctx.session.scalars(select(OpenMAICJob).where(OpenMAICJob.build_id==str(bid),OpenMAICJob.workspace_id==str(wid)))
        return {'items':[{'job_id':r.id,'status':r.status,'error_code':r.error_code} for r in rows]}

    @router.post('/student/workspaces/{wid}/course-builds/{bid}/openmaic/{jid}/cancel')
    def student_cancel_openmaic(wid:UUID,bid:UUID,jid:UUID,ctx=sa):
        service.owned(ctx.session,ctx.actor,str(wid),str(bid),active=True)
        row=ctx.session.get(OpenMAICJob,str(jid))
        if not row or row.build_id!=str(bid) or row.workspace_id!=str(wid):raise DomainError(404,'not_found','没有此课堂任务')
        ctx.session.refresh(row)  # workspace lock was acquired by owned()
        if row.status not in {'completed','cancelled','failed'}:
            from app.modules.resources.quality import cancel_checks
            cancel_checks(ctx.session,row)
            ctx.session.flush()
            row=ctx.session.scalar(select(OpenMAICJob).where(OpenMAICJob.id==str(jid)).with_for_update().execution_options(populate_existing=True))
            row.status='cancelled';ctx.session.get(ResourceBudget,row.budget_id).enabled=False
            ctx.session.add(AuditEvent(actor_id=ctx.actor.account.id,action='resource.student_cancel',target_id=row.id))
        return {'job_id':row.id,'status':row.status}

    @router.post('/admin/openmaic-jobs/{jid}/reimport')
    def reimport_resource(jid:UUID,ctx=aa):
        row=ctx.session.get(OpenMAICJob,str(jid),with_for_update=True)
        if not row or not row.remote_id or not row.classroom_id or row.status not in {'pending_review','import_pending','completed'}:
            raise DomainError(409,'existing_classroom_required','仅可重新读取已生成课堂，不创建模型任务')
        build=ctx.session.get(CourseBuild,row.build_id)
        if build.status!='ready':raise DomainError(409,'course_not_ready','原课程已撤回')
        knowledge.check_snapshot(ctx.session,build.sources)
        from app.infrastructure.db.models import now
        row.status='import_pending';row.next_poll_at=now();row.error_code=None
        ctx.session.add(AuditEvent(actor_id=ctx.actor.account.id,action='resource.reimport',target_id=row.id))
        return {'job_id':row.id,'status':row.status,'model_call':False}

    @router.get('/admin/openmaic-jobs')
    def resource_jobs(ctx=aa):
        rows=ctx.session.scalars(select(OpenMAICJob).order_by(OpenMAICJob.created_at.desc()).limit(20))
        return {'items':[{'job_id':r.id,'build_id':r.build_id,'budget_id':r.budget_id,'status':r.status,'remote_id':r.remote_id,'error_code':r.error_code} for r in rows],
            'budgets':[{'budget_id':b.id,'enabled':b.enabled,'calls':b.calls,'input_reserved_or_known':b.input_reserved,'output_reserved_or_known':b.output_reserved,
            'max_calls':20,'max_input_tokens':128000,'max_output_tokens':32768} for b in ctx.session.scalars(select(ResourceBudget).order_by(ResourceBudget.created_at.desc()).limit(10))]}

    @router.post('/admin/openmaic-jobs/{jid}/cancel')
    def cancel_resource(jid:UUID,ctx=aa):
        row=ctx.session.get(OpenMAICJob,str(jid))
        if not row: raise DomainError(404,'not_found','未找到资源任务')
        ctx.session.get(Workspace,row.workspace_id,with_for_update=True)
        ctx.session.refresh(row)
        from app.modules.resources.quality import cancel_checks
        cancel_checks(ctx.session,row)
        ctx.session.flush()
        row=ctx.session.scalar(select(OpenMAICJob).where(OpenMAICJob.id==str(jid)).with_for_update().execution_options(populate_existing=True))
        row.status='cancelled';ctx.session.get(ResourceBudget,row.budget_id,with_for_update=True).enabled=False
        return {'status':'cancelled','notice':'本地停止发布和后续模型调用；在途上游请求可能仍产生费用，不声称远端已停止。'}

    return router
