"""Offline evolution is traceable engineering evidence, never automatic promotion."""
import json
from copy import deepcopy
from sqlalchemy import select,func,update
from sqlalchemy.exc import DBAPIError
import pytest
from app.infrastructure.db.models import AgentRun,ActiveStrategy,StrategyVersion,LearningFact,EvolutionDataset,EvolutionCandidate,EvolutionEvaluation,ModelOperation,ProductionReview
from app.modules.planning.contracts import ExecutionPolicy
from app.modules.skill_evolution.evaluator import evaluate
from app.worker import Worker
from tests.test_s02 import database,env,recovery_database
from tests.test_g07 import activate
from tests.test_s09 import decide


def ready(env):
    from tests.test_s06 import make_card
    client,wid,path,card=make_card(env,'project')
    run=client.get(path).json();assert client.post(path+'/cancel',json={'expected_revision':run['revision']}).status_code==200
    bound=client.post(f'/api/v1/student/workspaces/{wid}/course',json={'topic':'等式性质与检查','title':'方程开发课程','unit_ids':['math.g7.equations.dev1'],'active_unit_id':'math.g7.equations.dev1','expected_revision':0})
    assert bound.status_code==200,bound.text
    _,parent=activate(env,client,wid,ExecutionPolicy().model_dump())
    return client,wid,parent


def start(env,client,wid,key='evolve',work=True):
    root=f'/api/v1/student/workspaces/{wid}/skill-evolutions'
    response=client.post(root,headers={'Idempotency-Key':key},json={'mode':'offline'})
    assert response.status_code==202,response.text
    path=root+'/'+response.json()['run_id']
    if work:assert Worker(env.db,enable_models=False).step()
    return path,client.get(path).json()


def review(env,run,action='approve',key=None):
    return env.admin.post('/api/v1/admin/skill-evolutions/'+run['run_id']+'/decision',headers={'Idempotency-Key':key or action},json={
        'expected_revision':run['revision'],'action':action,'report_sha256':run['report_sha256'],'note':'核对冻结来源与工程样本，仍不宣称真实学习效果'})


def propose(client,path,run,key='propose'):
    return client.post(path+'/proposal',headers={'Idempotency-Key':key},json={'expected_revision':run['revision'],'report_sha256':run['report_sha256'],'note':'我愿意把此假设作为下一轮待审策略，与当前学习情况比较'})


def test_evaluator_uses_disjoint_frozen_samples_and_observes_real_changes():
    base={'daily_minutes':20,'reflection_focus':'basis','execution':ExecutionPolicy().model_dump()}
    candidate=deepcopy(base);candidate['execution']['practice_order']='errors_first'
    report=evaluate(base,candidate)
    assert report['eligible_for_review'] and report['automatic_publish'] is False and report['learning_gain'] is None
    development={r['family'] for r in report['cases'] if r['split']=='development'}
    holdout={r['family'] for r in report['cases'] if r['split']=='heldout'}
    assert not development&holdout and len(report['cases'])==4
    assert all(r['reference_check'] and r['passed'] and r['behavior_changed'] for r in report['cases'])
    assert not evaluate(base,base)['eligible_for_review']


def test_sources_candidate_evaluation_review_and_dual_gate_activation(env):
    client,wid,parent=ready(env)
    path,run=start(env,client,wid)
    assert run['phase']=='awaiting_review',run
    assert run['candidate']['policy']['execution']['practice_order']=='errors_first'
    assert run['candidate']['hypothesis']['source_ids']
    assert run['dataset']['baseline']['strategy_id']==parent['run_id']
    assert run['evaluation']['eligible_for_review'] and not run['automatic_publish']
    assert client.post(path.rsplit('/',1)[0],headers={'Idempotency-Key':'evolve'},json={'mode':'offline'}).json()['run_id']==run['run_id']
    assert propose(client,path,run).status_code==409
    approved=review(env,run);assert approved.status_code==200,approved.text
    assert review(env,run).status_code==200 # identical receipt replay
    approved=approved.json()
    response=propose(client,path,approved);assert response.status_code==202,response.text
    assert propose(client,path,approved).status_code==202
    created=response.json();assert created['phase']=='strategy_proposed'
    strategy=f'/api/v1/student/workspaces/{wid}/strategies/'+created['strategy_run_id']
    assert Worker(env.db,enable_models=False).step()
    planned=client.get(strategy).json()
    assert planned['bundle']['evolution_reference']['candidate_id']==run['run_id']
    assert client.get(f'/api/v1/student/workspaces/{wid}/active-strategy').json()['version_id']==parent['run_id']
    decide(env,client,strategy,'approve',True);final=decide(env,client,strategy,'confirm')
    assert client.get(f'/api/v1/student/workspaces/{wid}/active-strategy').json()['version_id']==final['run_id']
    with env.db.transaction() as session:assert session.scalar(select(func.count()).select_from(ModelOperation))==0
    # Withdrawal is also effective after conversion/activation, without deleting history.
    withdrawn=review(env,client.get(path).json(),'withdraw');assert withdrawn.status_code==200,withdrawn.text
    active=client.get(f'/api/v1/student/workspaces/{wid}/active-strategy').json()
    assert not active['effective'] and active['version_id']==final['run_id'] and active['unavailable_reason']=='evolution_review_unavailable'


def test_isolation_temporary_reject_cancel_and_immutable_artifacts(env):
    client,wid,_=ready(env);path,run=start(env,client,wid)
    other,_=env.student('evo.other')
    assert other.get(path).status_code==404 and client.get('/api/v1/admin/skill-evolutions').status_code==403
    assert env.admin.get(path).status_code==403
    temp=env.workspace(client,'temporary')['workspace_id']
    assert client.post(f'/api/v1/student/workspaces/{temp}/skill-evolutions',headers={'Idempotency-Key':'temp'},json={'mode':'offline'}).status_code==409
    assert client.post(path.rsplit('/',1)[0],headers={'Idempotency-Key':'live'},json={'mode':'live'}).status_code==422
    for model in (EvolutionDataset,EvolutionCandidate,EvolutionEvaluation):
        with pytest.raises(DBAPIError):
            with env.db.transaction() as session:session.execute(update(model).where(model.id==run['run_id']).values(sha256='f'*64))
    rejected=review(env,run,'reject');assert rejected.status_code==200
    assert propose(client,path,rejected.json()).status_code==409
    again,next_run=start(env,client,wid,'next',False)
    assert client.post(again+'/cancel',json={'expected_revision':next_run['revision']}).status_code==200
    assert Worker(env.db,enable_models=False).step()
    assert client.get(again).json()['candidate'] is None


def test_new_evidence_stales_evaluation_and_blocks_conversion(env):
    client,wid,_=ready(env);path,run=start(env,client,wid)
    fid=run['candidate']['hypothesis']['source_ids'][0]
    assert client.post(f'/api/v1/student/workspaces/{wid}/memory-notes',headers={'Idempotency-Key':'later-note'},json={'fact_id':fid,'text':'补充体验需要纳入下一轮冻结来源'}).status_code==201
    rejected=review(env,run)
    assert rejected.status_code==409 and rejected.json()['error']['code']=='evolution_sources_changed'


def test_no_signal_does_not_fabricate_a_candidate(env):
    client,wid,_=ready(env)
    # Baseline already consumes both error hypotheses; no correction or two-help event.
    pointer=client.get(f'/api/v1/student/workspaces/{wid}/active-strategy').json()
    assert client.post(f'/api/v1/student/workspaces/{wid}/active-strategy/rollback',headers={'Idempotency-Key':'defaults'},json={'expected_revision':pointer['revision'],'target_version':None,'note':'先验证缺少执行基线时拒绝生成'}).status_code==200
    assert client.post(f'/api/v1/student/workspaces/{wid}/skill-evolutions',headers={'Idempotency-Key':'no-base'},json={'mode':'offline'}).status_code==409
    from app.modules.skill_evolution.graph import evolve
    baseline={'daily_minutes':20,'reflection_focus':'basis','execution':ExecutionPolicy().model_dump()}
    result=evolve({'baseline':{'policy':baseline},'facts':[],'judgments':[]})
    assert result['candidate'] is None and result.get('evaluation') is None


def test_restore_keeps_report_and_guard_provenance(env,tmp_path):
    from app.infrastructure.db.maintenance import backup,restore
    client,wid,_=ready(env);path,run=start(env,client,wid)
    destination=tmp_path/'evolution-backup.json';backup(env.db,destination)
    with recovery_database(env.db,tmp_path) as recovered:
        restore(recovered,destination)
        with recovered.transaction() as session:
            assert session.get(EvolutionDataset,run['run_id']) is not None
            assert session.get(EvolutionEvaluation,run['run_id']).sha256==run['report_sha256']


def test_blocked_report_and_changed_engine_cannot_be_approved(env,monkeypatch):
    client,wid,_=ready(env)
    from app.modules.skill_evolution import graph
    original=graph.evaluate
    def blocked(base,candidate):
        report=original(base,candidate);report['eligible_for_review']=False;return report
    monkeypatch.setattr(graph,'evaluate',blocked)
    path,run=start(env,client,wid)
    assert run['phase']=='blocked' and review(env,run).status_code==409
    assert propose(client,path,run).status_code==409
    assert review(env,run,'reject').status_code==200
    monkeypatch.setattr(graph,'evaluate',original)
    path,run=start(env,client,wid,'new-signature')
    import app.modules.skill_evolution.service as service
    old=service.signature
    monkeypatch.setattr(service,'signature',lambda:{**old(),'engine':'changed-engine'})
    response=review(env,run)
    assert response.status_code==409 and response.json()['error']['code']=='evaluation_version_changed'


def test_admin_raw_source_is_limited_to_dataset_and_audited(env):
    client,wid,_=ready(env);path,run=start(env,client,wid)
    source=run['candidate']['hypothesis']['source_ids'][0]
    route='/api/v1/admin/skill-evolutions/'+run['run_id']+'/facts/'
    assert client.get(route+source).status_code==403
    response=env.admin.get(route+source)
    assert response.status_code==200 and response.json()['fact_id']==source
    from uuid import uuid4
    assert env.admin.get(route+str(uuid4())).status_code==404
    from app.infrastructure.db.models import AuditEvent
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action=='evolution.source_read'))==1
