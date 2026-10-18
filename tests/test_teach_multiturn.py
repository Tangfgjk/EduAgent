import json

import pytest
from sqlalchemy import func, select

from app.infrastructure.db.models import LearningFact, ModelOperation, ModelDailyBudget
from app.infrastructure.runtime.model_tasks import ModelTaskRunner, day_key
from app.llm.provider import FakeProvider, ProviderError
from app.worker import Worker
from tests.test_s02 import database, env
from tests.test_teach_model import start, submit, pending
from tests.test_model_runtime import publish


def respond(env, client, path, question='为什么采用这样的基本结果来计数？'):
    op = client.get(path).json()['coaching']
    def provider(config, snapshot):
        return FakeProvider((json.dumps({'quote': snapshot['student_text'][:80], 'focus': 'sample_space',
            'question': question}, ensure_ascii=False),))
    assert ModelTaskRunner(env.db, provider).step(operation_id=op['operation_id'])
    return client.get(path).json()


def test_three_rounds_keep_provenance_limits_idempotency_and_independent_check(env):
    client, path, account = start(env)
    pending(env, client, path)
    run = respond(env, client, path)
    ops = [run['coaching']['operation_id']]
    for number in (2, 3):
        assert run['coaching_available']
        assert submit(client, path, 'refine', '2/5', reasoning=f'第{number}次回应：每个球是基本结果，颜色对应的球数可能不同。',
            request_model=True, key=f'refine-{number}').status_code == 202
        assert Worker(env.db, enable_models=False).step()
        queued = client.get(path).json()
        assert not queued['coaching_available']
        assert queued['view']['model_rounds'] == number
        run = respond(env, client, path)
        ops.append(run['coaching']['operation_id'])
        assert len(run['coaching_history']) == number - 1
        with env.db.transaction() as session:
            op = session.get(ModelOperation, ops[-1])
            assert op.request['prompt_version'] == 'probability.teach.followup.v2'
            assert op.request['context_sources'] == ops[:-1]
            body = json.loads(op.request['generation']['messages'][1]['content'])
            assert len(body['history']) == number - 1
            assert '3/7' not in json.dumps(body) and 'verification_task' not in body
    assert not run['coaching_available']
    assert submit(client, path, 'refine', request_model=True, key='fourth').status_code == 409
    assert submit(client, path, 'conclude', '2/5', key='finish').status_code == 202
    assert Worker(env.db, enable_models=False).step()
    run = client.get(path).json()
    assert run['view']['phase'] == 'verification' and run['coaching_history'] == []
    assert run['coaching']['result'] is None
    assert submit(client, path, 'refine', request_model=True, key='during-verification').status_code == 409
    assert submit(client, path, 'verify', '3/7', key='verify').status_code == 202
    assert Worker(env.db, enable_models=False).step()
    final = client.get(path).json()
    assert final['view']['verification_check']['status'] == 'passed'
    assert len(final['coaching_history']) == 2 and final['coaching']['result']
    with env.db.transaction() as session:
        facts = list(session.scalars(select(LearningFact).where(LearningFact.run_id == final['run_id'])))
        assert len(facts) == 5 and sum(f.payload['independent'] for f in facts) == 1
        assert session.get(ModelDailyBudget, (day_key(), 'account:' + account['id'])).calls == 3
        assert session.scalar(select(func.count()).select_from(ModelOperation).where(ModelOperation.run_id == final['run_id'])) == 3


@pytest.mark.parametrize('outcome', ['cancel', 'invalid', 'late'])
def test_followup_busy_failure_and_late_result_never_force_more_calls(env, outcome):
    client, path, _ = start(env)
    run = pending(env, client, path)
    assert submit(client, path, 'refine', request_model=True, key='busy').status_code == 409
    assert submit(client, path, 'refine', key='no-consent').status_code == 409
    respond(env, client, path)
    run = client.get(path).json()
    payload = {'action': 'refine', 'answer': '2/5', 'reasoning': '我改用等可能的球来计数。', 'request_model': True,
        'expected_revision': run['revision'], 'gate_id': run['gate_id']}
    assert client.post(path + '/inputs', headers={'Idempotency-Key': 'same'}, json=payload).status_code == 202
    assert client.post(path + '/inputs', headers={'Idempotency-Key': 'same'}, json=payload).status_code == 202
    assert Worker(env.db, enable_models=False).step()
    run = client.get(path).json()
    oid = run['coaching']['operation_id']
    runner = ModelTaskRunner(env.db)
    if outcome == 'cancel':
        assert client.post(path + '/model-operations/' + oid + '/cancel').status_code == 200
        assert not runner.step(operation_id=oid)
    elif outcome == 'invalid':
        def bad(*args):
            return FakeProvider(('{}',))
        assert ModelTaskRunner(env.db, bad).step(operation_id=oid)
        assert client.get(path).json()['coaching']['status'] == 'failed'
        assert not runner.step(operation_id=oid)
    else:
        claim = runner.claim(operation_id=oid)
        assert claim
    assert submit(client, path, 'conclude', '2/5', key='leave').status_code == 202
    assert Worker(env.db, enable_models=False).step()
    if outcome == 'late':
        assert not runner.complete(claim, error='timeout')
    assert client.get(path).json()['view']['phase'] == 'verification'


def test_history_excludes_changed_configuration_and_tampered_requests(env):
    client, path, _ = start(env)
    pending(env, client, path)
    run = respond(env, client, path)
    with env.db.transaction() as session:
        op = session.get(ModelOperation, run['coaching']['operation_id'])
        op.request_hash = 'tampered'
    assert submit(client, path, 'refine', request_model=True, key='after-tamper').status_code == 202
    assert Worker(env.db, enable_models=False).step()
    run = respond(env, client, path)
    with env.db.transaction() as session:
        op = session.get(ModelOperation, run['coaching']['operation_id'])
        assert op.request['context_sources'] == []
    publish(env, prompt_version='probability.followup.v3', account_daily_calls=0, site_daily_calls=0)
    assert submit(client, path, 'refine', request_model=True, key='after-config').status_code == 202
    assert Worker(env.db, enable_models=False).step()
    run = respond(env, client, path)
    with env.db.transaction() as session:
        op = session.get(ModelOperation, run['coaching']['operation_id'])
        assert op.request['context_sources'] == []
