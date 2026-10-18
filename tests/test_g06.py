"""G06 observation semantics, ownership, provenance and local-only planning snapshot."""
import json
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.infrastructure.db.models import LearningFact, MemoryNote, ModelOperation, now
from app.modules.evidence.insights import measure
from tests.test_s02 import database, env
from tests.test_s08 import dataset, seed


def insights(client,wid=None,**params):
    route=f'/api/v1/student/workspaces/{wid}/learning-insights' if wid else '/api/v1/student/learning-insights'
    response=client.get(route,params=params)
    assert response.status_code==200,response.text
    return response.json()


def test_real_facts_map_to_known_versions_and_separate_assistance(env):
    client,wid,_=dataset(env)
    data=insights(client,wid)
    assert data['facts_count']==8
    metrics={row['metric']:row for row in data['measures']}
    assert metrics['diagnostic']['submissions']==1
    assert metrics['practice_assisted']['submissions']==1
    assert metrics['practice_unassisted']['submissions']==1
    assert metrics['assessment_first']['submissions']==2
    assert metrics['assessment_first']['passed']==2
    observed=[n for n in data['knowledge_nodes'] if n['facts']]
    assert len(observed)==1 and observed[0]['kc_id']=='MATH.G7.EQUATION'
    assert observed[0]['facts']==8 and observed[0]['task_count']==5
    assert len(data['competencies'])==6 and all(d['score'] is None and d['reviewed_count']==0 for d in data['competencies'])
    assert next(d for d in data['competencies'] if d['name']=='逻辑推理')['candidate_count']==3
    assert all(not row['ability_judgment'] for row in data['support_events'])
    assert any(row['hints_used']==1 for row in data['support_events'])
    for ref in observed[0]['refs']:
        assert client.get(f'/api/v1/student/workspaces/{wid}/facts/'+ref['fact_id']).status_code==200
        assert not {'answer','reasoning','revealed_hint','task_snapshot'} & ref.keys()
    assert data['planning_summary']['delivery']=='local_only'
    assert insights(client,wid)['planning_summary']['fingerprint']==data['planning_summary']['fingerprint']


def test_totals_not_limited_by_refs_and_filters_align_with_legacy(env):
    client,_=env.student()
    wid=env.workspace(client)['workspace_id']
    seed(env,client,wid,45)
    temp=env.workspace(client,'temporary')['workspace_id']
    seed(env,client,temp,3)
    data=insights(client)
    assert data['facts_count']==45 and data['workspace_count']==1
    assert sum(row['submissions'] for row in data['measures'])==45
    assert all(len(row['refs'])<=8 for row in data['measures'])
    assert all(node['mapping_status']=='unmapped' for node in data['knowledge_nodes'] if node['facts'])
    adult=insights(client,source='adult_test')
    assert adult['facts_count']==22 and all(row['source_kind']=='adult_test' for row in adult['measures'])
    assert insights(client,temp)['facts_count']==3
    assert client.post(f'/api/v1/student/workspaces/{wid}/archive',json={'expected_revision':1}).status_code==200
    assert insights(client)['facts_count']==45
    assert all(r['archived'] for n in insights(client)['knowledge_nodes'] for r in n['refs'])
    assert insights(client,include_archived=False)['facts_count']==0
    assert client.get('/api/v1/student/evidence',params={'include_archived':False}).json()['record_count']==0
    other,_=env.student('g06.other')
    assert other.get(f'/api/v1/student/workspaces/{wid}/learning-insights').status_code==404
    assert insights(other)['facts_count']==0
    assert env.admin.get('/api/v1/student/learning-insights').status_code==403
    assert client.get('/api/v1/student/learning-insights?days=15').status_code==422
    assert client.get('/api/v1/student/learning-insights?source=unknown').status_code==422


def test_exposure_and_recall_boundaries_are_not_ability_claims():
    def fact(kind,**payload):return SimpleNamespace(kind=kind,payload=payload)
    assert measure(fact('answer',stage='checkpoint',assessment_exposure='fresh'))=='assessment_first'
    assert measure(fact('answer',stage='checkpoint',assessment_exposure='seen_before'))=='assessment_repeat'
    assert measure(fact('answer',stage='checkpoint'))=='assessment_unknown'
    assert measure(fact('activity_contribution',action='conclude',independent=False)) is None
    assert measure(fact('activity_contribution',action='verify',independent=False))=='assessment_repeat'
    assert measure(fact('activity_contribution',action='verify',independent=True))=='assessment_first'
    # Due failures still count as due attempts, although they do not advance schedule.
    attempt=SimpleNamespace(created_at=100,due_before=100,counted_for_schedule=False)
    assert measure(fact('review_answer',status='failed'),attempt)=='recall_due'
    attempt.due_before=101
    assert measure(fact('review_answer'),attempt)=='recall_early'
    assert measure(fact('review_answer'))=='recall_unknown'


def test_real_review_attempts_split_due_and_early(env):
    from tests.test_s06 import make_card,recall
    client,wid,_,card=make_card(env,'project')
    failed=recall(client,wid,card,'0').json()
    passed=recall(client,wid,failed['card'],'6').json()
    assert recall(client,wid,passed['card'],'6').status_code==200
    metrics={r['metric']:r for r in insights(client,wid)['measures']}
    assert metrics['recall_due']['submissions']==2 and metrics['recall_due']['passed']==1
    assert metrics['recall_early']['submissions']==1


def test_self_report_and_human_numeric_review_remain_separate(env):
    from tests.test_s05 import setup,send
    client,wid,path=setup(env,'project')
    send(env,client,path,'answer',answer='无法表达为数值')
    review=env.admin.get('/api/v1/admin/answer-reviews').json()['items'][0]
    assert env.admin.post('/api/v1/admin/answer-reviews/'+review['review_id']+'/resolve',json={
        'expected_revision':1,'conclusion':'incorrect','note':'仅数值复核，不评价素养'}).status_code==200
    data=insights(client,wid)
    fid=data['planning_summary']['references'][0]['fact_id']
    assert client.post(f'/api/v1/student/workspaces/{wid}/memory-notes',headers={'Idempotency-Key':'g06-note'},
        json={'fact_id':fid,'text':'这是我的体验，不是能力评分'}).status_code==201
    data=insights(client,wid)
    assert data['planning_summary']['counts']=={'facts':1,'self_reports':1,'human_numeric_judgments':1}
    assert data['measures'][0]['unverifiable']==1 and data['measures'][0]['passed']==0
    assert all(row['reviewed_count']==0 for row in data['competencies'])


def test_time_window_recent_note_on_old_fact_retains_provenance(env,monkeypatch):
    client,_=env.student()
    wid=env.workspace(client)['workspace_id']
    # Choose actual today's midnight to keep inserted events within fixture lifetime.
    stamp=now();begin=int((datetime.fromtimestamp(stamp,timezone(timedelta(hours=8))).replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=13)).timestamp())
    with env.db.transaction() as session:
        actor=env.actor(session,client)
        from app.infrastructure.db.models import AgentRun,LearningInput,new_id
        conversation=env.spaces.create_conversation(session,actor,wid,'tutor')
        run=AgentRun(workspace_id=wid,conversation_id=conversation.id,creation_key=new_id(),creation_hash='0'*64,version='g06-date',status='completed')
        session.add(run);session.flush()
        ids=[]
        for at in (begin-1,begin,stamp+1):
            entry=LearningInput(workspace_id=wid,conversation_id=conversation.id,account_id=actor.account.id,idempotency_key=new_id(),request_hash='0'*64,source_kind='synthetic',payload={})
            session.add(entry);session.flush()
            f=LearningFact(workspace_id=wid,run_id=run.id,input_id=entry.id,kind='reflection',source_kind='synthetic',task_id='D-EQ-01',content_version='0'*64,payload={'stage':'practice','status':None,'hints_used':0},created_at=at)
            session.add(f);session.flush();ids.append(f.id)
        session.add(MemoryNote(workspace_id=wid,fact_id=ids[0],actor_id=actor.account.id,idempotency_key='g06-old',request_hash='0'*64,text='窗口内补充旧事实',created_at=stamp))
    monkeypatch.setattr('app.modules.evidence.insights.now',lambda:stamp)
    data=insights(client,wid,days=14)
    assert data['starts_at']==begin and data['facts_count']==1
    assert data['planning_summary']['counts']['self_reports']==1
    assert {r['fact_id'] for r in data['planning_summary']['references']}==set(ids[:2])


def test_planning_freezes_local_summary_without_expanding_external_request(env):
    from tests.test_planning_model import pending,generate
    client,wid,path=pending(env)
    run=client.get(path).json()
    summary=run['bundle']['learning_observations']
    assert summary['counts']['facts']==0 and summary['delivery']=='local_only'
    with env.db.transaction() as session:
        operation=session.get(ModelOperation,run['generation']['operation_id'])
        messages=json.dumps(operation.request['generation'],ensure_ascii=False)
        assert 'learning_observations' not in messages
        assert summary['fingerprint'] not in messages
    final=generate(env,client,path)
    assert final['bundle']['learning_observations']==summary


def test_generated_content_metadata_is_resolved_by_immutable_version(env):
    from tests.test_g00_g01 import example
    from app.modules.evidence.insights import metadata
    body=example();body['knowledge_points']=['有理数运算']
    result=env.admin.post('/api/v1/admin/dynamic-content',json=body)
    assert result.status_code==201,result.text
    record=result.json()
    with env.db.transaction() as session:
        mapped=metadata(session,record['version'],{})
        assert mapped['unit_id']==body['unit_id'] and mapped['kc_id']=='MATH.G7.RATIONAL'
        assert metadata(session,'unknown-version',{})['mapping_status']=='unmapped'


def test_support_timeline_keeps_restoration_without_ability_label(env):
    from tests.test_topic_generation import prepare,confirm
    from app.infrastructure.runtime.model_tasks import ModelTaskRunner
    from app.worker import Worker
    from tests.test_s05 import send
    client,wid,root,row=prepare(env)
    queued=confirm(env,client,root,row)
    assert ModelTaskRunner(env.db).step(operation_id=queued['operation']['operation_id'])
    admin=next(r for r in env.admin.get('/api/v1/admin/course-builds').json()['items'] if r['build_id']==row['build_id'])
    approved=env.admin.post('/api/v1/admin/course-builds/'+row['build_id']+'/review',json={
        'expected_revision':admin['revision'],'approve':True,'note':'仅开发验证：核对数学条件与检测隔离后发布'}).json()
    assert client.post(root+'/'+row['build_id']+'/activate',json={'expected_revision':approved['revision'],'course_revision':0}).status_code==200
    unit=approved['unit_id']
    start=client.post(f'/api/v1/student/workspaces/{wid}/tutoring',headers={'Idempotency-Key':'support'},json={'unit_id':unit})
    assert start.status_code==202,start.text
    assert Worker(env.db,enable_models=False).step()
    path=f'/api/v1/student/workspaces/{wid}/tutoring/'+start.json()['run_id']
    from app.modules.learning.content_repository import ContentRepository
    with env.db.transaction() as session:
        tasks=ContentRepository(session,include_private=True).pack(unit).tasks
        answers=[task['answer'] for task in tasks]
    send(env,client,path,'answer',answer=answers[0]);send(env,client,path,'next')
    send(env,client,path,'answer',answer=answers[1])
    send(env,client,path,'reflect',reflection='先检查运算顺序与符号')
    send(env,client,path,'hint');send(env,client,path,'hint')
    data=insights(client,wid);events=data['support_events']
    assert any(e['mode']=='socratic_first' for e in events)
    assert any(e['mode']=='restored_step_support' and e['restorable'] for e in events)
    assert all(e['ability_judgment'] is False for e in events)
    assert any(n['kc_id']=='MATH.G7.RATIONAL' and n['facts'] for n in data['knowledge_nodes'])


def test_backfill_ignores_only_derived_exposure_but_rejects_changed_task(env,monkeypatch):
    from tests.test_s05 import setup,send
    from app.modules.memory.backfill import backfill
    from app.infrastructure.db.models import AgentRun
    from copy import deepcopy
    monkeypatch.setattr('app.worker.publish_turn',lambda *args:None)
    client,wid,path=setup(env)
    run=send(env,client,path,'answer',answer='3')
    assert backfill(env.db)['eligible']==1
    with env.db.transaction() as session:
        row=session.get(AgentRun,run['run_id']);view=deepcopy(row.view)
        view['task']['stem']='被修改的题干，不能用于猜测历史作答'
        row.view=view
    result=backfill(env.db,apply=True)
    assert result['eligible']==0 and result['incomplete']==1
