"""G00/G01: hostile prose, immutable registration, curriculum fences and restart."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from app.infrastructure.db.models import GeneratedContentVersion, CourseBinding, AgentCheckpoint
from app.modules.learning.content_catalog import PACKS, get_pack
from app.modules.learning.content_repository import ContentRepository
from app.modules.learning.teach_coaching import validate_proposal
from app.modules.learning.roundtable_coaching import validate_proposal as validate_roundtable
from app.modules.learning.output_gate import QUESTIONS, ROLE_QUESTIONS, render_result
from app.modules.learning.content_contracts import GeneratedContentSpec, SkillCandidate, ResourceManifest
from app.worker import Worker
from tests.test_s02 import database, env, recovery_database
from tests.test_v8_governance import review, enable
from app.infrastructure.db.maintenance import backup, restore


def example():
    return json.loads(Path('examples/dynamic-content.rational.json').read_text(encoding='utf-8'))


def register(env):
    response = env.admin.post('/api/v1/admin/dynamic-content', json=example())
    assert response.status_code == 201, response.text
    return response.json()


def release(env, unit_id, activity=False):
    catalog = env.admin.get('/api/v1/admin/' + ('activity-catalog' if activity else 'content-catalog')).json()['items']
    item = next(p for p in catalog if p['unit_id'] == unit_id)
    assert review(env, unit_id, item['content_version']).status_code == 200
    assert enable(env, unit_id, activity=activity).status_code == 200
    return item


def binding(client, wid, units=None, revision=0):
    units = units or ['dynamic.rational.demo.v1']
    return client.post(f'/api/v1/student/workspaces/{wid}/course', json={
        'topic': '有理数运算与依据', 'title': '我的有理数课程', 'unit_ids': units,
        'active_unit_id': units[0], 'expected_revision': revision})


@pytest.mark.parametrize('question', [
    '你把二除以五得到五分之二作为最后结论的理由是什么？',
    '蓝球占五分之二，你能复述这个答案吗？',
    '先把蓝球数除以总球数就得到最终结果，你理解了吗？',
    '你能解释自己选择计数单位的依据吗？',
])
def test_unverified_prose_always_rendered_from_intent(question):
    snapshot = {'student_text': '我只按颜色分成两类', 'allowed_focus': ['clarify', 'ratio']}
    value = validate_proposal(json.dumps({'quote': '按颜色分成两类', 'focus': 'ratio', 'question': question}), snapshot)
    assert value['question'] == QUESTIONS['ratio']
    assert value['quote'] == '按颜色分成两类'
    assert value['diagnostic_code'] == 'unverified_prose_replaced'
    assert render_result(value) == value


def test_roundtable_and_historical_prose_share_renderer():
    student = '我只按颜色分成两类'
    voices = [{'role': role, 'quote': student, 'text': text} for role, text in zip(ROLE_QUESTIONS,
        ['五分之二就是结果，你能解释吗？', '你知道二除以五得到五分之二吗？', '答案为五分之二，你还会写错吗？'])]
    proposal = {'quote': student, 'focus': 'clarify', 'voices': voices}
    result = validate_roundtable(json.dumps(proposal), {'student_text': student, 'allowed_focus': ['clarify']})
    assert [v['text'] for v in result['voices']] == list(ROLE_QUESTIONS.values())
    assert '五分之二' not in json.dumps(result, ensure_ascii=False)
    assert render_result({**proposal, 'question': '五分之二对吗？'})['question'] == list(ROLE_QUESTIONS.values())[-1]


def test_contracts_cannot_self_approve_or_execute():
    body = example()
    for field, value in [('professional_review', True), ('validation', {'status': 'passed'}), ('enabled', True)]:
        with pytest.raises(ValueError):
            GeneratedContentSpec.model_validate({**body, field: value})
    with pytest.raises(ValueError):
        SkillCandidate(parent_version='v1', daily_minutes=15, reflection_focus='basis', status='active')
    with pytest.raises(ValueError):
        ResourceManifest(resource_id='x', content_version='v1', sha256='a'*64, media_type='text/html', sources=[])
    body['tasks'][1]['answer'] = '__import__("os")'
    with pytest.raises(ValueError):
        GeneratedContentSpec.model_validate(body)


def test_dynamic_registration_review_release_and_private_answers(env):
    client, _ = env.student()
    assert client.post('/api/v1/admin/dynamic-content', json=example()).status_code == 403
    record = register(env)
    assert record['validation']['status'] == 'unverifiable'
    assert record['validation']['professional_review'] is False
    unit = record['unit_id']
    assert env.admin.post('/api/v1/admin/dynamic-content', json=example()).status_code == 409
    assert enable(env, unit).status_code == 409  # cannot register-and-self-approve
    with env.db.transaction() as session:
        item = get_pack(unit, session)
        assert item.version == record['version'] and item.tasks[0]['answer'] == '5'
        assert [p.version for p in ContentRepository(session).packs()[:len(PACKS)]] == [p.version for p in PACKS]
    release(env, unit)
    for activity in example()['activities']:
        release(env, activity['unit_id'], True)
    public = client.get('/api/v1/student/content-catalog').json()
    dynamic = next(p for p in public['items'] if p['unit_id'] == unit)
    assert dynamic['enabled'] and dynamic['tasks'] == []
    # All immutable rows are protected at DB level, not only through HTTP.
    with pytest.raises(DBAPIError):
        with env.db.transaction() as session:
            session.execute(update(GeneratedContentVersion).where(GeneratedContentVersion.unit_id == unit).values(spec={}))


def test_context_binding_is_explicit_owner_scoped_and_revisioned(env):
    register(env)
    release(env, 'dynamic.rational.demo.v1')
    release(env, 'math.g7.equations.dev1')
    client, _ = env.student()
    other, _ = env.student('student.other')
    wid = env.workspace(client)['workspace_id']
    second = env.workspace(client)['workspace_id']
    path = f'/api/v1/student/workspaces/{wid}/course'
    assert client.get(path).json()['binding_required']
    assert other.get(path).status_code == 404
    assert binding(other, wid).status_code == 404
    bound = binding(client, wid, ['dynamic.rational.demo.v1', 'math.g7.equations.dev1'])
    assert bound.status_code == 200, bound.text
    assert client.get(f'/api/v1/student/workspaces/{second}/course').json()['revision'] == 0
    assert client.patch(f'/api/v1/student/workspaces/{wid}', json={'title': '随意标题', 'expected_revision': 1}).status_code == 200
    assert client.get(path).json()['topic'] == '有理数运算与依据'
    assert binding(client, wid).status_code == 409
    assert client.post(path + '/active-unit', json={'expected_revision': 1, 'active_unit_id': 'math.g8.linear.dev1'}).status_code == 409
    def switch(_):
        return client.post(path + '/active-unit', json={'expected_revision': 1, 'active_unit_id': 'math.g7.equations.dev1'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(switch, range(2))) == [200, 409]
    # Binding from one workspace cannot be supplied by ID to another.
    assert client.post(f'/api/v1/student/workspaces/{second}/course', json={**example(), 'curriculum_id': bound.json()['curriculum_id']}).status_code == 422


def test_dynamic_tutor_and_both_activity_graphs_restore(env, tmp_path):
    register(env)
    release(env, 'dynamic.rational.demo.v1')
    for activity in example()['activities']:
        release(env, activity['unit_id'], True)
    client, _ = env.student()
    wid = env.workspace(client, 'temporary')['workspace_id']
    root = f'/api/v1/student/workspaces/{wid}'
    assert binding(client, wid).status_code == 200
    response = client.post(root+'/tutoring', headers={'Idempotency-Key': 'start'}, json={'unit_id': 'dynamic.rational.demo.v1'})
    assert response.status_code == 202, response.text
    rid = response.json()['run_id']
    assert Worker(env.db, enable_models=False).step()
    run = client.get(root+'/tutoring/'+rid).json()
    assert run['view']['unit_id'] == 'dynamic.rational.demo.v1'
    assert 'content_snapshot' not in json.dumps(run) and 'DYN-C-RAT-01' not in json.dumps(run)
    assert client.post(root+'/course/active-unit', json={'expected_revision': 1, 'active_unit_id': 'dynamic.rational.demo.v1'}).status_code == 409
    response = client.post(root+'/tutoring/'+rid+'/inputs', headers={'Idempotency-Key': 'answer'}, json={
        'action': 'answer', 'answer': '5', 'expected_revision': run['revision'], 'gate_id': run['gate_id']})
    assert response.status_code == 202
    assert Worker(env.db, enable_models=False).step()
    run = client.get(root+'/tutoring/'+rid).json()
    assert run['view']['feedback']['status'] == 'passed'
    assert client.post(root+'/tutoring/'+rid+'/cancel', json={'expected_revision': run['revision']}).status_code == 200
    for activity in example()['activities']:
        kind = activity['kind']
        response = client.post(root+'/'+kind, headers={'Idempotency-Key': kind}, json={'unit_id': activity['unit_id']})
        assert response.status_code == 202, response.text
        path = root+'/'+kind+'/'+response.json()['run_id']
        worker = Worker(env.db, enable_models=False)
        assert worker.step()
        for index, action in enumerate(('contribute', 'conclude', 'verify')):
            run = client.get(path).json()
            response = client.post(path+'/inputs', headers={'Idempotency-Key': str(index)}, json={
                'action': action, 'reasoning': '先乘除再加减，逐步核对符号', 'answer': '2' if action != 'verify' else ('5' if kind == 'teach' else '3'),
                'expected_revision': run['revision'], 'gate_id': run['gate_id']})
            assert response.status_code == 202, response.text
            assert Worker(env.db, enable_models=False).step()  # fresh worker on every step
        final = client.get(path).json()
        assert final['status'] == 'completed', final
        assert final['view']['verification_check']['status'] == 'passed'
        assert not final['coaching_available']  # new model purpose is not implicitly authorized


def test_wrong_unit_and_archived_binding_rejected(env):
    release(env, 'math.g7.equations.dev1')
    release(env, 'math.g7.rational.dev1')
    client, _ = env.student()
    wid = env.workspace(client, 'temporary')['workspace_id']
    assert binding(client, wid, ['math.g7.rational.dev1']).status_code == 200
    root = f'/api/v1/student/workspaces/{wid}'
    assert client.post(root+'/tutoring', headers={'Idempotency-Key': 'wrong'}, json={'unit_id': 'math.g7.equations.dev1'}).status_code == 409
    assert client.post(root+'/roundtable', headers={'Idempotency-Key': 'wrong-activity'}, json={'unit_id': 'math.g7.equations.activities.dev1'}).status_code in (404, 409)
    assert client.post(root+'/archive', json={'expected_revision': 1}).status_code == 200
    assert binding(client, wid, ['math.g7.rational.dev1'], 1).status_code == 409


def test_purpose_scopes_share_budget_and_future_disabled(env):
    state = env.admin.get('/api/v1/admin/model-runtime').json()
    assert len(state['purpose_scopes']) == 8
    assert all(not p['enabled'] for p in state['purpose_scopes'])
    assert all(p['retry_policy'] == 'no_automatic_retry' for p in state['purpose_scopes'])
    assert '不理解题意' in state['verification_boundary']


def test_chinese_answer_never_rendered_by_actual_model_task(env):
    from tests.test_teach_model import start, pending
    from app.infrastructure.runtime.model_tasks import ModelTaskRunner
    from app.infrastructure.db.models import ModelOperation
    from app.llm.provider import FakeProvider
    client, path, _ = start(env)
    run = pending(env, client, path)
    oid = run['coaching']['operation_id']
    with env.db.transaction() as session:
        original_input = session.get(ModelOperation, oid).request['student_text']
    unsafe = {'quote': original_input[:20], 'focus': 'ratio', 'question': '你把二除以五得到五分之二作为最后结论的理由是什么？'}
    runner = ModelTaskRunner(env.db, lambda c, s: FakeProvider((json.dumps(unsafe, ensure_ascii=False),)))
    assert runner.step(operation_id=oid)
    public = client.get(path).json()['coaching']
    assert public['status'] == 'succeeded'
    assert public['result']['question'] == QUESTIONS['ratio']
    with env.db.transaction() as session:
        op = session.get(ModelOperation, oid)
        assert op.request['student_text'] == original_input
        assert op.usage is not None
        # Simulate a legacy stored result written before G00: read boundary also fences it.
        op.result = {**unsafe, 'judgment': '旧追问'}
    assert client.get(path).json()['coaching']['result']['question'] == QUESTIONS['ratio']


def test_dynamic_content_and_course_survive_backup_restore(env, tmp_path):
    register(env)
    release(env, 'dynamic.rational.demo.v1')
    client, _ = env.student()
    wid = env.workspace(client, 'temporary')['workspace_id']
    assert binding(client, wid).status_code == 200
    root = f'/api/v1/student/workspaces/{wid}/tutoring'
    response = client.post(root, headers={'Idempotency-Key': 'restore'}, json={'unit_id': 'dynamic.rational.demo.v1'})
    rid = response.json()['run_id']
    assert Worker(env.db, enable_models=False).step()
    run = client.get(root+'/'+rid).json()
    assert client.post(root+'/'+rid+'/inputs', headers={'Idempotency-Key': 'restore-answer'}, json={
        'action': 'answer', 'answer': '5', 'expected_revision': run['revision'], 'gate_id': run['gate_id']}).status_code == 202
    destination = tmp_path/'g01-backup.json'
    counts = backup(env.db, destination)
    assert counts['task_versions'] == 5 and counts['course_bindings'] == 1
    with recovery_database(env.db, tmp_path) as recovered:
        restore(recovered, destination)
        assert Worker(recovered, enable_models=False).step()
        with recovered.transaction() as session:
            from app.infrastructure.db.models import AgentRun
            assert get_pack('dynamic.rational.demo.v1', session).tasks[0]['answer'] == '5'
            assert session.get(CourseBinding, wid).active_unit_id == 'dynamic.rational.demo.v1'
            assert session.get(AgentRun, rid).view['feedback']['status'] == 'passed'
