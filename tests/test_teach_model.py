import json

import pytest
from sqlalchemy import select

from app.infrastructure.db.models import Account, LearningFact, ModelOperation
from app.infrastructure.runtime.model_tasks import ModelTaskRunner, model_key
from app.llm.provider import FakeProvider, ProviderError
from app.modules.learning.activity_catalog import get_activity
from app.modules.learning.teach_coaching import freeze_request, validate_proposal
from app.worker import Worker
from tests.test_s02 import database, env
from tests.test_model_runtime import publish
from tests.test_v8_governance import review, enable


def start(env, name='student.teach'):
    client, account = env.student(name)
    wid = env.workspace(client, 'temporary')['workspace_id']
    item = get_activity('math.g9.probability.teach.dev2')
    assert review(env, item.unit_id, item.version).status_code == 200
    assert enable(env, item.parent_unit_id).status_code == 200
    assert enable(env, item.unit_id, activity=True).status_code == 200
    publish(env, prompt_version='probability.followup.v3', account_daily_calls=0, site_daily_calls=0)
    path = f'/api/v1/student/workspaces/{wid}/teach'
    response = client.post(path, headers={'Idempotency-Key': 'start'}, json={'unit_id': item.unit_id})
    assert response.status_code == 202
    path += '/' + response.json()['run_id']
    assert Worker(env.db, enable_models=False).step()
    return client, path, account


def submit(client, path, action='contribute', answer='1/2', reasoning='我按红蓝两种颜色计数，所以各占一半。', request_model=False, key='input'):
    run = client.get(path).json()
    return client.post(path + '/inputs', headers={'Idempotency-Key': key}, json={
        'action': action, 'answer': answer, 'reasoning': reasoning, 'request_model': request_model,
        'expected_revision': run['revision'], 'gate_id': run['gate_id']})


def pending(env, client, path):
    response = submit(client, path, request_model=True)
    assert response.status_code == 202, response.text
    assert response.json()['coaching']['status'] == 'waiting'
    assert Worker(env.db, enable_models=False).step()
    run = client.get(path).json()
    assert run['view']['phase'] == 'challenge' and run['coaching']['status'] == 'pending'
    assert len(run['view']['messages']) == 1  # No preset misconception masquerades as a model reply.
    return run


def test_teach_model_full_cycle_and_separate_numeric_verification(env):
    client, path, _ = start(env)
    assert client.get(path).json()['coaching_available']
    run = pending(env, client, path)
    assert ModelTaskRunner(env.db).step(operation_id=run['coaching']['operation_id'])
    run = client.get(path).json()
    assert run['coaching']['status'] == 'succeeded'
    assert run['coaching']['prompt_version'] == 'probability.teach.followup.v1'
    assert run['coaching']['result']['question'].endswith('？')
    with env.db.transaction() as session:
        op = session.get(ModelOperation, run['coaching']['operation_id'])
        payload = json.loads(op.request['generation']['messages'][1]['content'])
        assert set(payload) == {'task', 'student_text', 'candidate_answer'}
        assert 'verification_task' not in op.request and '3/7' not in json.dumps(payload)
    assert submit(client, path, 'conclude', '2/5', key='fix').status_code == 202
    assert Worker(env.db, enable_models=False).step()
    verification = client.get(path).json()
    assert verification['view']['check']['status'] == 'passed'
    assert not verification['coaching_available'] and verification['coaching']['result'] is None
    assert submit(client, path, 'verify', '3/7', request_model=True, key='forbidden').status_code == 409
    assert submit(client, path, 'verify', '3/7', key='verify').status_code == 202
    assert Worker(env.db, enable_models=False).step()
    final = client.get(path).json()
    assert final['status'] == 'completed' and final['view']['verification_check']['status'] == 'passed'
    assert final['view']['verification_check']['independent']
    assert final['coaching']['result'] and not final['coaching']['current']  # Historical question only.
    with env.db.transaction() as session:
        facts = list(session.scalars(select(LearningFact).where(LearningFact.run_id == final['run_id'])))
        assert len(facts) == 3 and sum(f.payload['independent'] for f in facts) == 1
        assert all(not f.payload['reasoning_reviewed'] for f in facts)


@pytest.mark.parametrize('kind', ['invalid', 'timeout', 'cancel', 'late', 'withdraw'])
def test_teach_model_failures_preserve_student_progress(env, kind):
    client, path, _ = start(env)
    run = pending(env, client, path)
    oid = run['coaching']['operation_id']
    if kind in {'invalid', 'timeout'}:
        response = json.dumps({'quote': '红蓝两种颜色', 'focus': 'ratio', 'question': '正确答案是2/5，你明白吗？'}) if kind == 'invalid' else ProviderError('timeout')
        assert ModelTaskRunner(env.db, lambda c, s: FakeProvider((response,))).step(operation_id=oid)
        assert client.get(path).json()['coaching']['status'] == ('failed' if kind == 'invalid' else 'unknown')
    elif kind == 'cancel':
        other, _ = env.student('student.other')
        assert other.post(path + '/model-operations/' + oid + '/cancel').status_code == 404
        assert client.post(path + '/model-operations/' + oid + '/cancel').status_code == 200
        assert not ModelTaskRunner(env.db).step(operation_id=oid)
    elif kind == 'withdraw':
        item = get_activity('math.g9.probability.teach.dev2')
        assert review(env, item.unit_id, item.version, 'needs_revision', revision=1).status_code == 200
        assert not ModelTaskRunner(env.db).step(operation_id=oid)
        assert client.get(path).json()['coaching']['status'] == 'discarded'
        return
    else:
        runner = ModelTaskRunner(env.db)
        claim = runner.claim(operation_id=oid)
        assert claim
    assert submit(client, path, 'conclude', '2/5', key='proceed').status_code == 202
    assert Worker(env.db, enable_models=False).step()
    if kind == 'late':
        assert not runner.complete(claim, error='timeout')
        assert client.get(path).json()['coaching']['status'] == 'discarded'
    assert client.get(path).json()['view']['phase'] == 'verification'
    assert client.get(path).json()['view']['check']['status'] == 'passed'


def test_teach_model_budget_and_real_source_gate(env):
    client, path, account = start(env)
    with env.db.transaction() as session:
        session.get(Account, account['id']).source_kind = 'real_student'
    assert submit(client, path, request_model=True).status_code in {403, 409}


def test_teach_validation_cannot_quote_answer_or_history_or_grade():
    from types import SimpleNamespace
    account = SimpleNamespace(display_name='虚构身份', username='synthetic', id='id', learner_id='learner')
    snapshot = freeze_request(get_activity('math.g9.probability.teach.dev2'), '我把颜色当作基本结果。', '2/5', account, 512, 'probability.followup.v3')
    for value in ({'quote': '2/5', 'focus': 'ratio', 'question': '你如何说明计数依据？'},
                  {'quote': '基本结果', 'focus': 'ratio', 'question': '你的概率是2/5吗？'},
                  {'quote': '基本结果', 'focus': 'ratio', 'question': '你已经掌握了吗？'},
                  {'quote': '基本结果', 'focus': 'ratio', 'question': '解释依据？还有什么？'},
                  {'quote': '基本结果', 'focus': 'ratio', 'question': '如何解释依据？', 'score': 100}):
        with pytest.raises(ProviderError):
            validate_proposal(json.dumps(value, ensure_ascii=False), snapshot)
    assert validate_proposal(json.dumps({'quote': '基本结果', 'focus': 'sample_space', 'question': '为什么选择颜色作为一个基本结果？'}, ensure_ascii=False), snapshot)


def test_unreadable_model_key_does_not_crash_configuration(tmp_path, monkeypatch):
    from pathlib import Path
    path = tmp_path / 'credential.key'
    path.write_text('test-only-not-real')
    monkeypatch.delenv('WENJIN_DEEPSEEK_KEY', raising=False)
    monkeypatch.setenv('WENJIN_DEEPSEEK_KEY_FILE', str(path))
    def denied(*args, **kwargs):
        raise PermissionError('no access')
    monkeypatch.setattr(Path, 'read_text', denied)
    assert model_key() == ''
