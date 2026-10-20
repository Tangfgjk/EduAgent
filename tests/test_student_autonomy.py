from app.worker import Worker
from tests.test_s02 import database, env
from tests.test_s09 import decide
from tests.test_s05 import setup, send


def test_private_strategy_uses_builtin_reference_without_admin_or_upload(env):
    client, _ = env.student()
    wid = env.workspace(client)['workspace_id']
    url = f'/api/v1/student/workspaces/{wid}/strategies'
    body = {'daily_minutes': 15, 'reflection_focus': 'verification',
            'learning_experience': '我希望先自己检查，再请求提示', 'self_service': True}
    response = client.post(url, headers={'Idempotency-Key': 'self-service'}, json=body)
    assert response.status_code == 202, response.text
    assert Worker(env.db).step()
    path = url + '/' + response.json()['run_id']
    run = client.get(path).json()
    assert run['view']['phase'] == 'student_confirm'
    assert run['bundle']['materials'] == []
    assert run['bundle']['default_reference']['authorization'] == 'project_authored'
    assert run['bundle']['release_decision']['kind'] == 'system_auto'
    assert not run['bundle']['release_decision']['learning_effect_validated']
    other, _ = env.student('other.student')
    assert other.get(path).status_code == 404
    result = decide(env, client, path, 'confirm')
    assert result['view']['phase'] == 'approved'
    active = client.get(f'/api/v1/student/workspaces/{wid}/active-strategy').json()
    assert active['effective'] and active['policy']['reflection_focus'] == 'verification'
    retry = client.post(url, headers={'Idempotency-Key': 'self-service'}, json=body)
    assert retry.status_code == 202 and retry.json()['run_id'] == run['run_id']


def test_chinese_answers_and_clarification_are_persisted_through_tutor_api(env):
    client, _, path = setup(env)
    uncertain = send(env, client, path, 'answer', answer='答案是3或5')
    assert uncertain['view']['feedback']['status'] == 'unverifiable'
    assert uncertain['view']['can_answer']
    clarified = send(env, client, path, 'answer', answer='我的答案是3，因为先移项再计算')
    assert clarified['view']['feedback']['status'] == 'passed'
    assert clarified['view']['feedback']['interpretation']['candidate'] == '3'
    assert len(clarified['view']['records']) == 1
    history = client.get(path + '/history').json()['items']
    assert [r['numeric_status'] for r in history if r['answer']] == ['unverifiable', 'passed']
    assert history[-1]['answer'] == '我的答案是3，因为先移项再计算'
