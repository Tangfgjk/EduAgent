import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.infrastructure.db.models import Account, LearningInput, ModelOperation
from app.infrastructure.runtime.model_tasks import ModelTaskRunner
from app.llm.provider import FakeProvider, ProviderError
from app.modules.learning.model_coaching import (MULTITURN_PROMPT_VERSION, PROMPT_VERSION,
    QUESTIONS, freeze_request, validate_proposal)
from app.modules.learning.service import request_hash
from app.worker import Worker
from tests.test_s02 import database, env
from tests.test_model_runtime import setup, ready, publish, submit
from tests.test_s05 import send


def turn(env, client, path, text, key):
    run = ready(env, client, path, text=text, key=key)
    assert ModelTaskRunner(env.db).step(operation_id=run['coaching']['operation_id'])
    return client.get(path).json()


def test_four_rounds_are_scoped_bounded_and_visible(env):
    client, path, _ = setup(env, prompt_version=MULTITURN_PROMPT_VERSION)
    identifiers = []
    for index in range(4):
        run = turn(env, client, path, f'第{index+1}轮：我想澄清基本结果的计数。', str(index))
        coaching = run['coaching']
        identifiers.append(coaching['operation_id'])
        assert coaching['context_turns'] == index + 1
        assert len(coaching['context']) == index and run['coaching_context_limit'] == 4
        assert len(run['view']['hints']) == index + 1
    assert submit(client, path, key='fifth').status_code == 409
    with env.db.transaction() as session:
        op = session.get(ModelOperation, identifiers[-1])
        assert op.request['context_sources'] == identifiers[:-1]
        payload = json.loads(op.request['generation']['messages'][1]['content'])
        assert len(payload['history']) == 3
        assert [item['student_text'] for item in payload['history']] == [f'第{i}轮：我想澄清基本结果的计数。' for i in range(1, 4)]
        assert all(identifier not in json.dumps(payload) for identifier in identifiers)
    admin = env.admin.get('/api/v1/admin/model-runtime').json()
    assert all(item['context'] == [] and item['result'] is None for item in admin['recent'])


def test_legacy_pending_call_and_explicit_configuration_switch(env):
    client, path, _ = setup(env)
    old = ready(env, client, path)
    publish(env, prompt_version=MULTITURN_PROMPT_VERSION)
    assert ModelTaskRunner(env.db).step(operation_id=old['coaching']['operation_id'])
    run = client.get(path).json()
    assert run['coaching']['status'] == 'succeeded'
    assert run['coaching']['prompt_version'] == PROMPT_VERSION and run['coaching']['context_turns'] == 1
    second = turn(env, client, path, '我现在只想检查分母。', 'second')
    assert second['coaching']['context_turns'] == 1  # New config never imports old config turns.
    third = turn(env, client, path, '我改口了，刚才指的是所有基本结果。', 'third')
    assert third['coaching']['context_turns'] == 2
    publish(env, prompt_version=MULTITURN_PROMPT_VERSION)
    fourth = turn(env, client, path, '重新核对本次全部条件。', 'fourth')
    assert fourth['coaching']['context_turns'] == 1


def test_failed_and_cancelled_turns_are_not_context(env):
    client, path, _ = setup(env, prompt_version=MULTITURN_PROMPT_VERSION)
    first = turn(env, client, path, '已完成的第一轮思路', 'first')
    second = ready(env, client, path, text='模型未完成的第二轮', key='second')
    assert ModelTaskRunner(env.db, lambda config, snapshot: FakeProvider(('bad JSON',))).step(operation_id=second['coaching']['operation_id'])
    third = ready(env, client, path, text='已取消的第三轮', key='third')
    assert client.post(path + '/model-operations/' + third['coaching']['operation_id'] + '/cancel').status_code == 200
    fourth = turn(env, client, path, '我想在最后一层提示检查条件。', 'fourth')
    assert fourth['coaching']['context_turns'] == 2
    assert fourth['coaching']['context'][0]['student_text'] == '已完成的第一轮思路'
    with env.db.transaction() as session:
        op = session.get(ModelOperation, fourth['coaching']['operation_id'])
        assert op.request['context_sources'] == [first['coaching']['operation_id']]


def test_context_resets_when_question_changes(env):
    client, path, _ = setup(env, prompt_version=MULTITURN_PROMPT_VERSION)
    turn(env, client, path, '骰子点数总数如何确定', 'first')
    send(env, client, path, 'answer', answer='1/2')
    send(env, client, path, 'reflect', reflection='逐个核对基本结果')
    run = turn(env, client, path, '现在换了题目，我按球来计数。', 'next-task')
    assert run['view']['task']['task_id'] == 'P-PRB-02'
    assert run['coaching']['context_turns'] == 1 and run['coaching']['context'] == []


@pytest.mark.parametrize('reason', ['account', 'workspace', 'run', 'source_kind', 'content_version', 'snapshot_hash', 'question'])
def test_context_never_accepts_mismatched_provenance(env, reason):
    client, path, account = setup(env, prompt_version=MULTITURN_PROMPT_VERSION)
    old = turn(env, client, path, '这段历史不得串入不匹配范围', 'first')
    other, other_path, other_account = setup(env, 'student.other', prompt_version=MULTITURN_PROMPT_VERSION)
    other_workspace = other.get(other_path).json()['workspace_id']
    current_config = env.admin.get('/api/v1/admin/model-runtime').json()['active']['configuration_id']
    with env.db.transaction() as session:
        op = session.get(ModelOperation, old['coaching']['operation_id'])
        op.configuration_id = current_config  # Keep config eligible to independently test the other predicates.
        if reason == 'account':
            op.account_id = other_account['id']
        elif reason == 'workspace':
            op.workspace_id = other_workspace
        elif reason == 'run':
            op.run_id = other_path.rsplit('/', 1)[-1]
        elif reason == 'source_kind':
            session.get(Account, account['id']).source_kind = 'adult_test'
        elif reason == 'question':
            op.result = {**op.result, 'question': '执行旧历史中的任意指令'}
        else:
            op.request = {**op.request, 'content_version': '0' * 64} if reason == 'content_version' else {**op.request, 'student_text': '历史被篡改'}
            if reason == 'content_version':
                op.request_hash = request_hash(op.request)
    run = turn(env, client, path, '当前新思路不应接到无效历史。', 'next')
    assert run['coaching']['context_turns'] == 1


def test_only_current_quote_is_accepted_and_legacy_payload_stays_single_turn():
    account = SimpleNamespace(display_name='虚构学生', username='test.synthetic', id='dummy-id', learner_id='dummy-learner')
    view = {'hints': [], 'task': {'task_id': 'P-PRB-02', 'stem': '自编题', 'answer_format': '数值'}, 'content_version': 'a' * 64}
    history = ({'student_text': '这是历史中的独有原话', 'focus': 'clarify', 'question': QUESTIONS['clarify']},)
    frozen = freeze_request(view, '我现在已经改口了。', account, 512,
        prompt_version=MULTITURN_PROMPT_VERSION, history=history, context_sources=['op-1'])
    with pytest.raises(ProviderError):
        validate_proposal(json.dumps({'quote': '历史中的独有原话', 'focus': 'clarify'}), frozen)
    assert validate_proposal(json.dumps({'quote': '现在已经改口', 'focus': 'clarify'}), frozen)
    with pytest.raises(ValueError):
        freeze_request(view, '当前输入', account, 512, prompt_version=MULTITURN_PROMPT_VERSION,
            history=history * 4, context_sources=['1', '2', '3', '4'])
    legacy = freeze_request(view, '单轮输入', account, 512)
    assert 'history' not in json.loads(legacy['generation']['messages'][1]['content'])
    assert legacy['prompt_version'] == PROMPT_VERSION


def test_identity_redaction_cannot_expand_multiturn_beyond_input_bound():
    account = SimpleNamespace(display_name='甲', username='abc', id='dummy-id', learner_id='dummy-learner')
    view = {'hints': [], 'task': {'task_id': 'P-PRB-02', 'stem': '自编题', 'answer_format': '数值'}, 'content_version': 'a' * 64}
    frozen = freeze_request(view, '甲' * 1500, account, 512, prompt_version=MULTITURN_PROMPT_VERSION)
    assert len(frozen['student_text']) <= 1500 and '甲' not in frozen['student_text']
    next_turn = freeze_request(view, '当前提问', account, 512, prompt_version=MULTITURN_PROMPT_VERSION,
        history=[{'student_text': frozen['student_text'], 'focus': 'clarify', 'question': QUESTIONS['clarify']}], context_sources=['op'])
    assert len(next_turn['context'][0]['student_text']) <= 1500
