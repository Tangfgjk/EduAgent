"""开发闭环、安全数值验证、内容治理与 30 个核心多轮场景。"""
from itertools import product
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.infrastructure.db.models import Account, AgentCheckpoint, AgentRun, AnswerReview, LearningInput, OutboxJob
from app.infrastructure.db.maintenance import backup, restore
from app.modules.learning.development_content import CONTENT_VERSION, TASKS, UNIT_ID
from app.modules.learning.numeric_verifier import verify
from app.modules.learning.tutor_graph import advance_tutor
from app.worker import Worker
from tests.test_s02 import database, env, recovery_database
from tests.test_s04 import start as start_plan, decision as plan_decision


def enable(env, enabled=True):
    content = env.admin.get('/api/v1/admin/content').json()
    response = env.admin.post('/api/v1/admin/content/development-release', json={
        'content_version': content['content_version'], 'expected_revision': content['revision'],
        'enabled': enabled, 'acknowledge_development_only': True})
    assert response.status_code == 200, response.text
    return response.json()


def setup(env, kind='temporary'):
    student, _ = env.student()
    wid = env.workspace(student, kind)['workspace_id']
    enable(env)
    if kind == 'project':
        plan = start_plan(env, student, wid)
        plan_path = f'/api/v1/student/workspaces/{wid}/runs/{plan["run_id"]}'
        Worker(env.db).step()
        waiting = student.get(plan_path).json()
        assert student.post(plan_path + '/resume', headers={'Idempotency-Key': 'confirm-plan'}, json=plan_decision(waiting)).status_code == 202
        Worker(env.db).step()
    response = student.post(f'/api/v1/student/workspaces/{wid}/tutoring',
        headers={'Idempotency-Key': 'start-tutor'}, json={'unit_id': UNIT_ID})
    assert response.status_code == 202, response.text
    path = f'/api/v1/student/workspaces/{wid}/tutoring/{response.json()["run_id"]}'
    assert Worker(env.db).step()
    return student, wid, path


def send(env, client, path, action, **fields):
    run = client.get(path).json()
    response = client.post(path + '/inputs', headers={'Idempotency-Key': str(uuid4())}, json={
        'action': action, 'gate_id': run['gate_id'], 'expected_revision': run['revision'], **fields})
    assert response.status_code == 202, response.text
    assert Worker(env.db).step()
    return client.get(path).json()


def to_practice(env, client, path):
    send(env, client, path, 'answer', answer='6/2', reasoning='两边减5，再除以3')
    return send(env, client, path, 'next')


@pytest.mark.parametrize('answer,expected,status', [
    ('x=3', '3', 'passed'), (' X＝6÷2 ', '3', 'passed'), ('(1+2)*2/2', '3', 'passed'),
    ('0.1+0.2', '3/10', 'passed'), ('-6/-2', '3', 'passed'), ('3.1', '3', 'failed'),
    ('我不会', '3', 'unverifiable'), ('x=0=3', '3', 'unverifiable'),
    ('y=3', '3', 'unverifiable'), ('sqrt(9)', '3', 'unverifiable'), ('1/0', '3', 'unverifiable'),
    ('2**999999999', '3', 'unverifiable'), ("__import__('os').system('echo bad')", '3', 'unverifiable'),
    ('9'*100, '3', 'unverifiable'), ('('*65+'3'+')'*65, '3', 'unverifiable'),
])
def test_bounded_numeric_verifier(answer, expected, status):
    assert verify(answer, expected) == status


SCENARIOS = list(product(['3', '0', '暂时不会'], ['independent', 'one_hint', 'three_hints', 'full_ladder', 'skip'], ['correct', 'one_wrong']))


@pytest.mark.parametrize('diagnosis,support,checkpoint', SCENARIOS)
def test_thirty_multiturn_scenarios(diagnosis, support, checkpoint):
    state, image, paused = advance_tutor(str(uuid4()), 'synthetic-learner', {}, None)
    rid = state['run_id']
    def step(action, **fields):
        nonlocal state, image, paused
        old_gate = state['gate_id']
        state, image, paused = advance_tutor(rid, 'synthetic-learner', {'action': action, **fields}, image)
        assert state['gate_id'] != old_gate
        return state['view']
    step('answer', answer=diagnosis)
    step('next')
    for answer in ['6', '7']:
        hints = {'independent': 0, 'one_hint': 1, 'three_hints': 3, 'full_ladder': 4, 'skip': 0}[support]
        for _ in range(hints): step('hint')
        if support == 'skip':
            step('answer', answer='尚未理解')
            step('skip')
        else:
            step('answer', answer='0')
            step('answer', answer=answer)
        assert state['view']['phase'] == 'reflection'
        step('reflect', reflection='等式两边做相同运算，再代入检查')
    assert state['view']['phase'] == 'checkpoint'
    assert not state['view']['can_hint']
    step('answer', answer='0' if checkpoint == 'one_wrong' else '5')
    step('next')
    step('answer', answer='6')
    step('next')
    assert not paused and state['view']['phase'] == 'summary'
    records = state['view']['records']
    assert len(records) == 5
    assert all(record['hints_used'] == 0 for record in records if record['stage'] != 'practice')
    assert sum(record['status'] == 'passed' for record in records if record['stage'] == 'checkpoint') == (1 if checkpoint == 'one_wrong' else 2)


def test_project_plan_gate_and_content_version_gate(env):
    student, _ = env.student()
    wid = env.workspace(student)['workspace_id']
    base = f'/api/v1/student/workspaces/{wid}/tutoring'
    assert student.post(base, headers={'Idempotency-Key': 'a'}, json={'unit_id': UNIT_ID}).json()['error']['code'] == 'content_not_enabled'
    content = enable(env)
    assert student.post(base, headers={'Idempotency-Key': 'b'}, json={'unit_id': UNIT_ID}).json()['error']['code'] == 'plan_required'
    assert student.get('/api/v1/student/content').json()['tasks'] == []
    assert student.get('/api/v1/admin/content').status_code == 403
    assert env.admin.post('/api/v1/admin/content/development-release', json={
        'content_version': '0'*64, 'expected_revision': content['revision'], 'enabled': True,
        'acknowledge_development_only': True}).status_code == 409


def test_full_unit_real_api_and_independent_check(env):
    client, wid, path = setup(env, 'project')
    run = client.get(path).json()
    assert 'answer' not in run['view']['task'] and run['view']['hints'] == []
    assert client.post(path+'/inputs', headers={'Idempotency-Key': 'no-hint'}, json={
        'action': 'hint', 'gate_id': run['gate_id'], 'expected_revision': run['revision']}).json()['error']['code'] == 'independent_check'
    to_practice(env, client, path)
    for expected in ['6', '7']:
        for _ in range(4): run = send(env, client, path, 'hint')
        assert len(run['view']['hints']) == 4
        assert not run['view']['can_hint']
        send(env, client, path, 'answer', answer=expected)
        send(env, client, path, 'reflect', reflection='我先列式，再用等式性质计算和验算')
    for expected in ['5', '6']:
        waiting = client.get(path).json()
        assert waiting['view']['phase'] == 'checkpoint' and not waiting['view']['can_hint']
        assert client.post(path+'/inputs', headers={'Idempotency-Key': str(uuid4())}, json={
            'action': 'hint', 'gate_id': waiting['gate_id'], 'expected_revision': waiting['revision']}).status_code == 409
        run = send(env, client, path, 'answer', answer=expected)
        assert not run['view']['can_answer']
        run = send(env, client, path, 'next')
    assert run['status'] == 'completed' and run['view']['phase'] == 'summary'
    events = client.get(path+'/event-history').json()
    assert events[-1]['kind'] == 'tutor.completed'
    assert client.get(path+'/events', headers={'Last-Event-ID': str(events[-2]['seq'])}).text.count('data: ') == 1
    # 两类运行接口不能混用；计划列表也不包含 TutorGraph。
    assert client.get(path.replace('/tutoring/', '/runs/')).status_code == 404
    assert len(client.get(f'/api/v1/student/workspaces/{wid}/runs').json()['items']) == 1


def test_unknown_answer_real_review_and_no_fake_success(env):
    client, _, path = setup(env)
    run = send(env, client, path, 'answer', answer='我用画图理解，暂时没写数值', reasoning='我想把两边看成天平')
    assert run['view']['feedback']['status'] == 'unverifiable' and run['view']['pending_reviews'] == 1
    review = env.admin.get('/api/v1/admin/answer-reviews').json()['items'][0]
    assert review['source_kind'] == 'synthetic' and review['task']['answer'] == '3'
    assert client.get('/api/v1/admin/answer-reviews').status_code == 403
    body = {'expected_revision': review['revision'], 'conclusion': 'unresolved', 'note': '需补充清晰的数学表达，不能仅凭这段文字判对错'}
    response = env.admin.post(f'/api/v1/admin/answer-reviews/{review["review_id"]}/resolve', json=body)
    assert response.status_code == 200
    assert env.admin.post(f'/api/v1/admin/answer-reviews/{review["review_id"]}/resolve', json=body).status_code == 409
    current = client.get(path).json()
    assert current['view']['pending_reviews'] == 0
    assert current['view']['records'][0]['status'] == 'unverifiable'


def test_scope_idempotency_and_old_gate(env):
    client, wid, path = setup(env)
    run = client.get(path).json()
    body = {'action': 'answer', 'answer': '3', 'gate_id': run['gate_id'], 'expected_revision': run['revision']}
    for _ in range(2):
        assert client.post(path+'/inputs', headers={'Idempotency-Key': 'same-input'}, json=body).status_code == 202
    Worker(env.db).step()
    assert client.post(path+'/inputs', headers={'Idempotency-Key': 'same-input'}, json=body).status_code == 202
    assert client.post(path+'/inputs', headers={'Idempotency-Key': 'new-key-old-gate'}, json=body).status_code == 409
    assert client.post(path+'/inputs', headers={'Idempotency-Key': 'same-input'}, json={**body, 'answer':'8'}).status_code == 409
    two, _ = env.student('student.two')
    assert two.get(path).status_code == 404
    other = env.workspace(client)['workspace_id']
    assert client.get(path.replace(wid, other)).status_code == 404
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(LearningInput)) == 2


@pytest.mark.parametrize('boundary', ['disabled_content', 'real_student', 'archived', 'cancelled'])
def test_runtime_rechecks_authorization_and_content(env, boundary):
    client, wid, path = setup(env)
    run = client.get(path).json()
    assert client.post(path+'/inputs', headers={'Idempotency-Key':'pending'}, json={
        'action':'answer', 'answer':'3', 'gate_id':run['gate_id'], 'expected_revision':run['revision']}).status_code == 202
    if boundary == 'disabled_content': enable(env, False)
    elif boundary == 'real_student':
        with env.db.transaction() as session:
            session.scalar(select(Account).where(Account.role == 'student')).source_kind = 'real_student'
    elif boundary == 'archived': client.post(f'/api/v1/student/workspaces/{wid}/archive', json={'expected_revision':1})
    else:
        pending = client.get(path).json()
        assert client.post(path+'/cancel', json={'expected_revision':pending['revision']}).status_code == 200
    Worker(env.db).step()
    assert client.get(path).json()['status'] == ('cancelled' if boundary == 'cancelled' else 'failed')


def test_backup_restore_resumes_tutor(env, tmp_path):
    from app.modules.identity.service import Actor
    from app.modules.learning.tutor_service import Tutoring
    client, wid, path = setup(env)
    to_practice(env, client, path)
    run = send(env, client, path, 'hint')
    file = tmp_path / 's05.json'
    backup(env.db, file)
    with recovery_database(env.db, tmp_path) as recovered:
        restore(recovered, file)
        with recovered.transaction() as session:
            actor = Actor(session.scalar(select(Account).where(Account.role == 'student')), 'restore-test')
            Tutoring().submit(session, actor, wid, run['run_id'], {'action':'answer', 'answer':'6',
                'gate_id':run['gate_id'], 'expected_revision':run['revision']}, 'restore-answer')
        Worker(recovered).step()
        with recovered.transaction() as session:
            saved = session.get(AgentRun, run['run_id'])
            assert saved.view['phase'] == 'reflection' and len(saved.view['hints']) == 1


def test_real_student_cannot_start_development_content(env):
    client, _ = env.student()
    enable(env)
    wid = env.workspace(client, 'temporary')['workspace_id']
    with env.db.transaction() as session:
        session.scalar(select(Account).where(Account.role == 'student')).source_kind = 'real_student'
    response = client.post(f'/api/v1/student/workspaces/{wid}/tutoring',
        headers={'Idempotency-Key': 'real-student'}, json={'unit_id': UNIT_ID})
    assert response.status_code == 409 and response.json()['error']['code'] == 'development_content_only'


def test_review_checkpoint_and_feedback_commit_together(env, monkeypatch):
    import app.worker as module
    client, _, path = setup(env)
    original = module.event
    def crash(session, run, kind):
        if kind == 'tutor.answer':
            raise RuntimeError('模拟事件写入前崩溃')
        original(session, run, kind)
    monkeypatch.setattr(module, 'event', crash)
    waiting = client.get(path).json()
    accepted = client.post(path+'/inputs', headers={'Idempotency-Key':'unknown-crash'}, json={
        'action':'answer', 'answer':'无法表达', 'expected_revision':waiting['revision'], 'gate_id':waiting['gate_id']})
    assert accepted.status_code == 202
    Worker(env.db).step()
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(AnswerReview)) == 0
        job = session.scalar(select(OutboxJob).where(OutboxJob.status == 'pending'))
        assert job is not None
        job.available_at = 0
    monkeypatch.setattr(module, 'event', original)
    Worker(env.db).step()
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(AnswerReview)) == 1
    assert client.get(path).json()['view']['feedback']['status'] == 'unverifiable'
