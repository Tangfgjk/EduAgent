"""真实运行/事实集成；固定模板不被冒充为模型或学生能力。"""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.infrastructure.db.models import Account, AgentCheckpoint, AgentRun, LearningFact, LearningInput
from app.modules.learning.activity_content import UNIT_ID
from app.modules.learning.activity_graph import advance_activity
from app.worker import Worker
from tests.test_s02 import database, env, recovery_database
from tests.test_s05 import setup as tutor_setup, enable as enable_tutor


def enable(env, enabled=True):
    content = env.admin.get('/api/v1/admin/activity-content').json()
    response = env.admin.post('/api/v1/admin/activity-content/development-release', json={
        'content_version': content['content_version'], 'expected_revision': content['revision'],
        'enabled': enabled, 'acknowledge_development_only': True})
    assert response.status_code == 200, response.text
    return response.json()


def start(env, kind='roundtable', workspace_kind='temporary'):
    client, _ = env.student()
    wid = env.workspace(client, workspace_kind)['workspace_id']
    enable(env)
    path = f'/api/v1/student/workspaces/{wid}/{kind}'
    response = client.post(path, headers={'Idempotency-Key':'start-activity'}, json={'unit_id':UNIT_ID})
    assert response.status_code == 202, response.text
    path += '/' + response.json()['run_id']
    assert Worker(env.db).step()
    return client, wid, path


def command(run, action='contribute', answer='6'):
    return {'action':action, 'answer':answer, 'reasoning':'等式两边同时加7，再除以2，代入原方程核验',
            'gate_id':run['gate_id'], 'expected_revision':run['revision']}


def send(env, client, path, action='contribute', answer='6'):
    run = client.get(path).json()
    response = client.post(path+'/inputs', headers={'Idempotency-Key':str(uuid4())}, json=command(run,action,answer))
    assert response.status_code == 202, response.text
    assert Worker(env.db).step()
    return client.get(path).json()


@pytest.mark.parametrize('kind', ['roundtable','teach'])
def test_end_to_end_student_first_restore_report_and_fact_separation(env, kind):
    client,wid,path = start(env,kind,'project')
    initial = client.get(path).json()
    assert initial['kind']==kind and initial['view']['phase']=='opening'
    assert not initial['view']['messages'] and 'answer' not in initial['view']['task']
    challenged = send(env,client,path)
    assert challenged['view']['phase']=='challenge'
    assert challenged['view']['messages'][0]['category']=='student_contribution'
    assert len(challenged['view']['messages'])==(4 if kind=='roundtable' else 2)
    # worker 实例更换后从数据库中的检查点继续。
    completed = send(env,client,path,'conclude')
    assert completed['status']=='completed' and completed['view']['check']['status']=='passed'
    assert completed['view']['check']['reasoning_reviewed'] is False
    assert completed['view']['check']['independent'] is False
    prefix=f'/api/v1/student/workspaces/{wid}'
    memory=client.get(prefix+'/memory').json()['items'][0]
    assert memory['digest']['rows']==[]
    claims=memory['observations']['claims']
    assert len(claims)==1 and len(claims[0]['source_refs'])==2
    for ref in claims[0]['source_refs']:
        fact=client.get(prefix+'/facts/'+ref['id']).json()
        assert fact['kind']=='activity_contribution' and fact['reasoning'] and fact['activity_version']
        assert fact['independent'] is False and fact['reasoning_reviewed'] is False
    assert client.get('/api/v1/student/memory').json()['project_count']==1
    assert client.get(path+'/event-history').status_code==200
    assert 'activity.completed' in client.get(path+'/events').text
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(LearningFact))==2
        assert session.get(AgentCheckpoint,completed['run_id'])


@pytest.mark.parametrize('kind', ['roundtable','teach'])
def test_gate_idempotency_race_and_route_kind_isolation(env, kind):
    client,wid,path=start(env,kind)
    run=client.get(path).json()
    body=command(run)
    first=client.post(path+'/inputs',headers={'Idempotency-Key':'same'},json=body)
    assert first.status_code==202
    assert client.post(path+'/inputs',headers={'Idempotency-Key':'same'},json=body).status_code==202
    assert client.post(path+'/inputs',headers={'Idempotency-Key':'same'},json={**body,'answer':'7'}).status_code==409
    assert Worker(env.db).step()
    assert client.post(path+'/inputs',headers={'Idempotency-Key':'stale'},json=body).status_code==409
    body=command(client.get(path).json(),'conclude')
    def submit(key):
        return client.post(path+'/inputs',headers={'Idempotency-Key':key},json=body).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit,['race1','race2']))==[202,409]
    assert Worker(env.db).step()
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(LearningFact))==2
    other='teach' if kind=='roundtable' else 'roundtable'
    assert client.get(path.replace('/'+kind+'/', '/'+other+'/')).status_code==404
    assert client.get(path.replace('/'+kind+'/', '/runs/')).status_code==404


def test_content_separate_approval_version_source_and_worker_recheck(env):
    client,_=env.student()
    wid=env.workspace(client)['workspace_id']
    path=f'/api/v1/student/workspaces/{wid}/roundtable'
    enable_tutor(env)
    assert client.post(path,headers={'Idempotency-Key':'blocked'},json={'unit_id':UNIT_ID}).status_code==409
    metadata=enable(env)
    assert client.get('/api/v1/student/activity-content').json()['tasks']==[]
    assert metadata['tasks'][0]['activity_templates']
    bad={ 'content_version':'0'*64,'expected_revision':metadata['revision'],'enabled':True,'acknowledge_development_only':True }
    assert env.admin.post('/api/v1/admin/activity-content/development-release',json=bad).status_code==409
    run=client.post(path,headers={'Idempotency-Key':'start'},json={'unit_id':UNIT_ID}).json()
    enable(env,False)
    assert Worker(env.db).step()
    failed=client.get(path+'/'+run['run_id']).json()
    assert failed['status']=='failed' and failed['error_code']=='content_not_enabled'
    enable(env)
    with env.db.transaction() as session:
        account=session.scalar(select(Account).where(Account.role=='student'))
        account.source_kind='real_student'
    response=client.post(path,headers={'Idempotency-Key':'real'},json={'unit_id':UNIT_ID})
    assert response.status_code==409 and response.json()['error']['code']=='development_content_only'


def test_mutual_exclusion_at_both_directions_and_cancellation(env):
    client,wid,path=tutor_setup(env)
    enable(env)
    activity=f'/api/v1/student/workspaces/{wid}/roundtable'
    assert client.post(activity,headers={'Idempotency-Key':'busy'},json={'unit_id':UNIT_ID}).json()['error']['code']=='learning_busy'
    tutor=client.get(path).json()
    assert client.post(path+'/cancel',json={'expected_revision':tutor['revision']}).status_code==200
    run=client.post(activity,headers={'Idempotency-Key':'open'},json={'unit_id':UNIT_ID}).json()
    # 排队阶段已经占用空间，尚未显示模板时也不能同时开始独立诊断。
    assert client.post(path.rsplit('/',1)[0],headers={'Idempotency-Key':'restart'},json={'unit_id':'math.g7.equations.dev1'}).status_code==409
    assert client.post(activity+'/'+run['run_id']+'/cancel',json={'expected_revision':run['revision']}).status_code==200
    assert Worker(env.db).step()  # 已取消作业不会生成角色发言或检查点。
    with env.db.transaction() as session:
        assert session.get(AgentCheckpoint,run['run_id']) is None


@pytest.mark.parametrize('kind', ['roundtable','teach'])
def test_unknown_manual_review_and_no_role_evidence(env,kind):
    client,wid,path=start(env,kind)
    send(env,client,path)
    run=send(env,client,path,'conclude','暂时不能写出数值')
    assert run['view']['check']['status']=='unverifiable'
    row=env.admin.get('/api/v1/admin/answer-reviews').json()['items'][0]
    assert row['answer']=='暂时不能写出数值' and row['reasoning']
    resolved=env.admin.post('/api/v1/admin/answer-reviews/'+row['review_id']+'/resolve',json={
        'expected_revision':1,'conclusion':'unresolved','note':'当前表达不能核定数值，解释质量不在本次复核范围内'})
    assert resolved.status_code==200
    base=f'/api/v1/student/workspaces/{wid}'
    assert not client.get(base+'/review-cards').json()['items']
    assert len(client.get(base+'/unverifiable-answers').json()['items'])==1
    assert client.get('/api/v1/student/memory').json()['project_count']==0


def test_ownership_archive_and_atomic_retry(env,monkeypatch):
    client,wid,path=start(env)
    stranger,_=env.student('other.s07')
    assert stranger.get(path).status_code==404
    assert stranger.get(path+'/events').status_code==404
    run=client.get(path).json()
    body=command(run)
    accepted=client.post(path+'/inputs',headers={'Idempotency-Key':'turn'},json=body)
    assert accepted.status_code==202
    import app.worker as worker_module
    original=worker_module.publish_activity
    monkeypatch.setattr(worker_module,'publish_activity',lambda *args: (_ for _ in ()).throw(RuntimeError('atomic rollback')))
    assert Worker(env.db).step()
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(LearningFact))==0
        stored=session.get(AgentRun,run['run_id'])
        assert stored.status=='queued' and stored.view['phase']=='opening'
    monkeypatch.setattr(worker_module,'publish_activity',original)
    from app.infrastructure.db.models import OutboxJob, now
    with env.db.transaction() as session:
        for job in session.scalars(select(OutboxJob).where(OutboxJob.status=='pending')):
            job.available_at=now()-1
    assert Worker(env.db).step()
    current=client.get(path).json()
    assert current['view']['phase']=='challenge'
    space=client.get('/api/v1/student/workspaces').json()['items'][0]
    assert client.post(f'/api/v1/student/workspaces/{wid}/archive',json={'expected_revision':space['revision']}).status_code==200
    assert client.post(path+'/inputs',headers={'Idempotency-Key':'archived'},json=command(current,'conclude')).status_code==409
    assert client.get(path).status_code==200


@pytest.mark.parametrize('kind', ['roundtable','teach'])
def test_backup_restores_pause_and_continues_with_original_sources(env,tmp_path,kind):
    from app.infrastructure.db.maintenance import backup, restore
    from app.modules.learning.activity_service import Activities
    client,wid,path=start(env,kind)
    waiting=send(env,client,path)
    destination=tmp_path/'s07.json'
    backup(env.db,destination)
    with recovery_database(env.db,tmp_path) as recovered:
        restore(recovered,destination)
        with recovered.transaction() as session:
            account=session.scalar(select(Account).where(Account.role=='student'))
            username=account.username
        from app.modules.identity.service import Identity
        identity=Identity(recovered)
        from tests.test_s02 import PASSWORD
        _,token,csrf=identity.login(username,PASSWORD,'127.0.0.1')
        with recovered.transaction() as session:
            authorized=identity.authorize(session,token,csrf,'student',True)
            Activities(kind).submit(session,authorized,wid,waiting['run_id'],command(waiting,'conclude'),'restored-turn')
        assert Worker(recovered).step()
        with recovered.transaction() as session:
            run=session.get(AgentRun,waiting['run_id'])
            assert run.status=='completed' and run.view['check']['status']=='passed'
            assert session.scalar(select(func.count()).select_from(LearningFact))==2


def test_worker_rechecks_disabled_identity(env):
    client,wid,path=start(env)
    waiting=client.get(path).json()
    assert client.post(path+'/inputs',headers={'Idempotency-Key':'queued'},json=command(waiting)).status_code==202
    with env.db.transaction() as session:
        account=session.scalar(select(Account).where(Account.role=='student'))
        account.enabled=False
    assert Worker(env.db).step()
    with env.db.transaction() as session:
        run=session.get(AgentRun,waiting['run_id'])
        assert run.status=='failed' and run.error_code=='account_disabled'
        assert session.scalar(select(func.count()).select_from(LearningFact))==0


def test_activity_templates_use_shared_governor():
    from app.modules.errors import DomainError
    from app.modules.learning.activity_graph import govern_text
    with pytest.raises(DomainError, match="互动模板未通过"):
        govern_text('test-learner','帮我作弊')
    assert govern_text('test-learner','你真聪明',feedback=True) != '你真聪明'


@pytest.mark.parametrize('kind', ['roundtable','teach'])
@pytest.mark.parametrize('answer,status',[('6','passed'),('0','failed'),('不知道','unverifiable')])
def test_graph_bounded_result_never_grades_explanation(kind,answer,status):
    rid=str(uuid4())
    state,image,paused=advance_activity(rid,'test',{'activity_kind':kind},None)
    assert paused
    state,image,paused=advance_activity(rid,'test',{'activity_kind':kind,'action':'contribute','answer':'0','reasoning':'我的第一段表达'},image)
    assert paused
    state,image,paused=advance_activity(rid,'test',{'activity_kind':kind,'action':'conclude','answer':answer,'reasoning':'我的核验依据'},image)
    assert not paused and state['view']['check']['status']==status
    assert state['view']['check']['reasoning_reviewed'] is False
