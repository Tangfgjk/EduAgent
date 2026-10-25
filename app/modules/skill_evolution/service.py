"""Owned offline candidates and append-only review, bridged into existing dual gates."""
from types import SimpleNamespace
from sqlalchemy import select
from app.infrastructure.db.models import Account,Workspace,AgentRun,LearningInput,LearningFact,MemoryNote,FactJudgment,CourseBinding,ActiveStrategy,StrategyVersion,ProductionReview,EvolutionDataset,EvolutionCandidate,EvolutionEvaluation,now
from app.modules.learning.content_repository import ContentRepository,content_hash
from app.modules.learning.content_service import require_content
from app.modules.learning.service import event
from app.modules.workspaces.service import Workspaces
from app.modules.planning.service import Planning,effective_policy
from app.modules.planning.compiler import compile_policy
from app.modules.planning.runtime import course_baseline
from app.modules.skill_evolution.evaluator import signature
from app.modules.errors import DomainError

VERSION='skill-evolution.v1'
spaces=Workspaces()


def fail(code,message):raise DomainError(409,code,message)


def owned(session,actor,wid,rid,active=False):
    spaces._student(actor);spaces.owned(session,actor,wid,active=active)
    row=session.scalar(select(AgentRun).where(AgentRun.id==rid,AgentRun.workspace_id==wid,AgentRun.kind=='skill_evolution').with_for_update())
    if row is None:raise DomainError(404,'not_found','未找到本项目的Skill改进任务')
    return row


def admin_owned(session,actor,rid):
    if actor.account.role!='admin':raise DomainError(403,'forbidden','仅管理员可复核开发评测')
    wid=session.scalar(select(AgentRun.workspace_id).where(AgentRun.id==rid,AgentRun.kind=='skill_evolution'))
    if wid is None:raise DomainError(404,'not_found','未找到Skill改进任务')
    session.get(Workspace,wid,with_for_update=True)
    return session.get(AgentRun,rid,with_for_update=True)


def source_snapshot(session,actor,wid):
    space=spaces.owned(session,actor,wid,active=True)
    if not actor.account.enabled:fail('account_disabled','账号已停用')
    if space.kind!='project':fail('project_required','离线Skill改进只在正式项目使用')
    if actor.account.source_kind not in {'synthetic','adult_test'}:fail('development_only','本轮仅模拟/成人开发账号')
    version,policy,reason=effective_policy(session,wid)
    if not version or not policy.get('execution'):fail('execution_baseline_required','请先审核并激活含四项执行参数的策略作为基线')
    binding=session.get(CourseBinding,wid)
    if not binding:fail('course_required','请先确认当前项目课程')
    pack=ContentRepository(session,workspace_id=wid).pack(binding.active_unit_id)
    require_content(session,actor.account.source_kind,pack.unit_id,pack.version,wid)
    stamp=now()
    condition=[LearningFact.workspace_id==wid,LearningFact.source_kind==actor.account.source_kind,LearningFact.content_version==pack.version,LearningFact.created_at<=stamp]
    facts=list(session.scalars(select(LearningFact).where(*condition).order_by(LearningFact.created_at,LearningFact.id)))
    notes=list(session.scalars(select(MemoryNote).join(LearningFact,MemoryNote.fact_id==LearningFact.id).where(*condition,MemoryNote.created_at<=stamp).order_by(MemoryNote.created_at,MemoryNote.id)))
    judgments=list(session.scalars(select(FactJudgment).join(LearningFact,FactJudgment.fact_id==LearningFact.id).where(*condition,FactJudgment.created_at<=stamp).order_by(FactJudgment.created_at,FactJudgment.id)))
    base=session.get(StrategyVersion,version)
    identity={'facts':[f.id for f in facts],'notes':[n.id for n in notes],'judgments':[j.id for j in judgments],
        'course':course_baseline(session,wid),'source_kind':actor.account.source_kind,'parent_strategy_id':version,'parent_hash':base.content_hash,'strategy_revision':session.get(ActiveStrategy,wid).revision}
    from app.modules.learning.goal_context import snapshot as goal_snapshot
    goal=goal_snapshot(session,wid,actor.account)
    return {'learning_goal':goal,'schema_version':'evolution-dataset.v1','fingerprint':content_hash(identity),'identity':identity,'signature':signature(),
        'baseline':{'strategy_id':version,'content_hash':base.content_hash,'policy':policy},'unit_id':pack.unit_id,'content_version':pack.version,
        'facts':[{'id':f.id,'task_id':f.task_id,'kind':f.kind,'stage':f.payload.get('stage'),'status':f.payload.get('status'),'hints_used':f.payload.get('hints_used',0)} for f in facts[-60:]],
        'judgments':[{'id':j.id,'fact_id':j.fact_id,'conclusion':j.conclusion} for j in judgments[-3:]],
        'self_report_ids':[n.id for n in notes[-3:]],'counts':{'facts':len(facts),'self_reports':len(notes),'judgments':len(judgments)},
        'source_kind':actor.account.source_kind,'limitation':'仅本项目同来源同内容版本；推导只读取近期60条结构化事实及3条数值复核，不自动理解自述/讲解正文。'}


def fresh(session,actor,run,dataset):
    if content_hash(dataset.body)!=dataset.sha256:fail('dataset_invalid','冻结数据校验失败')
    if dataset.body.get('automation_consent'):
        from app.infrastructure.db.models import LearningAutomation
        consent=session.get(LearningAutomation,run.workspace_id)
        if not consent or not consent.enabled or consent.body.get('consent_token')!=dataset.body['automation_consent']:
            fail('automation_consent_withdrawn','自动调整授权已关闭或变更，旧候选不能调用或生效')
    current=source_snapshot(session,actor,run.workspace_id)
    if current['fingerprint']!=dataset.body['fingerprint']:fail('evolution_sources_changed','来源、课程或策略基线已变化，请重新生成')
    if current['signature']!=dataset.body['signature']:fail('evaluation_version_changed','机制样本或运行器已变化，请重新评测')


def reviews(session,rid):
    return list(session.scalars(select(ProductionReview).where(ProductionReview.target_kind=='skill_candidate',ProductionReview.target_id==rid).order_by(ProductionReview.revision,ProductionReview.id)))


def latest_admin(session,rid):return next((r for r in reversed(reviews(session,rid)) if r.decision in {'approve','reject','withdraw'}),None)


def artifact_check(candidate,evaluation):
    if not candidate or not evaluation:fail('evaluation_missing','候选或评测尚未完成')
    if content_hash(candidate.body)!=candidate.sha256 or content_hash(evaluation.body)!=evaluation.sha256:fail('artifact_invalid','候选或评测校验失败')
    if compile_policy(candidate.body['policy'])!=candidate.body['compilation']:fail('candidate_invalid','编译产物不一致')
    if evaluation.body['signature']!=signature():fail('evaluation_version_changed','评测运行器或样本已变化')
    if not evaluation.body['eligible_for_review']:fail('evaluation_blocked','工程评测未通过，不能送审或发布')


def validate_reference(session,reference,policy):
    candidate=session.get(EvolutionCandidate,reference['candidate_id']);evaluation=session.get(EvolutionEvaluation,reference['candidate_id'])
    artifact_check(candidate,evaluation)
    if reference.get('kind') == 'personal_semantic_trial':
        from app.infrastructure.db.models import SkillTrial
        trial = session.get(SkillTrial, candidate.id)
        if not trial or trial.status != 'active' or not evaluation.body.get('eligible_for_personal_trial') or candidate.body.get('generator') != 'skill-semantic.v1':
            fail('evolution_trial_unavailable', '个人开发试用已停止或未通过评测')
        if reference['report_sha256'] != evaluation.sha256 or reference['candidate_sha256'] != candidate.sha256 or policy != candidate.body['policy']:
            fail('evolution_candidate_changed', '试用策略与评测候选不一致')
        return
    review=latest_admin(session,candidate.id)
    reviewer=session.get(Account,review.reviewer_id) if review else None
    if not review or review.decision!='approve' or review.id!=reference['review_id'] or not reviewer or not reviewer.enabled or reviewer.role!='admin':
        fail('evolution_review_unavailable','开发评测复核已撤回或复核身份无效')
    if reference['report_sha256']!=evaluation.sha256 or reference['candidate_sha256']!=candidate.sha256 or policy!=candidate.body['policy']:
        fail('evolution_candidate_changed','提案与离线候选不一致')


def public(session,run,detail=True):
    space=session.get(Workspace,run.workspace_id);dataset=session.get(EvolutionDataset,run.id);candidate=session.get(EvolutionCandidate,run.id);evaluation=session.get(EvolutionEvaluation,run.id)
    from app.infrastructure.db.models import ModelOperation, SkillTrial
    op = session.scalar(select(ModelOperation).where(ModelOperation.run_id == run.id).order_by(ModelOperation.created_at.desc()).limit(1))
    trial = session.get(SkillTrial, run.id)
    return {'generation': {'operation_id': op.id, 'status': op.status, 'usage': op.usage, 'error_code': op.error_code} if op else None,
        'trial': {'status': trial.status, **trial.body} if trial else None,
        'run_id':run.id,'workspace_id':run.workspace_id,'workspace_title':space.title,'workspace_status':space.status,'status':run.status,
        'reason':(run.view or {}).get('reason'),'phase':(run.view or {}).get('phase','queued'),'revision':run.revision,'error_code':run.error_code,'created_at':run.created_at,
        'dataset':dataset.body if dataset and detail else None,'candidate':candidate.body if candidate and detail else None,'evaluation':evaluation.body if evaluation and detail else None,
        'report_sha256':evaluation.sha256 if evaluation else None,'reviews':[{'action':r.decision,'note':r.note,'revision':r.revision,'created_at':r.created_at} for r in reviews(session,run.id)],
        'strategy_run_id':(run.view or {}).get('strategy_run_id'),'automatic_publish':False}


def start(session,actor,wid,key,mode='offline',allow_personal_trial=False,*,automation_consent=None):
    spaces._student(actor);spaces.owned(session,actor,wid)
    old=session.scalar(select(AgentRun).where(AgentRun.workspace_id==wid,AgentRun.creation_key=='evolution:'+key))
    if old:
        if old.kind!='skill_evolution':fail('idempotency_conflict','标识已用于其他请求')
        if old.creation_hash != content_hash({'mode':mode, **({'allow_personal_trial':allow_personal_trial} if mode=='semantic' else {})}):fail('idempotency_conflict','同一标识不能改变改进方式')
        return public(session,old)
    snapshot=source_snapshot(session,actor,wid)
    if automation_consent:snapshot['automation_consent']=automation_consent
    if mode == 'semantic':
        from app.modules.learning.activity_service import require_idle
        from app.modules.learning.model_coaching import redact
        require_idle(session, wid, ['tutor', 'teach', 'roundtable'])
        entries = session.execute(select(LearningFact, LearningInput).join(LearningInput, LearningInput.id==LearningFact.input_id)
            .where(LearningFact.workspace_id==wid, LearningFact.source_kind==actor.account.source_kind,
                LearningFact.content_version==snapshot['content_version'])
            .order_by(LearningFact.created_at.desc(), LearningFact.id.desc()).limit(6)).all()
        if not entries: fail('evolution_evidence_required','请先在当前课程留下学习记录')
        snapshot['semantic_evidence'] = [{'id':f.id,'kind':f.kind,'status':f.payload.get('status'),
            'text':redact(str(e.payload.get('reasoning') or e.payload.get('reflection') or e.payload.get('answer') or e.payload.get('coaching_text') or ''),actor.account)[:300]} for f,e in reversed(entries)]
        snapshot['allow_personal_trial'] = allow_personal_trial
    if session.scalar(select(AgentRun.id).where(AgentRun.workspace_id==wid,AgentRun.kind=='skill_evolution',AgentRun.status.in_(['queued','awaiting_input']))):fail('evolution_pending','本项目已有待处理改进任务，请继续或取消')
    convo=spaces.create_conversation(session,actor,wid,'tutor')
    run=AgentRun(workspace_id=wid,conversation_id=convo.id,creation_key='evolution:'+key,creation_hash=content_hash({'mode':mode, **({'allow_personal_trial':allow_personal_trial} if mode=='semantic' else {})}),kind='skill_evolution',version=VERSION)
    session.add(run);session.flush()
    session.add(EvolutionDataset(id=run.id,workspace_id=wid,body=snapshot,sha256=content_hash(snapshot)));session.flush()
    entry = Planning().enqueue(session,run,actor.account.id,'start:'+run.id,{'operation':'evolution_semantic_start' if mode=='semantic' else 'evolution_start','dataset_id':run.id})
    if mode == 'semantic':
        from app.infrastructure.runtime.model_tasks import reserve
        reserve(session, actor.account, run, entry, '', evolution_dataset=snapshot)
    return public(session,run)


def process(session,run,entry,account,workspace):
    try:
        if not account.enabled:fail('account_disabled','账号已停用')
        if run.version!=VERSION:fail('version_conflict','进化图版本不一致')
        dataset=session.get(EvolutionDataset,run.id);fresh(session,SimpleNamespace(account=account),run,dataset)
        if entry.payload['operation'] == 'evolution_semantic_start':
            from app.infrastructure.runtime.model_tasks import activate
            run.view = {'phase':'model_pending', 'reason':'正在分析至多六条本项目脱敏记录；没有自动教学效果结论'}
            run.status='awaiting_input';run.revision+=1
            activate(session,run,entry);event(session,run,'evolution.model_pending')
            return
        from app.modules.skill_evolution.graph import evolve
        state=evolve(dataset.body)
        if state.get('candidate'):
            candidate=state['candidate'];evaluation=state['evaluation']
            session.add(EvolutionCandidate(id=run.id,dataset_id=run.id,parent_strategy_id=dataset.body['baseline']['strategy_id'],body=candidate,sha256=content_hash(candidate)));session.flush()
            session.add(EvolutionEvaluation(id=run.id,body=evaluation,sha256=content_hash(evaluation)))
            phase='awaiting_review' if evaluation['eligible_for_review'] else 'blocked'
            run.status='awaiting_input' if phase=='awaiting_review' else 'completed'
        else:phase='no_candidate';run.status='completed'
        run.view={'phase':phase,'reason':state['reason']}
    except DomainError as error:run.status,run.error_code,run.view='failed',error.code,{'phase':'failed'}
    run.gate_id=None;run.revision+=1;event(session,run,'evolution.evaluated')


def operation(session,actor,run,key,body):
    token='evo:'+actor.account.id+':'+key
    old=session.scalar(select(LearningInput).where(LearningInput.conversation_id==run.conversation_id,LearningInput.idempotency_key==token))
    payload={**body,'actor_id':actor.account.id}
    if old:
        if old.payload!=payload:fail('idempotency_conflict','同一标识不能用于不同决定')
        return True
    session.add(LearningInput(workspace_id=run.workspace_id,conversation_id=run.conversation_id,account_id=actor.account.id,idempotency_key=token,request_hash=content_hash(payload),source_kind=session.get(Account,session.get(Workspace,run.workspace_id).owner_id).source_kind,payload=payload))
    return False


def decide(session,actor,run,body,key):
    if actor.account.role!='admin':raise DomainError(403,'forbidden','仅管理员可复核')
    if operation(session,actor,run,key,body):return public(session,run)
    if run.revision!=body['expected_revision']:fail('revision_conflict','候选状态已变化')
    evaluation=session.get(EvolutionEvaluation,run.id);candidate=session.get(EvolutionCandidate,run.id)
    if not evaluation or evaluation.sha256!=body['report_sha256']:fail('report_changed','请刷新并核对评测报告')
    action=body['action'];phase=(run.view or {}).get('phase')
    if action=='withdraw':
        previous=latest_admin(session,run.id)
        if not previous or previous.decision!='approve':fail('invalid_phase','只有已复核候选可撤回')
    else:
        if session.get(Workspace,run.workspace_id).status!='active':fail('workspace_archived','归档项目不能复核新候选')
        if phase not in {'awaiting_review','approved_for_proposal','blocked'}:fail('invalid_phase','此候选不能再次复核')
    if action=='approve':
        artifact_check(candidate,evaluation)
        owner=session.get(Account,session.get(Workspace,run.workspace_id).owner_id)
        fresh(session,SimpleNamespace(account=owner),run,session.get(EvolutionDataset,run.id))
    run.revision+=1
    session.add(ProductionReview(target_kind='skill_candidate',target_id=run.id,revision=run.revision,decision=action,note=body['note'],reviewer_id=actor.account.id))
    run.view={**(run.view or {}),'phase':{'approve':'approved_for_proposal','reject':'rejected','withdraw':'withdrawn'}[action]}
    run.status='awaiting_input' if action=='approve' else 'completed';session.flush();event(session,run,'evolution.reviewed')
    return public(session,run)


def propose(session,actor,run,body,key):
    if operation(session,actor,run,key,{**body,'action':'propose'}):return public(session,run)
    if run.revision!=body['expected_revision']:fail('revision_conflict','候选状态已变化')
    if (run.view or {}).get('phase')!='approved_for_proposal':fail('review_required','请先由管理员复核离线评测')
    dataset=session.get(EvolutionDataset,run.id);candidate=session.get(EvolutionCandidate,run.id);evaluation=session.get(EvolutionEvaluation,run.id)
    if not evaluation or evaluation.sha256!=body['report_sha256']:fail('report_changed','评测报告不一致')
    fresh(session,actor,run,dataset);review=latest_admin(session,run.id)
    reference={'candidate_id':run.id,'candidate_sha256':candidate.sha256,'report_sha256':evaluation.sha256,'review_id':review.id}
    validate_reference(session,reference,candidate.body['policy'])
    parent=session.get(StrategyVersion,candidate.parent_strategy_id)
    created=Planning().start(session,actor,run.workspace_id,{**candidate.body['policy'],'learning_experience':body['note'],
        'material_ids':[m['material_id'] for m in parent.bundle['materials']]},'evolution:'+run.id,evolution_reference=reference)
    run.revision+=1;run.status='completed';run.view={**run.view,'phase':'strategy_proposed','strategy_run_id':created['run_id']}
    session.add(ProductionReview(target_kind='skill_candidate',target_id=run.id,revision=run.revision,decision='propose',note=body['note'],reviewer_id=actor.account.id))
    event(session,run,'evolution.proposed');session.flush();return public(session,run)
