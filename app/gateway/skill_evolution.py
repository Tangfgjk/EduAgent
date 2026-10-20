from uuid import UUID
from fastapi import APIRouter,Depends,Header,Query
from sqlalchemy import select
from app.infrastructure.db.models import AgentRun
from app.modules.skill_evolution import service
from app.modules.skill_evolution.contracts import EvolutionStart,EvolutionDecision,EvolutionProposal,EvolutionOut,EvolutionsOut
from app.modules.learning.contracts import RunRevision
from app.modules.errors import DomainError
from app.modules.memory.contracts import FactOut
from app.modules.learning.service import Learning,event
from app.modules.learning.contracts import Contract
from pydantic import Field


class AutomationIn(Contract):
    enabled:bool=Field(strict=True)
    expected_revision:int=Field(ge=0,strict=True)


def evolution_router(student,admin):
    router=APIRouter(prefix='/api/v1');sa=Depends(student,scope='function');aa=Depends(admin,scope='function')
    key=Header(alias='Idempotency-Key',min_length=1,max_length=64)
    root='/student/workspaces/{wid}/skill-evolutions'
    @router.get('/student/workspaces/{wid}/learning-automation')
    def automation(wid:UUID,ctx=sa):
        from app.modules.skill_evolution.automation import view
        return view(ctx.session,ctx.actor,str(wid))
    @router.put('/student/workspaces/{wid}/learning-automation')
    def configure_automation(wid:UUID,body:AutomationIn,ctx=sa):
        from app.modules.skill_evolution.automation import configure
        return configure(ctx.session,ctx.actor,str(wid),body.enabled,body.expected_revision)
    @router.get(root,response_model=EvolutionsOut)
    def listing(wid:UUID,ctx=sa,offset:int=Query(0,ge=0)):
        service.spaces.owned(ctx.session,ctx.actor,str(wid))
        rows=ctx.session.scalars(select(AgentRun).where(AgentRun.workspace_id==str(wid),AgentRun.kind=='skill_evolution').order_by(AgentRun.created_at.desc(),AgentRun.id.desc()).offset(offset).limit(20))
        return {'items':[service.public(ctx.session,r,False) for r in rows]}
    @router.post(root,response_model=EvolutionOut,status_code=202)
    def start(wid:UUID,body:EvolutionStart,ctx=sa,idempotency_key:str=key):return service.start(ctx.session,ctx.actor,str(wid),idempotency_key,body.mode,body.allow_personal_trial)
    @router.get(root+'/{rid}',response_model=EvolutionOut)
    def detail(wid:UUID,rid:UUID,ctx=sa):return service.public(ctx.session,service.owned(ctx.session,ctx.actor,str(wid),str(rid)))
    @router.post(root+'/{rid}/withdraw-trial')
    def withdraw_trial(wid:UUID,rid:UUID,ctx=sa):
        from app.modules.skill_evolution.trials import withdraw
        return withdraw(ctx.session,ctx.actor,str(wid),str(rid))
    @router.post(root+'/{rid}/proposal',response_model=EvolutionOut,status_code=202)
    def propose(wid:UUID,rid:UUID,body:EvolutionProposal,ctx=sa,idempotency_key:str=key):return service.propose(ctx.session,ctx.actor,service.owned(ctx.session,ctx.actor,str(wid),str(rid),True),body.model_dump(),idempotency_key)
    @router.post(root+'/{rid}/cancel',response_model=EvolutionOut)
    def cancel(wid:UUID,rid:UUID,body:RunRevision,ctx=sa):
        run=service.owned(ctx.session,ctx.actor,str(wid),str(rid),True)
        if run.revision!=body.expected_revision:raise DomainError(409,'revision_conflict','状态已变化')
        if run.status not in {'queued','awaiting_input'}:raise DomainError(409,'invalid_phase','任务已结束')
        run.status,run.gate_id='cancelled',None;run.view={**(run.view or {}),'phase':'cancelled'};run.revision+=1;event(ctx.session,run,'evolution.cancelled')
        return service.public(ctx.session,run)
    @router.get('/admin/skill-evolutions',response_model=EvolutionsOut)
    def queue(ctx=aa,offset:int=Query(0,ge=0)):
        rows=ctx.session.scalars(select(AgentRun).where(AgentRun.kind=='skill_evolution').order_by(AgentRun.created_at.desc(),AgentRun.id.desc()).offset(offset).limit(20))
        return {'items':[service.public(ctx.session,r,False) for r in rows]}
    @router.get('/admin/skill-evolutions/{rid}',response_model=EvolutionOut)
    def inspect(rid:UUID,ctx=aa):return service.public(ctx.session,service.admin_owned(ctx.session,ctx.actor,str(rid)))
    @router.get('/admin/skill-evolutions/{rid}/facts/{fid}',response_model=FactOut)
    def source(rid:UUID,fid:UUID,ctx=aa):
        from types import SimpleNamespace
        from app.infrastructure.db.models import EvolutionDataset,Workspace,Account,AuditEvent
        from app.modules.memory.service import Memory
        run=service.admin_owned(ctx.session,ctx.actor,str(rid));dataset=ctx.session.get(EvolutionDataset,run.id)
        allowed={r['id'] for r in dataset.body['facts']}|{r['fact_id'] for r in dataset.body['judgments']}
        if str(fid) not in allowed:raise DomainError(404,'not_found','此来源不在冻结数据集中')
        owner=ctx.session.get(Account,ctx.session.get(Workspace,run.workspace_id).owner_id)
        result=Memory().fact(ctx.session,SimpleNamespace(account=owner),run.workspace_id,str(fid))
        ctx.session.add(AuditEvent(actor_id=ctx.actor.account.id,action='evolution.source_read',target_id=str(fid)))
        return result
    @router.post('/admin/skill-evolutions/{rid}/decision' ,response_model=EvolutionOut)
    def decide(rid:UUID,body:EvolutionDecision,ctx=aa,idempotency_key:str=key):return service.decide(ctx.session,ctx.actor,service.admin_owned(ctx.session,ctx.actor,str(rid)),body.model_dump(),idempotency_key)
    return router
