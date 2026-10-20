from sqlalchemy import select,func,update
from sqlalchemy.exc import DBAPIError
import pytest
from tests.test_s02 import database,env
from tests.test_g08 import ready
from app.modules.identity.privacy import erase_requested
from app.infrastructure.db.models import Account,Workspace,LearningFact
from app.modules.errors import DomainError


def test_owner_export_excludes_truth_and_erasure_requires_request_backup_and_offline_ack(env,tmp_path):
    client,wid,_=ready(env)
    other,identity=env.student('privacy.other');other_wid=env.workspace(other)['workspace_id']
    exported=client.get('/api/v1/student/privacy/export')
    assert exported.status_code==200,exported.text
    value=exported.json()['data']
    aid=value['account']['id']
    assert {s['id'] for s in value['workspaces']}=={wid}
    assert value['facts'] and other_wid not in exported.text
    assert 'password_hash' not in exported.text and 'task_snapshot' not in exported.text and 'expected_answer' not in exported.text
    assert env.admin.get('/api/v1/student/privacy/export').status_code==403
    with pytest.raises(DomainError):erase_requested(env.db,aid,tmp_path/'sensitive-backup.json')
    assert client.post('/api/v1/student/privacy/erasure-request',json={'acknowledge_export_and_disable':False}).status_code==422
    response=client.post('/api/v1/student/privacy/erasure-request',json={'acknowledge_export_and_disable':True})
    assert response.status_code==202 and response.json()['status']=='pending_maintenance'
    assert client.get('/api/v1/student/workspaces').status_code==401
    preview=erase_requested(env.db,aid,tmp_path/'sensitive-backup.json');assert preview['dry_run']
    assert preview['counts']['learning_facts']>0 and not (tmp_path/'sensitive-backup.json').exists()
    with pytest.raises(DomainError):erase_requested(env.db,aid,tmp_path/'sensitive-backup.json',apply=True)
    result=erase_requested(env.db,aid,tmp_path/'sensitive-backup.json',apply=True,maintenance_confirmed=True)
    assert result['dry_run'] is False and (tmp_path/'sensitive-backup.json').exists()
    with env.db.transaction() as s:
        assert s.get(Account,aid) is None and s.get(Workspace,wid) is None
        assert s.scalar(select(func.count()).select_from(LearningFact).where(LearningFact.workspace_id==wid))==0
        assert s.get(Workspace,other_wid) is not None and s.get(Account,identity['id']) is not None
    assert other.get('/api/v1/student/workspaces').status_code==200
