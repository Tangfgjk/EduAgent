"""Personal development trials. Engineering eligibility never proves efficacy."""
from types import SimpleNamespace
from sqlalchemy import select
from app.core.rules import registry_hash
from app.infrastructure.db.models import (Account, Workspace, AgentRun, EvolutionDataset, EvolutionCandidate,
    EvolutionEvaluation, SkillTrial, StrategyVersion, ActiveStrategy, StrategyActivation, LearningFact, AuditEvent, now)
from app.modules.learning.content_repository import content_hash
from app.modules.skill_evolution.evaluator import signature
from app.modules.errors import DomainError


def accept(session, op):
    from app.modules.skill_evolution.service import fresh
    from app.modules.skill_evolution.semantic import assess
    from app.modules.planning.service import evidence_snapshot
    run = session.get(AgentRun, op.run_id)
    dataset = session.get(EvolutionDataset, run.id)
    actor = SimpleNamespace(account=session.get(Account, op.account_id))
    fresh(session, actor, run, dataset)
    candidate = op.result
    report = assess(dataset.body['baseline']['policy'], candidate)
    c = EvolutionCandidate(id=run.id, dataset_id=run.id, parent_strategy_id=dataset.body['baseline']['strategy_id'],
        body=candidate, sha256=content_hash(candidate))
    session.add(c); session.flush()
    evaluation = EvolutionEvaluation(id=run.id, body=report, sha256=content_hash(report))
    session.add(evaluation); session.flush()
    phase = 'evaluated' if report['eligible_for_personal_trial'] else 'blocked'
    if report['eligible_for_personal_trial'] and dataset.body.get('allow_personal_trial'):
        parent = session.get(StrategyVersion, c.parent_strategy_id)
        pointer = session.get(ActiveStrategy, run.workspace_id, with_for_update=True)
        if not pointer or pointer.version_id != parent.id:
            raise DomainError(409, 'strategy_base_changed', '策略基线已改变，不能覆盖新版本')
        # Preserve signed reference/compiled policy and append an explicit trial receipt.
        bundle = {**parent.bundle, 'learning_goal': dataset.body.get('learning_goal'), 'evidence': evidence_snapshot(session, run.workspace_id),
            'created_at': now(), 'base_version_id': parent.id, 'student_experience': '学生同意的个人开发试用，教学效果待验证',
            'skill_compilation': candidate['compilation'], 'policy_base': parent.policy, 'policy_changes': candidate['diff'],
            'evolution_reference': {'kind':'personal_semantic_trial','candidate_id':run.id,
                'candidate_sha256':c.sha256,'report_sha256':evaluation.sha256},
            'release_decision': {'kind':'system_auto','policy_version':'semantic-personal-trial.v1','effect_status':'not_validated'}}
        version = StrategyVersion(id=run.id, workspace_id=run.workspace_id, bundle=bundle, policy=candidate['policy'],
            checks=['受限字段与独立工程评测通过；教学效果未知；个人开发试用'], hard_rules_hash=registry_hash(),
            base_revision=pointer.revision, expires_at=now()+7*86400,
            content_hash=content_hash({'bundle':bundle,'policy':candidate['policy'],'rules':registry_hash()}))
        session.add(version); session.flush()
        trial = SkillTrial(id=run.id, workspace_id=run.workspace_id, baseline_id=parent.id, candidate_id=version.id,
            body={'effect_status':'not_validated','signature':signature(),'expires_at':now()+7*86400,
                'source_kind':actor.account.source_kind,'observations':0,'independent_passed':0,'independent_submissions':0,
                'stop_rules':['evaluation_signature_changed','account_or_project_unavailable','seven_day_expiry','explicit_revert','three_fresh_independent_failures'],
                'notice':'过程观察与自动技术回滚已启用，不构成教学效果证据。'})
        session.add(trial)
        session.add(StrategyActivation(workspace_id=run.workspace_id, from_version=parent.id, to_version=version.id,
            actor_id=actor.account.id, kind='activate', note='学生预先同意；语义候选通过工程评测，个人开发试用',
            idempotency_key='semantic-trial:'+run.id, request_hash=content_hash({'candidate':c.sha256,'report':evaluation.sha256})))
        pointer.version_id=version.id;pointer.revision+=1
        session.add(AuditEvent(actor_id=actor.account.id, action='skill.trial.activate', target_id=run.id))
        phase='personal_trial_active'
    run.status='completed';run.revision+=1
    run.view={'phase':phase,'reason':'模型语义候选已评测；仅受限个人开发试用，不自动全局发布，不宣称教学有效'}


def revert(session, trial, reason):
    pointer=session.get(ActiveStrategy,trial.workspace_id,with_for_update=True)
    if pointer and pointer.version_id==trial.candidate_id:
        account=session.get(Workspace,trial.workspace_id).owner_id
        session.add(StrategyActivation(workspace_id=trial.workspace_id,from_version=pointer.version_id,
            to_version=trial.baseline_id,actor_id=account,kind='rollback',note=reason,
            idempotency_key='trial-stop:'+trial.id,request_hash=content_hash({'reason':reason})))
        pointer.version_id=trial.baseline_id;pointer.revision+=1
    trial.status='reverted';trial.body={**trial.body,'stop_reason':reason,'stopped_at':now()}


def monitor(session, wid):
    for trial in session.scalars(select(SkillTrial).where(SkillTrial.workspace_id==wid,SkillTrial.status=='active').with_for_update()):
        space=session.get(Workspace,wid);account=session.get(Account,space.owner_id)
        pointer=session.get(ActiveStrategy,wid)
        reason=('evaluation_signature_changed' if trial.body['signature']!=signature() else
            'account_or_project_unavailable' if not account.enabled or space.status!='active' or account.source_kind!=trial.body['source_kind'] else
            'seven_day_expiry' if now()>=trial.body['expires_at'] else
            'strategy_replaced' if not pointer or pointer.version_id!=trial.candidate_id else None)
        if reason:
            revert(session,trial,reason);continue
        facts=list(session.scalars(select(LearningFact).where(LearningFact.workspace_id==wid,
            LearningFact.source_kind==trial.body['source_kind'],LearningFact.payload['strategy_version_id'].as_string()==trial.candidate_id)))
        independent=sorted([f for f in facts if f.kind=='answer' and f.payload.get('stage')=='checkpoint'
            and f.payload.get('assessment_exposure')=='fresh' and f.payload.get('hints_used',0)==0],key=lambda f:(f.created_at,f.id))
        trial.body={**trial.body,'observations':len(facts),'independent_submissions':len(independent),
            'independent_passed':sum(f.payload.get('status')=='passed' for f in independent),
            'effect_status':'not_validated'}
        # An explicitly recorded conservative safety stop, not a causal efficacy
        # conclusion. Never retroactively change stopping rules for old trials.
        if 'three_fresh_independent_failures' in trial.body.get('stop_rules',[]) and len(independent)>=3 and all(f.payload.get('status')=='failed' for f in independent[-3:]):
            revert(session,trial,'three_fresh_independent_failures')


def withdraw(session, actor, wid, rid):
    from app.modules.workspaces.service import Workspaces
    Workspaces().owned(session,actor,wid,active=True)
    trial=session.get(SkillTrial,rid,with_for_update=True)
    if not trial or trial.workspace_id!=wid:raise DomainError(404,'not_found','未找到个人试用')
    if trial.status=='active':revert(session,trial,'explicit_revert')
    return {'status':trial.status,'effect_status':'not_validated'}
