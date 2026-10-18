"""概率双路线、独立审核、来源与旧方程兼容。"""
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.infrastructure.db.models import Account, LearningFact
from app.modules.learning.activity_catalog import ACTIVITY_PACKS
from app.modules.learning.activity_content import CONTENT_VERSION, UNIT_ID
from app.worker import Worker
from tests.test_s02 import database, env, recovery_database
from tests.test_s07 import send, command, enable as enable_equation
from tests.test_s10 import release as release_base
from tests.test_s06 import recall

PROB = ACTIVITY_PACKS[1]


def release(env, enabled=True, **override):
    row = next(item for item in env.admin.get('/api/v1/admin/activity-catalog').json()['items'] if item['unit_id'] == PROB.unit_id)
    return env.admin.post('/api/v1/admin/activity-catalog/'+PROB.unit_id+'/development-release', json={
        'content_version':row['content_version'], 'expected_revision':row['revision'],
        'enabled':enabled, 'acknowledge_development_only':True, **override})


def start(env, kind='roundtable'):
    client, _ = env.student()
    wid = env.workspace(client, 'temporary')['workspace_id']
    release(env)
    base = f'/api/v1/student/workspaces/{wid}'
    response = client.post(base+'/'+kind, headers={'Idempotency-Key':'start'}, json={'unit_id':PROB.unit_id})
    assert response.status_code == 202, response.text
    path = base+'/'+kind+'/'+response.json()['run_id']
    Worker(env.db).step()
    return client, wid, path


@pytest.mark.parametrize('kind', ['roundtable','teach'])
def test_probability_two_contributions_exact_versions_and_evidence(env, kind):
    client, wid, path = start(env,kind)
    run=client.get(path).json()
    assert run['view']['task']['task_id']=='P-PRB-02'
    assert set(run['view']['task'])=={'task_id','stem','answer_format','exposure_status'}
    assert run['view']['messages']==[] and run['view']['activity_version']==PROB.version
    challenge=send(env,client,path,'contribute','1/2')
    assert challenge['view']['phase']=='challenge'
    assert '颜色' in challenge['view']['messages'][1]['text'] or '球' in challenge['view']['messages'][1]['text']
    done=send(env,client,path,'conclude','2/5')
    assert done['status']=='completed' and done['view']['check']['status']=='passed'
    assert done['view']['check']['independent'] is False and done['view']['check']['reasoning_reviewed'] is False
    with env.db.transaction() as session:
        facts=list(session.scalars(select(LearningFact).where(LearningFact.workspace_id==wid)))
        assert len(facts)==2
        assert all(f.content_version==PROB.parent.version and f.payload['activity_version']==PROB.version and f.task_id=='P-PRB-02' for f in facts)
    evidence=client.get(f'/api/v1/student/workspaces/{wid}/evidence').json()
    assert evidence['record_count']==2 and sum(group['checkpoint_answers'] for group in evidence['groups'])==0
    assert all(item['score'] is None for item in evidence['competencies'])
    assert client.get('/api/v1/student/evidence').json()['record_count']==0


def test_catalog_secrecy_independent_release_and_revision(env):
    client,_=env.student()
    catalog=client.get('/api/v1/student/activity-catalog').json()['items']
    assert {item['unit_id'] for item in catalog} == {
        'math.g7.equations.activities.dev1',
        'math.g9.probability.activities.dev1',
        'math.g9.probability.teach.dev2',
        'math.g9.probability.roundtable.dev2',
    }
    assert all(not item['tasks'] for item in catalog)
    assert client.get('/api/v1/admin/activity-catalog').status_code==403
    assert ACTIVITY_PACKS[0].unit_id==UNIT_ID and ACTIVITY_PACKS[0].version==CONTENT_VERSION
    enable_equation(env)
    release_base(env,PROB.parent)
    wid=env.workspace(client,'temporary')['workspace_id']
    response=client.post(f'/api/v1/student/workspaces/{wid}/teach',headers={'Idempotency-Key':'a'},json={'unit_id':PROB.unit_id})
    assert response.json()['error']['code']=='content_not_enabled'
    assert release(env,content_version='0'*64).status_code==409
    assert release(env).status_code==200
    assert release(env,expected_revision=0).status_code==409
    assert client.post('/api/v1/admin/activity-catalog/'+PROB.unit_id+'/development-release',json={}).status_code==403


@pytest.mark.parametrize('boundary',['withdraw','real_student','archive','version','retired'])
def test_worker_rechecks_new_pack_after_queue(env,boundary,monkeypatch):
    client,wid,path=start(env)
    run=client.get(path).json()
    assert client.post(path+'/inputs',headers={'Idempotency-Key':'queued'},json=command(run)).status_code==202
    if boundary=='withdraw': release(env,False)
    elif boundary=='retired':
        import app.worker as worker_module
        from app.modules.errors import DomainError
        def removed(*args, **kwargs):
            raise DomainError(409, 'version_conflict', '历史互动版本已退役')
        monkeypatch.setattr(worker_module, 'activity_for_run', removed)
    elif boundary=='archive': client.post(f'/api/v1/student/workspaces/{wid}/archive',json={'expected_revision':1})
    else:
        from app.infrastructure.db.models import AgentRun
        with env.db.transaction() as session:
            if boundary=='real_student': session.scalar(select(Account).where(Account.role=='student')).source_kind='real_student'
            else: session.get(AgentRun,run['run_id']).version='unsupported'
    Worker(env.db).step()
    assert client.get(path).json()['status']=='failed'
    if boundary=='retired': assert client.get(path).json()['error_code']=='version_conflict'
    with env.db.transaction() as session:
        assert not list(session.scalars(select(LearningFact).where(LearningFact.workspace_id==wid)))


def test_unknown_human_review_recall_and_activity_withdrawal(env):
    client,wid,path=start(env,'teach')
    send(env,client,path,'contribute','1/2')
    send(env,client,path,'conclude','我还不能用数值表达')
    row=env.admin.get('/api/v1/admin/answer-reviews').json()['items'][0]
    assert row['task_id']=='P-PRB-02' and row['content_version']==PROB.parent.version and row['task']['answer']=='2/5'
    assert env.admin.post('/api/v1/admin/answer-reviews/'+row['review_id']+'/resolve',json={
        'expected_revision':row['revision'],'conclusion':'incorrect','note':'仅核对数值，保留学生原文，不评定讲解质量'}).status_code==200
    base=f'/api/v1/student/workspaces/{wid}'
    card=client.get(base+'/review-cards').json()['items'][0]
    release_base(env,PROB.parent)
    release(env,False)
    assert recall(client,wid,card,'2/5').json()['error']['code']=='content_not_enabled'
    release(env)
    result=recall(client,wid,card,'4/10')
    assert result.status_code==200 and result.json()['attempt']['status']=='passed',result.text
    other,_=env.student('student.other')
    assert other.get(path).status_code==404


def test_probability_gate_idempotency_and_cross_route(env):
    client,wid,path=start(env)
    run=client.get(path).json()
    payload=command(run,'contribute','1/2')
    headers={'Idempotency-Key':str(uuid4())}
    first=client.post(path+'/inputs',headers=headers,json=payload)
    assert first.status_code==202
    assert client.post(path+'/inputs',headers=headers,json=payload).status_code==202
    Worker(env.db).step()
    assert client.post(path+'/inputs',headers={'Idempotency-Key':'stale'},json=payload).status_code==409
    assert client.get(path.replace('/roundtable/','/teach/')).status_code==404
    with env.db.transaction() as session:
        assert len(list(session.scalars(select(LearningFact).where(LearningFact.workspace_id==wid))))==1


@pytest.mark.parametrize('kind', ['roundtable','teach'])
def test_probability_checkpoint_backup_restore(env,tmp_path,kind):
    from app.infrastructure.db.maintenance import backup, restore
    from app.infrastructure.db.models import AgentRun
    from app.modules.identity.service import Identity
    from app.modules.learning.activity_service import Activities
    from tests.test_s02 import PASSWORD
    client,wid,path=start(env,kind)
    waiting=send(env,client,path,'contribute','1/2')
    destination=tmp_path/'probability.json'
    backup(env.db,destination)
    with recovery_database(env.db,tmp_path) as recovered:
        restore(recovered,destination)
        with recovered.transaction() as session:
            username=session.scalar(select(Account).where(Account.role=='student')).username
        identity=Identity(recovered)
        _,token,csrf=identity.login(username,PASSWORD,'127.0.0.1')
        with recovered.transaction() as session:
            actor=identity.authorize(session,token,csrf,'student',True)
            Activities(kind).submit(session,actor,wid,waiting['run_id'],command(waiting,'conclude','2/5'),'restored')
        assert Worker(recovered).step()
        with recovered.transaction() as session:
            run=session.get(AgentRun,waiting['run_id'])
            assert run.status=='completed' and run.view['check']['status']=='passed'
            assert run.view['activity_version']==PROB.version
