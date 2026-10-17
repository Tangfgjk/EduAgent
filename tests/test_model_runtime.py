"""Model task behavior through authenticated endpoints and injected providers; never real HTTP."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.infrastructure.db.models import Account, ModelDailyBudget, ModelOperation, ModelPolicy, now
from app.infrastructure.runtime.model_tasks import ModelTaskRunner, day_key
from app.llm.provider import FakeProvider, GenerationResult, ProviderError, Usage
from app.modules.learning.model_coaching import UNIT
from app.worker import Worker
from tests.test_s02 import database, env, recovery_database
from tests.test_s05 import send


def publish(env, **changes):
    state = env.admin.get('/api/v1/admin/model-runtime').json()
    response = env.admin.post('/api/v1/admin/model-runtime/publish', json={
        'expected_revision': state['revision'], 'vendor': 'fake', 'max_output_tokens': 512,
        'account_daily_calls': 30, 'site_daily_calls': 100,
        'acknowledge_development_only': True, **changes})
    assert response.status_code == 200, response.text
    return response.json()


def setup(env, name='student.one', **limits):
    client, account = env.student(name)
    wid = env.workspace(client, 'temporary')['workspace_id']
    item = next(x for x in env.admin.get('/api/v1/admin/content-catalog').json()['items'] if x['unit_id'] == UNIT)
    assert env.admin.post(f'/api/v1/admin/content-catalog/{UNIT}/development-release', json={
        'content_version': item['content_version'], 'expected_revision': item['revision'],
        'enabled': True, 'acknowledge_development_only': True}).status_code == 200
    publish(env, **limits)
    run = client.post(f'/api/v1/student/workspaces/{wid}/tutoring',
        headers={'Idempotency-Key': 'start'}, json={'unit_id': UNIT}).json()
    path = f'/api/v1/student/workspaces/{wid}/tutoring/{run["run_id"]}'
    Worker(env.db).step()
    send(env, client, path, 'answer', answer='1/6')
    send(env, client, path, 'next')
    return client, path, account


def submit(client, path, text='我认为两种颜色所以机会各一半', key='coach'):
    run = client.get(path).json()
    return client.post(path + '/inputs', headers={'Idempotency-Key': key}, json={
        'action': 'hint', 'coaching_text': text,
        'expected_revision': run['revision'], 'gate_id': run['gate_id']})


def ready(env, client, path, **changes):
    response = submit(client, path, **changes)
    assert response.status_code == 202, response.text
    assert response.json()['coaching']['status'] == 'waiting'
    Worker(env.db).step()  # Only short graph transaction; no provider yet.
    run = client.get(path).json()
    assert run['coaching']['status'] == 'pending'
    assert len(run['view']['hints']) >= 1
    return run


def test_end_to_end_fake_and_ledger(env):
    client, path, _ = setup(env)
    ready(env, client, path)
    assert Worker(env.db).step()
    run = client.get(path).json()
    assert run['coaching']['status'] == 'succeeded' and run['coaching']['simulated']
    assert run['coaching']['result']['quote'] == '我认为两种颜色所以机会各一半'
    assert run['coaching']['result']['question']
    assert len(run['view']['hints']) == 1
    state = env.admin.get('/api/v1/admin/model-runtime').json()
    assert state['reserved_calls'] == 1 and state['missing_usage_calls'] == 1
    assert state['recent'][0]['result'] is None  # Admin summary receives no student quote.
    assert state['input_tokens_known'] == 0  # Incomplete, not a zero-cost assertion.
    assert not Worker(env.db).step()  # No duplicate execution.


def test_idempotency_reserves_once(env):
    client, path, _ = setup(env)
    run = client.get(path).json()
    body = {'action': 'hint', 'coaching_text': '我按颜色分类', 'gate_id': run['gate_id'], 'expected_revision': run['revision']}
    for _ in range(2):
        assert client.post(path + '/inputs', headers={'Idempotency-Key': 'same'}, json=body).status_code == 202
    assert env.admin.get('/api/v1/admin/model-runtime').json()['reserved_calls'] == 1
    assert client.post(path + '/inputs', headers={'Idempotency-Key': 'same'}, json={**body, 'coaching_text': '不同内容'}).status_code == 409


def test_budget_exhaustion_keeps_standard_hint_available(env):
    client, path, _ = setup(env, account_daily_calls=1)
    assert client.get(path).json()['coaching_remaining_calls'] == 1
    ready(env, client, path)
    Worker(env.db).step()
    shown = client.get(path).json()
    assert shown['coaching_remaining_calls'] == 0 and not shown['coaching_available']
    denied = submit(client, path, key='second')
    assert denied.status_code == 429 and denied.json()['error']['code'] == 'model_budget_exhausted'
    assert len(send(env, client, path, 'hint')['view']['hints']) == 2
    assert env.admin.get('/api/v1/admin/model-runtime').json()['reserved_calls'] == 1


def test_global_budget_concurrent_accounts(env):
    one, path1, _ = setup(env, 'student.one', site_daily_calls=1)
    two, path2, _ = setup(env, 'student.two', site_daily_calls=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, one, path1), pool.submit(submit, two, path2)]
        assert sorted(f.result().status_code for f in futures) == [202, 429]
    assert env.admin.get('/api/v1/admin/model-runtime').json()['reserved_calls'] == 1


@pytest.mark.parametrize('change', ['cancel_task', 'cancel_run', 'advance', 'archive', 'disabled', 'pause', 'pause_republish', 'withdraw'])
def test_running_result_is_fenced(env, change):
    client, path, account = setup(env)
    run = ready(env, client, path)
    runner = ModelTaskRunner(env.db)
    claim = runner.claim()
    if change == 'cancel_task':
        assert client.post(path + '/model-operations/' + run['coaching']['operation_id'] + '/cancel').status_code == 200
    elif change == 'cancel_run':
        assert client.post(path + '/cancel', json={'expected_revision': run['revision']}).status_code == 200
    elif change == 'advance':
        send(env, client, path, 'answer', answer='0')
    elif change == 'archive':
        assert client.post(f'/api/v1/student/workspaces/{run["workspace_id"]}/archive', json={'expected_revision': 1}).status_code == 200
    elif change == 'disabled':
        env.admin.post(f'/api/v1/admin/students/{account["id"]}/disable')
    elif change in {'pause', 'pause_republish'}:
        state = env.admin.get('/api/v1/admin/model-runtime').json()
        env.admin.post('/api/v1/admin/model-runtime/pause', json={'expected_revision': state['revision']})
        if change == 'pause_republish':
            publish(env)
    else:
        item = next(x for x in env.admin.get('/api/v1/admin/content-catalog').json()['items'] if x['unit_id'] == UNIT)
        env.admin.post(f'/api/v1/admin/content-catalog/{UNIT}/development-release', json={
            'content_version': item['content_version'], 'expected_revision': item['revision'],
            'enabled': False, 'acknowledge_development_only': True})
    result = GenerationResult(provider='fake', requested_model='scripted', text=json.dumps({'quote': '两种颜色', 'focus': 'equal_likelihood'}),
        finish_reason='stop', elapsed_ms=10, simulated=True, usage=Usage(input_tokens=20, output_tokens=10))
    assert runner.complete(claim, result=result) is False
    with env.db.transaction() as session:
        op = session.get(ModelOperation, claim['id'])
        assert op.status in {'cancelled', 'discarded'} and op.result is None


def test_lease_renewal_recovery_and_no_reissue(env):
    client, path, _ = setup(env)
    ready(env, client, path)
    runner = ModelTaskRunner(env.db)
    claim = runner.claim()
    assert runner.heartbeat(claim)
    with env.db.transaction() as session:
        session.get(ModelOperation, claim['id']).lease_until = now() - 1
    runner.recover()
    assert runner.claim() is None
    assert not runner.complete(claim, error='timeout')
    run = client.get(path).json()
    assert run['coaching']['status'] == 'unknown' and run['coaching']['billing_unknown']


@pytest.mark.parametrize('response,code', [(ProviderError('timeout'), 'timeout'),
    ('not json', 'invalid_coaching_proposal'), ('{"quote":"不存在的原话","focus":"clarify"}', 'invalid_coaching_proposal'),
    ('{"quote":"颜色","focus":"ratio"}', 'invalid_coaching_proposal'),
    ('{"quote":"颜色","focus":"clarify","question":"直接给出完整答案"}', 'invalid_coaching_proposal')])
def test_failures_do_not_retry_or_replace_hint(env, response, code):
    client, path, _ = setup(env)
    ready(env, client, path)
    fake = FakeProvider((response,))
    runner = ModelTaskRunner(env.db, provider_factory=lambda config, snapshot: fake)
    assert runner.step() and not runner.step()
    run = client.get(path).json()
    assert run['coaching']['error_code'] == code and run['coaching']['result'] is None
    assert len(run['view']['hints']) == 1


def test_provider_runs_without_database_transaction_and_can_be_cancelled(env):
    client, path, _ = setup(env)
    run = ready(env, client, path)
    class WaitingProvider:
        cancelled = False
        async def generate(self, request):
            # A separate connection/transaction can write during the external wait.
            with env.db.transaction() as session:
                assert session.get(ModelPolicy, 1).active_id
            client.post(path + '/model-operations/' + run['coaching']['operation_id'] + '/cancel')
            try:
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
    provider = WaitingProvider()
    assert ModelTaskRunner(env.db, lambda config, snapshot: provider, heartbeat_seconds=0.01).step()
    assert provider.cancelled
    assert client.get(path).json()['coaching']['status'] == 'cancelled'


def test_config_freeze_access_limits_and_redaction(env, monkeypatch):
    client, path, account = setup(env)
    run = ready(env, client, path, text='测试学生 student.one 13812345678 a@example.com 我按颜色计算')
    old_id = run['coaching']['configuration_id']
    publish(env, max_output_tokens=64)
    runner = ModelTaskRunner(env.db)
    claim = runner.claim()
    assert claim['config'].id == old_id and claim['config'].max_output_tokens == 512
    serialized = json.dumps(claim['request'], ensure_ascii=False)
    assert all(value not in serialized for value in ('测试学生', 'student.one', '13812345678', 'a@example.com', account['id']))
    assert client.get('/api/v1/admin/model-runtime').status_code == 403
    other, _ = env.student('student.other')
    assert other.get(path).status_code == 404
    assert other.post(path + '/model-operations/' + run['coaching']['operation_id'] + '/cancel').status_code == 404
    state = env.admin.get('/api/v1/admin/model-runtime').json()
    assert env.admin.post('/api/v1/admin/model-runtime/publish', json={
        'expected_revision': state['revision'], 'vendor': 'fake', 'site_daily_calls': 101,
        'acknowledge_development_only': True}).status_code == 422


def test_independent_stages_and_real_students_cannot_call_model(env):
    client, path, account = setup(env)
    with env.db.transaction() as session:
        session.get(Account, account['id']).source_kind = 'real_student'
    assert submit(client, path).status_code in {403, 409}
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ModelOperation)) == 0


def test_checkpoint_blocks_model_even_when_published(env):
    client, path, _ = setup(env)
    # Practice -> reflection -> practice -> reflection -> checkpoint.
    send(env, client, path, 'answer', answer='1/2')
    send(env, client, path, 'reflect', reflection='我会检查基本结果')
    send(env, client, path, 'answer', answer='2/5')
    run = send(env, client, path, 'reflect', reflection='我会逐个球计数')
    assert run['view']['phase'] == 'checkpoint' and not run['coaching_available']
    assert submit(client, path).json()['error']['code'] == 'independent_check'


def test_backup_restore_model_result_and_budget(env, tmp_path):
    from app.infrastructure.db.maintenance import backup, restore
    client, path, _ = setup(env)
    ready(env, client, path)
    Worker(env.db).step()
    destination = tmp_path / 'model-backup.json'
    backup(env.db, destination)
    with recovery_database(env.db, tmp_path) as restored:
        restore(restored, destination)
        with restored.transaction() as session:
            assert session.scalar(select(ModelOperation)).status == 'succeeded'
            assert session.get(ModelDailyBudget, (day_key(), 'global')).calls == 1


def test_budget_resets_at_shanghai_midnight(env, monkeypatch):
    from datetime import datetime, timedelta
    from app.infrastructure.runtime import model_tasks
    client, path, _ = setup(env, account_daily_calls=1)
    ready(env, client, path)
    Worker(env.db).step()
    assert submit(client, path, key='before-midnight').status_code == 429
    tomorrow = datetime.now(model_tasks.BUSINESS_TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    monkeypatch.setattr(model_tasks, 'now', lambda: int(tomorrow.timestamp()))
    assert submit(client, path, key='after-midnight').status_code == 202
    state = env.admin.get('/api/v1/admin/model-runtime').json()
    assert state['budget_timezone'] == 'Asia/Shanghai'
    assert state['budget_day'] == tomorrow.strftime('%Y-%m-%d') and state['reserved_calls'] == 1


def test_published_configuration_immutable_and_no_browser_keys(env, monkeypatch):
    from sqlalchemy import update
    from sqlalchemy.exc import DBAPIError
    from app.infrastructure.db.models import ModelConfiguration
    from app.infrastructure.runtime import model_tasks
    publish(env)
    with pytest.raises(DBAPIError):
        with env.db.transaction() as session:
            session.execute(update(ModelConfiguration).values(model='tampered'))
    state = env.admin.get('/api/v1/admin/model-runtime').json()
    payload = {'expected_revision': state['revision'], 'vendor': 'deepseek', 'acknowledge_development_only': True}
    monkeypatch.setattr(model_tasks, 'model_key', lambda: '')
    assert env.admin.post('/api/v1/admin/model-runtime/publish', json=payload).json()['error']['code'] == 'model_key_missing'
    assert env.admin.post('/api/v1/admin/model-runtime/publish', json={**payload, 'api_key': 'test-secret'}).status_code == 422
    assert 'test-secret' not in env.admin.get('/api/v1/admin/model-runtime').text
