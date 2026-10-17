from sqlalchemy import select

from app.infrastructure.db.models import LearningInput, OutboxJob, now
from app.infrastructure.runtime.model_tasks import ModelTaskRunner
from tests.test_s02 import database, env
from tests.test_model_runtime import setup, ready, submit
from tests.test_s05 import send


def test_history_keeps_own_words_presets_historical_model_and_numeric_results(env):
    client, path, _ = setup(env, prompt_version='probability.followup.v2')
    first = ready(env, client, path, text='第一轮自己的思路', key='first')
    assert ModelTaskRunner(env.db).step(operation_id=first['coaching']['operation_id'])
    second = ready(env, client, path, text='我改口后的第二轮思路', key='second')
    assert ModelTaskRunner(env.db).step(operation_id=second['coaching']['operation_id'])
    send(env, client, path, 'answer', answer='1/2', reasoning='逐个计数后构造比例')
    send(env, client, path, 'reflect', reflection='下次先说明等可能条件')
    assert client.get(path).json()['view']['task']['task_id'] == 'P-PRB-02'
    response = client.get(path + '/history')
    assert response.status_code == 200, response.text
    rows = response.json()['items']
    assert rows[0]['action'] == 'start'
    coaching = [r for r in rows if r['model']]
    assert [r['thinking'] for r in coaching] == ['第一轮自己的思路', '我改口后的第二轮思路']
    assert all(r['preset_hint'] and r['model']['result'] and r['task_id'] == 'P-PRB-01' for r in coaching)
    assert next(r for r in rows if r['answer'] == '1/2')['numeric_status'] == 'passed'
    assert any(r['reflection'] == '下次先说明等可能条件' for r in rows)
    assert 'generation' not in response.text and 'task_snapshot' not in response.text
    assert 'C-PRB-01' not in response.text and 'C-PRB-02' not in response.text
    # Pagination uses accepted revisions, not timestamps or UUID ordering.
    collected, after = [], 0
    while True:
        page = client.get(path + f'/history?limit=2&after={after}').json()
        collected.extend(page['items'])
        if not page['has_more']:
            break
        assert page['cursor'] > after
        after = page['cursor']
    assert collected == rows and len({r['sequence'] for r in rows}) == len(rows)


def test_history_pending_cancelled_and_access_boundaries(env):
    client, path, _ = setup(env)
    operation = ready(env, client, path, text='尚未完成的模型输入')['coaching']['operation_id']
    assert client.post(path + '/model-operations/' + operation + '/cancel').status_code == 200
    row = client.get(path + '/history').json()['items'][-1]
    assert row['model']['status'] == 'cancelled' and row['model']['result'] is None
    assert row['numeric_status'] is None
    other, _ = env.student('student.other')
    assert other.get(path + '/history').status_code == 404
    assert env.admin.get(path + '/history').status_code == 403
    other_space = env.workspace(client, 'temporary')['workspace_id']
    old_space = client.get(path).json()['workspace_id']
    assert client.get(path.replace(old_space, other_space) + '/history').status_code == 404
    assert client.get(path + '/history?limit=51').status_code == 422
    assert client.get(path + '/history?after=-1').status_code == 422


def test_queue_wait_retry_and_lease_recovery_are_explicit(env):
    client, path, _ = setup(env)
    assert submit(client, path).status_code == 202
    run = client.get(path).json()
    assert run['queue']['state'] == 'waiting'
    assert run['queue']['attempts'] == 0
    with env.db.transaction() as session:
        entry = session.scalar(select(LearningInput).where(LearningInput.conversation_id == run['conversation_id'])
            .order_by(LearningInput.payload['expected_revision'].as_integer().desc().nulls_last()).limit(1))
        job = session.scalar(select(OutboxJob).where(OutboxJob.input_id == entry.id))
        job_id = job.id
        job.status, job.attempts, job.available_at, job.error_code = 'pending', 1, now() + 10, 'runtime_error'
    assert client.get(path).json()['queue']['state'] == 'retrying'
    assert client.get(path).json()['queue']['retry_after_seconds'] > 0
    with env.db.transaction() as session:
        job = session.get(OutboxJob, job_id)
        job.status, job.lease_until = 'leased', now() - 1
    queue = client.get(path).json()['queue']
    assert queue['state'] == 'recovering'
    assert 'lease_token' not in queue and 'input_id' not in queue
