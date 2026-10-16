from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.infrastructure.db.models import AgentRun, LearningFact, LearningInput, MemoryNote, new_id, now
from app.modules.evidence.service import day
from tests.test_s02 import database, env
from tests.test_s05 import setup, send, to_practice


def dataset(env,kind='project'):
    client,wid,path=setup(env,kind)
    to_practice(env,client,path)
    send(env,client,path,'hint')
    send(env,client,path,'answer',answer='6')
    send(env,client,path,'reflect',reflection='等式两边做同样的运算')
    send(env,client,path,'answer',answer='7')
    send(env,client,path,'reflect',reflection='包装费只计一次，再代入检查')
    send(env,client,path,'answer',answer='5')
    send(env,client,path,'next')
    send(env,client,path,'answer',answer='6')
    send(env,client,path,'next')
    return client,wid,path


def report(client,wid=None,params=None):
    path=f'/api/v1/student/workspaces/{wid}/evidence' if wid else '/api/v1/student/evidence'
    result=client.get(path,params=params or {})
    assert result.status_code==200,result.text
    return result.json()


def test_counts_reconcile_with_sources_and_stage_boundaries(env):
    client,wid,_=dataset(env)
    data=report(client,wid)
    assert data['groups']==[{'source_kind':'synthetic','facts':8,'answers':5,'checkpoint_answers':2,
        'checkpoint_passed':2,'hints':1,'reflections':2,'activity_contributions':0,'self_reports':0,'human_judgments':0}]
    assert sum(row['answers'] for row in data['daily'])==5
    assert len(data['records'])==8 and data['record_count']==8
    assert len(data['competencies'])==6 and all(row['score'] is None for row in data['competencies'])
    assert data['model_estimates']==0
    for row in data['records']:
        fact=client.get(f'/api/v1/student/workspaces/{wid}/facts/'+row['fact_id']).json()
        assert fact['status']==row['status'] and fact['task']['task_id']==row['task_id']
    checkpoint=report(client,wid,{'record_kind':'checkpoint'})
    assert len(checkpoint['records'])==2 and checkpoint['groups']==data['groups']
    assert all(row['stage']=='checkpoint' for row in checkpoint['records'])


def test_global_all_projects_including_archived_excludes_temporary_and_other_owner(env):
    client,wid,_=dataset(env)
    temporary=env.workspace(client,'temporary')['workspace_id']
    other,_=env.student('s08.other')
    assert other.get(f'/api/v1/student/workspaces/{wid}/evidence').status_code==404
    assert other.get(f'/api/v1/student/workspaces/{wid}/facts/'+report(client,wid)['records'][0]['fact_id']).status_code==404
    assert not report(other)['records']
    assert client.post(f'/api/v1/student/workspaces/{wid}/archive',json={'expected_revision':1}).status_code==200
    data=report(client)
    assert data['workspace_count']==1 and len(data['records'])==8
    assert all(row['archived'] for row in data['records'])
    assert report(client,temporary)['workspace_count']==1
    assert report(client,temporary)['groups']==[]
    assert env.admin.get('/api/v1/student/evidence').status_code==403
    assert client.get('/api/v1/student/evidence?days=15').status_code==422
    assert client.get('/api/v1/student/evidence?source=legacy_unknown').status_code==422


def test_activity_not_counted_as_independent_and_subjective_judgment_separate(env):
    from tests.test_s07 import start,send as activity_send
    client,wid,path=start(env,'teach','project')
    activity_send(env,client,path)
    activity_send(env,client,path,'conclude','看不出数值')
    original=report(client,wid)
    assert original['groups'][0]['activity_contributions']==2 and original['groups'][0]['answers']==0
    assert not original['stages']
    fact_id=original['records'][0]['fact_id']
    assert client.post(f'/api/v1/student/workspaces/{wid}/memory-notes',headers={'Idempotency-Key':'note'},
        json={'fact_id':fact_id,'text':'我当时有点紧张，先保留我的感受'}).status_code==201
    review=env.admin.get('/api/v1/admin/answer-reviews').json()['items'][0]
    assert env.admin.post('/api/v1/admin/answer-reviews/'+review['review_id']+'/resolve',json={
        'expected_revision':1,'conclusion':'unresolved','note':'数值表达不足，不能判断推理质量'}).status_code==200
    data=report(client,wid)
    assert data['groups'][0]['self_reports']==1 and data['groups'][0]['human_judgments']==1
    assert data['record_count']==4 and data['groups'][0]['facts']==2
    subjective=report(client,wid,{'record_kind':'self_report'})
    assert subjective['record_count']==1 and subjective['records'][0]['excerpt']=='我当时有点紧张，先保留我的感受'
    assert data['groups'][0]['checkpoint_passed']==0


def seed(env,client,wid,quantity=45):
    # 批量来源用于验证分页/时间边界；合法外键，不触碰不可变历史。
    with env.db.transaction() as session:
        actor=env.actor(session,client)
        conversation=env.spaces.create_conversation(session,actor,wid,'tutor')
        run=AgentRun(workspace_id=wid,conversation_id=conversation.id,creation_key=new_id(),creation_hash='0'*64,
            version='test-read-model',kind='tutor',status='completed')
        session.add(run);session.flush()
        ids=[]
        for index in range(quantity):
            chosen_source='synthetic' if index%2==0 else 'adult_test'
            entry=LearningInput(workspace_id=wid,conversation_id=conversation.id,account_id=actor.account.id,
                idempotency_key=new_id(),request_hash='0'*64,source_kind=chosen_source,payload={'answer':'3'})
            session.add(entry);session.flush()
            fact=LearningFact(workspace_id=wid,run_id=run.id,input_id=entry.id,kind='answer',source_kind=chosen_source,
                task_id='D-EQ-01',content_version='0'*64,payload={'stage':'diagnostic','status':'passed','hints_used':0,
                    'task_snapshot':{'id':'D-EQ-01','stem':'test','format':'number'}},created_at=now()-index)
            session.add(fact);session.flush();ids.append(fact.id)
        return ids


def test_global_totals_do_not_depend_on_twenty_item_page_and_source_filter(env):
    client,_=env.student()
    wid=env.workspace(client)['workspace_id']
    seed(env,client,wid)
    for index in range(23): env.workspace(client,title=f"empty {index}")
    first=report(client)
    second=report(client,params={'offset':20})
    third=report(client,params={'offset':40})
    assert first['workspace_count']==24
    assert first['record_count']==45 and len(first['records'])==20 and len(third['records'])==5
    assert first['groups']==second['groups']==third['groups']
    assert len({row['record_id'] for part in [first,second,third] for row in part['records']})==45
    adult=report(client,params={'source':'adult_test'})
    assert adult['record_count']==22 and adult['groups'][0]['source_kind']=='adult_test'
    assert adult['groups'][0]['facts']==22 and all(row['source_kind']=='adult_test' for row in adult['records'])


def test_calendar_boundary_and_new_self_report_on_old_fact(env,monkeypatch):
    client,_=env.student()
    wid=env.workspace(client)['workspace_id']
    with env.db.transaction() as session:
        actor=env.actor(session,client)
        conversation=env.spaces.create_conversation(session,actor,wid,'tutor')
        run=AgentRun(workspace_id=wid,conversation_id=conversation.id,creation_key=new_id(),creation_hash='0'*64,
            version='test-date',status='completed')
        session.add(run);session.flush()
        stamp=int(datetime(2026,10,16,12,tzinfo=timezone(timedelta(hours=8))).timestamp())
        begin=int(datetime(2026,10,3,tzinfo=timezone(timedelta(hours=8))).timestamp())
        ids=[]
        for at in [begin-1,begin,stamp+1]:
            entry=LearningInput(workspace_id=wid,conversation_id=conversation.id,account_id=actor.account.id,
                idempotency_key=new_id(),request_hash='0'*64,source_kind='synthetic',payload={})
            session.add(entry);session.flush()
            fact=LearningFact(workspace_id=wid,run_id=run.id,input_id=entry.id,kind='reflection',source_kind='synthetic',
                task_id='D-EQ-01',content_version='0'*64,payload={'stage':'practice','status':None,'hints_used':0},created_at=at)
            session.add(fact);session.flush();ids.append(fact.id)
        session.add(MemoryNote(workspace_id=wid,fact_id=ids[0],actor_id=actor.account.id,
            idempotency_key='recent-note',request_hash='0'*64,text='对旧记录补充新说明',created_at=stamp))
    monkeypatch.setattr('app.modules.evidence.service.now',lambda:stamp)
    data=report(client,wid,{'days':14})
    assert data['starts_at']==begin and data['groups'][0]['facts']==1
    assert data['groups'][0]['self_reports']==1 and data['record_count']==2
    assert {row['day'] for row in data['daily']}=={'2026-10-03','2026-10-16'}
    assert day(begin-1)=='2026-10-02'
