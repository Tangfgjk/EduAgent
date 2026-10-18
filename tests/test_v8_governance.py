from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
import statistics

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from app.infrastructure.db.models import DevelopmentReview, LearningFact, ModelOperation
from app.infrastructure.db.maintenance import backup, restore
from app.modules.learning.activity_catalog import get_activity
from app.modules.learning.content_catalog import get_pack
from app.modules.governance.workbench import ROOT
from app.worker import Worker
from tests.test_s02 import database, env, recovery_database


def review(env, target, version, conclusion='development_ready', revision=0, kind='content'):
    return env.admin.post('/api/v1/admin/development-reviews', json={'kind':kind,'target_id':target,'version':version,
        'expected_revision':revision,'conclusion':conclusion,'note':'核对开发来源与参考依据，保留人工审核记录。'})


def enable(env, identifier, activity=False):
    segment = 'activity-catalog' if activity else 'content-catalog'
    item = next(item for item in env.admin.get('/api/v1/admin/'+segment).json()['items'] if item['unit_id']==identifier)
    return env.admin.post(f'/api/v1/admin/{segment}/{identifier}/development-release', json={
        'content_version':item['content_version'],'expected_revision':item['revision'],'enabled':True,'acknowledge_development_only':True})


def test_new_content_review_revision_withdrawal_and_immutability(env, tmp_path):
    pack = get_pack('math.g9.probability.counting.dev1')
    assert enable(env, pack.unit_id).json()['error']['code']=='development_review_required'
    assert review(env, pack.unit_id, '0'*64).status_code==409
    assert review(env, pack.unit_id, pack.version).status_code==200
    assert enable(env, pack.unit_id).status_code==200
    assert review(env, pack.unit_id, pack.version, 'needs_revision', revision=1).status_code==200
    item=next(i for i in env.admin.get('/api/v1/admin/content-catalog').json()['items'] if i['unit_id']==pack.unit_id)
    assert not item['enabled'] and not item['professional_review']
    assert enable(env, pack.unit_id).status_code==409
    assert review(env, pack.unit_id, pack.version, revision=1).status_code==409
    with pytest.raises(DBAPIError):
        with env.db.transaction() as session:
            session.execute(update(DevelopmentReview).values(note='try overwrite'))
    file=tmp_path/'review-backup.json'
    backup(env.db,file)
    with recovery_database(env.db,tmp_path) as recovered:
        restore(recovered,file)
        with recovered.transaction() as session:
            assert session.scalar(select(func.count()).select_from(DevelopmentReview))==2


def test_evaluation_reports_reviews_are_admin_only_versioned_and_do_not_call_models(env):
    student,_=env.student()
    assert student.get('/api/v1/admin/evaluation-reports').status_code==403
    reports=env.admin.get('/api/v1/admin/evaluation-reports').json()
    assert len(reports['items'])==17
    detail=env.admin.get('/api/v1/admin/evaluation-reports/holdout-fake').json()
    assert detail['mode']=='fake' and len(detail['cases'])==10
    row=detail['cases'][0]
    assert review(env,row['target_id'],detail['report_sha256'],'reference_ambiguous',kind='evaluation').status_code==200
    assert review(env,row['target_id'],'0'*64,'model_issue',revision=1,kind='evaluation').status_code==409
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls=[pool.submit(review,env,row['target_id'],detail['report_sha256'],'model_issue',1,'evaluation') for _ in range(2)]
        assert sorted(c.result().status_code for c in calls)==[200,409]
    current=env.admin.get('/api/v1/admin/evaluation-reports/holdout-fake').json()['cases'][0]
    assert current['revision']==2 and len(current['reviews'])==2
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ModelOperation))==0
    assert env.admin.get('/api/v1/admin/evaluation-reports/not-allowed').status_code==404


def test_packaged_reports_match_frozen_documents():
    for source,target in [('42-DeepSeek概率追问基线评测_20261017.json','single-baseline.json'),
        ('45-概率多轮追问离线流程验收_20261017.json','multiturn-fake.json'),
        ('48-独立多轮样本离线验证_20261017.json','holdout-fake.json')]:
        assert (Path('docs/评测')/source).read_bytes()==(ROOT/target).read_bytes()
    assert str(Fraction(2,8))==get_pack('math.g9.probability.counting.dev1').tasks[1]['answer']
    assert str(statistics.median([3,4,6,9,12]))==get_pack('math.g7.statistics.frequency.dev1').tasks[1]['answer']


@pytest.mark.parametrize('verification_answer,expected', [('3/7','passed'), ('不确定','unverifiable')])
def test_new_teach_cycle_fences_verification_and_persists_three_contributions(env, verification_answer, expected):
    client,_=env.student()
    wid=env.workspace(client,'temporary')['workspace_id']
    item=get_activity('math.g9.probability.teach.dev2')
    assert review(env,item.unit_id,item.version).status_code==200
    assert enable(env,item.parent_unit_id).status_code==200
    assert enable(env,item.unit_id,activity=True).status_code==200
    path=f'/api/v1/student/workspaces/{wid}/teach'
    response=client.post(path,headers={'Idempotency-Key':'teach-v2'},json={'unit_id':item.unit_id})
    assert response.status_code==202,response.text
    path+='/'+response.json()['run_id']
    assert Worker(env.db).step()
    run=client.get(path).json()
    assert run['view']['verification_task'] is None
    def submit(action,answer,key):
        state=client.get(path).json()
        return client.post(path+'/inputs',headers={'Idempotency-Key':key},json={'action':action,'answer':answer,'reasoning':'先列出等可能基本结果，再核对事件条件。','expected_revision':state['revision'],'gate_id':state['gate_id']})
    assert submit('verify','3/7','early').status_code==409
    assert submit('contribute','1/2','explain').status_code==202
    assert Worker(env.db).step()
    assert submit('conclude','2/5','revise').status_code==202
    assert Worker(env.db).step()
    run=client.get(path).json()
    assert run['view']['phase']=='verification' and run['view']['verification_task']['task_id']=='C-PRB-02'
    verification_gate, verification_revision = run['gate_id'], run['revision']
    assert submit('contribute','2/5','assist-during-verification').status_code==409
    tutor=client.post(f'/api/v1/student/workspaces/{wid}/tutoring',headers={'Idempotency-Key':'concurrent'},json={'unit_id':item.parent_unit_id})
    assert tutor.status_code==409
    assert submit('verify',verification_answer,'verify').status_code==202
    assert Worker(env.db).step()
    run=client.get(path).json()
    assert run['status']=='completed' and run['view']['verification_check']['status']==expected
    assert run['view']['verification_check']['independent'] and not run['view']['check']['independent']
    assert client.post(path+'/inputs',headers={'Idempotency-Key':'again'},json={'action':'verify','answer':'0',
        'reasoning':'试图覆盖已保存结果','expected_revision':verification_revision,'gate_id':verification_gate}).status_code==409
    with env.db.transaction() as session:
        facts=list(session.scalars(select(LearningFact).where(LearningFact.run_id==run['run_id'])))
        assert len(facts)==3 and sum(bool(f.payload['independent']) for f in facts)==1
        assert session.scalar(select(func.count()).select_from(ModelOperation))==0
    if expected == 'unverifiable':
        pending = next(row for row in env.admin.get('/api/v1/admin/answer-reviews').json()['items'] if row['run_id'] == run['run_id'])
        assert env.admin.post('/api/v1/admin/answer-reviews/'+pending['review_id']+'/resolve', json={
            'expected_revision':pending['revision'],'conclusion':'incorrect','note':'开发复核，要求重新核对本题数值。'}).status_code==200
        card = client.get(f'/api/v1/student/workspaces/{wid}/review-cards').json()['items'][0]
        from tests.test_s06 import recall
        assert recall(client,wid,card,'3/7').status_code==200
