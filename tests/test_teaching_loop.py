"""Observable adaptation and durable action/outcome links, without model calls."""
from copy import deepcopy
from uuid import uuid4

import pytest

from app.modules.learning.content_catalog import get_pack
from app.modules.learning.tutor_graph import advance_tutor
from app.modules.learning.teaching_loop import signals
from tests.test_s02 import database, env


def practice():
    pack = get_pack('math.g7.rational.dev1')
    rid = str(uuid4())
    state, image, _ = advance_tutor(rid, 'synthetic', {}, None, item=pack)

    def send(action, **fields):
        nonlocal state, image
        state, image, _ = advance_tutor(rid, 'synthetic', {'action': action, **fields}, deepcopy(image), item=pack)
        return state['view']

    send('answer', answer=pack.tasks[0]['answer'])
    send('next')
    return pack, send


@pytest.mark.parametrize('reasoning,expected', [('1+1=3', 'restore'), ('1+1=2', 'fade'), ('', 'fade')])
def test_same_numeric_answer_gets_evidence_based_support(reasoning, expected):
    pack, send = practice()
    view = send('answer', answer=pack.tasks[1]['answer'], reasoning=reasoning)
    assert view['feedback']['status'] == 'passed'
    if expected == 'restore':
        assert '算术等式' in view['reflection_prompt']
    view = send('reflect', reflection='记录我的方法与待检查之处')
    trace = view['teaching_history'][-1]
    assert trace['source'] == 'local' and trace['support'] == expected
    assert trace['execution']['next_task_id'] == pack.tasks[2]['id']
    assert trace['outcome'] is None
    assert ('理由中发现' in trace['reason']) == (expected == 'restore')
    assert view['support_decision']['mode'] == ('restored_step_support' if expected == 'restore' else 'socratic_first')


def test_stale_model_fade_is_rechecked_after_wrong_attempt_and_tracked_to_outcome():
    pack, send = practice()
    send('answer', answer='99999')
    send('answer', answer=pack.tasks[1]['answer'])
    proposal = {'action': 'practice', 'support': 'fade', 'reason': '先独立尝试下一道练习'}
    view = send('reflect', reflection='修正刚才的计算', teaching_decision=proposal)
    trace = view['teaching_history'][0]
    assert trace['source'] == 'model' and trace['support'] == 'restore'
    assert trace['observation']['attempts'] == 2
    assert proposal['support'] == 'fade'  # stored input/proposal is not mutated
    send('hint')
    view = send('answer', answer=pack.tasks[2]['answer'], reasoning='1+1=2')
    assert view['teaching_history'][0]['outcome']['hints_used'] == 1
    view = send('reflect', reflection='使用提示之后核对了依据')
    assert view['teaching_history'][0]['outcome']['reflection'] == '使用提示之后核对了依据'
    assert view['phase'] == 'checkpoint' and not view['can_hint']
    assert view['teaching_history'][-1]['execution']['support_applied'] is False
    for task in [t for t in pack.tasks if t['stage'] == 'checkpoint']:
        send('answer', answer=task['answer'])
        view = send('next')
    assert view['phase'] == 'summary'
    assert view['teaching_history'][-1]['outcome']['stage'] == 'checkpoint'
    assert [r['task_id'] for r in view['records']] == [t['id'] for t in pack.tasks]


def test_old_success_does_not_mask_more_recent_difficulty_or_reasoning_conflict():
    records = [{'stage': 'practice', 'status': 'passed', 'attempts': 1, 'hints_used': 0},
               {'stage': 'practice', 'status': 'passed', 'attempts': 2, 'hints_used': 0}]
    assert not signals(records, adaptive=True)['can_fade']
    feedback = {'status': 'passed', 'interpretation': {'reasoning_check': {'status': 'contradiction_found'}}}
    result = signals(records[:1], feedback, adaptive=True)
    assert result['needs_support'] and not result['can_fade']
    assert not signals(records[:1], adaptive=True, runtime={'execution': {'support_mode': 'step_first'}})['can_fade']


def test_trace_survives_worker_restart_and_duplicate_input_without_model(env):
    from tests.test_s05 import setup, to_practice, send
    from app.worker import Worker
    client, wid, path = setup(env)
    to_practice(env, client, path)
    run = send(env, client, path, 'answer', answer='6', reasoning='1+1=3')
    payload = {'action': 'reflect', 'reflection': '我需要核对理由中的等式',
               'expected_revision': run['revision'], 'gate_id': run['gate_id']}
    for _ in range(2):
        response = client.post(path + '/inputs', headers={'Idempotency-Key': 'reflect-once'}, json=payload)
        assert response.status_code == 202, response.text
    assert Worker(env.db, enable_models=False).step()
    view = client.get(path).json()['view']
    assert len(view['teaching_history']) == 1
    assert view['teaching_history'][0]['support'] == 'restore'
    send(env, client, path, 'answer', answer='7')
    view = client.get(path).json()['view']
    assert view['teaching_history'][0]['outcome']['status'] == 'passed'
    assert view['teaching_history'][0]['observation']['reasoning_status'] == 'contradiction_found'
    history = client.get(path + '/history').json()['items']
    assert next(row for row in history if row.get('reasoning') == '1+1=3')['reasoning_status'] == 'contradiction_found'
    other, _ = env.student('trace.other')
    assert other.get(path).status_code == 404
