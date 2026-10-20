"""Explicitly opted-in personal RSI: six new facts, at most once per Beijing day."""
from types import SimpleNamespace
from sqlalchemy import select
from app.infrastructure.db.models import LearningAutomation,LearningFact,Workspace,Account,AgentRun,ModelPolicy,SkillTrial,EvolutionDataset,ModelOperation,now,new_id
from app.modules.workspaces.service import Workspaces
from app.modules.learning.content_repository import content_hash
from app.modules.errors import DomainError


def view(session,actor,wid):
    Workspaces().owned(session,actor,wid)
    row=session.get(LearningAutomation,wid)
    return {'enabled':bool(row and row.enabled),'revision':row.revision if row else 0,'state':row.body if row else {},
        'limits':{'new_project_facts':6,'candidates_per_project_day':1,'max_output_tokens':2048,'max_calls_per_candidate':2,
            'automatic_retry':False,'effect_status':'not_validated','scope':'synthetic/adult current project; DeepSeek only'}}


def configure(session,actor,wid,enabled,revision):
    space=Workspaces().owned(session,actor,wid,active=True)
    if space.kind!='project' or actor.account.source_kind not in {'synthetic','adult_test'}:raise DomainError(409,'development_project_required','自动改进仅用于模拟或成人正式项目')
    row=session.get(LearningAutomation,wid,with_for_update=True)
    if (row.revision if row else 0)!=revision:raise DomainError(409,'revision_conflict','自动改进设置已变化')
    if not row:row=LearningAutomation(workspace_id=wid,enabled=False,revision=0,body={});session.add(row)
    if enabled and not row.enabled:
        ids=list(session.scalars(select(LearningFact.id).where(LearningFact.workspace_id==wid)))
        row.body={**row.body,'baseline_fact_ids':ids,'source_kind':actor.account.source_kind,'consent_token':new_id(),'consent_version':'personal-semantic-automation.v1','consented_at':now()}
    row.enabled=enabled;row.revision+=1
    if not enabled:
        from app.modules.skill_evolution.trials import revert
        for trial in session.scalars(select(SkillTrial).where(SkillTrial.workspace_id==wid,SkillTrial.status=='active')):revert(session,trial,'automation_disabled')
        for run,dataset in session.execute(select(AgentRun,EvolutionDataset).join(EvolutionDataset,EvolutionDataset.id==AgentRun.id)
                .where(AgentRun.workspace_id==wid,AgentRun.status.in_(['queued','awaiting_input']))):
            if not dataset.body.get('automation_consent'):continue
            run.status='cancelled';run.revision+=1;run.view={**(run.view or {}),'phase':'cancelled','reason':'学生关闭自动调整，未完成自动候选已取消'}
            from app.infrastructure.runtime.model_tasks import ModelTaskRunner
            for op in session.scalars(select(ModelOperation).where(ModelOperation.run_id==run.id,ModelOperation.status.in_(['waiting','pending','running']))):
                op.billing_unknown=op.status=='running';ModelTaskRunner._end(op,'cancelled','automation_disabled')
    session.flush();return view(session,actor,wid)


def tick(session,wid):
    from app.infrastructure.runtime.model_tasks import day_key
    row=session.get(LearningAutomation,wid,with_for_update=True)
    if not row or not row.enabled or row.body.get('last_day')==day_key():return
    space=session.get(Workspace,wid);account=session.get(Account,space.owner_id)
    if not account.enabled or space.status!='active' or account.source_kind not in {'synthetic','adult_test'}:return
    if account.source_kind!=row.body.get('source_kind'):
        row.enabled=False;row.revision+=1;row.body={**row.body,'last_skip_reason':'consent_source_changed'};return
    policy=session.get(ModelPolicy,1)
    if not policy.active_id:return
    if session.scalar(select(AgentRun.id).where(AgentRun.workspace_id==wid,AgentRun.status.in_(['queued','awaiting_input']))):return
    if session.scalar(select(SkillTrial.id).where(SkillTrial.workspace_id==wid,SkillTrial.status=='active')):return
    ids=list(session.scalars(select(LearningFact.id).where(LearningFact.workspace_id==wid,LearningFact.source_kind==account.source_kind).order_by(LearningFact.id)))
    if len(set(ids)-set(row.body.get('baseline_fact_ids',[])))<6:return
    from app.modules.skill_evolution.service import start
    try:
        with session.begin_nested():
            run=start(session,SimpleNamespace(account=account),wid,'auto-'+day_key()+'-'+content_hash(ids)[:24],mode='semantic',allow_personal_trial=True,automation_consent=row.body['consent_token'])
    except DomainError as error:
        row.body={**row.body,'last_skip_reason':error.code};return
    row.body={**row.body,'last_day':day_key(),'baseline_fact_ids':ids,'last_run_id':run['run_id'],'last_skip_reason':None}
    row.revision+=1
