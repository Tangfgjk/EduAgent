from copy import deepcopy
import json

import pytest
from sqlalchemy import func, select

from app.infrastructure.db.models import Account, LearningFact, ModelOperation, ModelDailyBudget
from app.infrastructure.runtime.model_tasks import ModelTaskRunner, day_key
from app.llm.provider import FakeProvider, ProviderError
from app.modules.learning.activity_catalog import get_activity
from app.modules.learning.roundtable_coaching import UNIT, fake_proposal, freeze_request, validate_proposal
from app.worker import Worker
from tests.test_s02 import database, env
from tests.test_model_runtime import publish
from tests.test_teach_model import submit
from tests.test_v8_governance import review, enable


def start_roundtable(env):
    client, account = env.student('student.roundtable')
    wid = env.workspace(client, 'temporary')['workspace_id']
    item = get_activity(UNIT)
    assert enable(env, UNIT, activity=True).status_code == 409
    assert review(env, UNIT, item.version).status_code == 200
    assert enable(env, item.parent_unit_id).status_code == 200
    assert enable(env, UNIT, activity=True).status_code == 200
    publish(env, prompt_version='probability.followup.v3', account_daily_calls=0, site_daily_calls=0)
    path = f'/api/v1/student/workspaces/{wid}/roundtable'
    response = client.post(path, headers={'Idempotency-Key': 'start'}, json={'unit_id': UNIT})
    assert response.status_code == 202
    path += '/' + response.json()['run_id']
    assert Worker(env.db, enable_models=False).step()
    return client, path, account


def pending(env, client, path):
    assert client.get(path).json()['coaching_available']
    assert submit(client, path, request_model=True).status_code == 202
    assert Worker(env.db, enable_models=False).step()
    run = client.get(path).json()
    assert len(run['view']['messages']) == 1
    assert run['coaching']['status'] == 'pending'
    return run


def test_roles_student_comparison_and_independent_numeric_check(env):
    client, path, account = start_roundtable(env)
    run = pending(env, client, path)
    assert ModelTaskRunner(env.db).step(operation_id=run['coaching']['operation_id'])
    run = client.get(path).json()
    assert run['coaching']['status'] == 'succeeded'
    voices = run['coaching']['result']['voices']
    assert [v['role'] for v in voices] == ['proposer', 'critic', 'organizer']
    assert len({v['text'] for v in voices}) == 3
    assert not run['coaching_available']
    with env.db.transaction() as session:
        op = session.get(ModelOperation, run['coaching']['operation_id'])
        body = json.loads(op.request['generation']['messages'][1]['content'])
        assert set(body) == {'task', 'student_text', 'candidate_answer'}
        assert '3/7' not in json.dumps(body) and account['username'] not in json.dumps(body)
        assert session.get(ModelDailyBudget, (day_key(), 'account:' + account['id'])).calls == 1
    assert submit(client, path, 'refine', request_model=True, key='more').status_code == 409
    assert submit(client, path, 'conclude', '2/5', request_model=True, key='no-more').status_code == 409
    assert submit(client, path, 'conclude', '2/5', key='compare').status_code == 202
    assert Worker(env.db, enable_models=False).step()
    run = client.get(path).json()
    assert run['view']['check']['status'] == 'passed' and run['view']['phase'] == 'verification'
    assert run['coaching']['result'] is None and not run['coaching_available']
    assert submit(client, path, 'verify', '3/7', request_model=True, key='forbidden').status_code == 409
    assert submit(client, path, 'verify', '3/7', key='verify').status_code == 202
    assert Worker(env.db, enable_models=False).step()
    run = client.get(path).json()
    assert run['status'] == 'completed' and len(run['coaching']['result']['voices']) == 3
    assert run['view']['verification_check']['independent']
    with env.db.transaction() as session:
        facts = list(session.scalars(select(LearningFact).where(LearningFact.run_id == run['run_id'])))
        assert len(facts) == 3 and sum(f.payload['independent'] for f in facts) == 1
        assert session.scalar(select(func.count()).select_from(ModelOperation)) == 1


@pytest.mark.parametrize('outcome', ['cancel', 'invalid', 'timeout', 'late', 'withdraw'])
def test_failures_and_cancellation_leave_student_in_control(env, outcome):
    client, path, _ = start_roundtable(env)
    run = pending(env, client, path)
    oid = run['coaching']['operation_id']
    runner = ModelTaskRunner(env.db)
    other, _ = env.student('student.other')
    assert other.get(path).status_code == 404
    assert other.post(path + '/model-operations/' + oid + '/cancel').status_code == 404
    if outcome == 'cancel':
        assert client.post(path + '/model-operations/' + oid + '/cancel').status_code == 200
        assert not runner.step(operation_id=oid)
    elif outcome in {'invalid', 'timeout'}:
        def provider(*args):
            return FakeProvider(('{}' if outcome == 'invalid' else ProviderError('timeout'),))
        assert ModelTaskRunner(env.db, provider).step(operation_id=oid)
        assert client.get(path).json()['coaching']['status'] == ('failed' if outcome == 'invalid' else 'unknown')
        assert not runner.step(operation_id=oid)
    elif outcome == 'withdraw':
        assert review(env, UNIT, get_activity(UNIT).version, 'needs_revision', revision=1).status_code == 200
        assert not runner.step(operation_id=oid)
        assert client.get(path).json()['coaching']['status'] == 'discarded'
        return
    else:
        claim = runner.claim(operation_id=oid)
        assert claim
    assert submit(client, path, 'conclude', '2/5', key='compare').status_code == 202
    assert Worker(env.db, enable_models=False).step()
    if outcome == 'late':
        assert not runner.complete(claim, error='timeout')
    assert client.get(path).json()['view']['phase'] == 'verification'


def test_source_and_route_boundaries_and_preset_fallback(env):
    client, path, account = start_roundtable(env)
    root = path.rsplit('/', 2)[0]
    assert client.post(root + '/teach', headers={'Idempotency-Key': 'wrong'}, json={'unit_id': UNIT}).status_code == 409
    with env.db.transaction() as session:
        session.get(Account, account['id']).source_kind = 'real_student'
    assert submit(client, path, request_model=True).status_code in {403, 409}
    with env.db.transaction() as session:
        session.get(Account, account['id']).source_kind = 'synthetic'
    assert submit(client, path, request_model=False).status_code == 202
    assert Worker(env.db, enable_models=False).step()
    run = client.get(path).json()
    assert len(run['view']['messages']) == 4 and run['coaching'] is None


def test_structural_checks_reject_missing_duplicate_injection_and_grading():
    from types import SimpleNamespace
    account = SimpleNamespace(display_name='虚构身份', username='synthetic', id='id', learner_id='learner')
    snapshot = freeze_request(get_activity(UNIT), '我按红蓝颜色计数，还没有说明等可能。', '1/2', account, 512, 'probability.followup.v3')
    valid = fake_proposal(snapshot['student_text'])
    assert validate_proposal(json.dumps(valid, ensure_ascii=False), snapshot)
    for mutation in ('missing', 'duplicate', 'quote', 'grade', 'answer', 'extra', 'role'):
        value = deepcopy(valid)
        if mutation == 'missing': value['voices'].pop()
        elif mutation == 'duplicate': value['voices'][1]['text'] = value['voices'][0]['text']
        elif mutation == 'quote': value['voices'][1]['quote'] = '历史或其他项目原文'
        elif mutation == 'grade': value['voices'][1]['text'] = '你已经掌握了这个知识点吗？'
        elif mutation == 'answer': value['voices'][1]['text'] = '正确答案是2/5，知道了吗？'
        elif mutation == 'role': value['voices'][1]['role'] = 'proposer'
        else: value['score'] = 100
        with pytest.raises(ProviderError):
            validate_proposal(json.dumps(value, ensure_ascii=False), snapshot)
