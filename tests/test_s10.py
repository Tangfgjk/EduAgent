"""多内容包版本、启停隔离及陪学—复习—证据真实闭环。"""
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.infrastructure.db.models import Account, LearningFact
from app.modules.learning.content_catalog import PACKS
from app.modules.learning.development_content import CONTENT_VERSION, UNIT_ID
from app.modules.learning.numeric_verifier import verify
from app.modules.memory.backfill import backfill
from app.worker import Worker
from tests.test_s02 import database, env
from tests.test_s05 import send
from tests.test_s06 import recall


def release(env, unit, enabled=True, **overrides):
    item = next(item for item in env.admin.get('/api/v1/admin/content-catalog').json()['items'] if item['unit_id'] == unit.unit_id)
    if enabled and unit.unit_id in {'math.g9.probability.counting.dev1', 'math.g7.statistics.frequency.dev1'}:
        review = env.admin.get('/api/v1/admin/development-reviews', params={'target_id':unit.unit_id,'version':unit.version}).json()
        assert env.admin.post('/api/v1/admin/development-reviews', json={'kind':'content','target_id':unit.unit_id,
            'version':unit.version,'expected_revision':review['revision'],'conclusion':'development_ready','note':'隔离测试核对题目与数值提示；不是专业审核。'}).status_code == 200
    return env.admin.post(f'/api/v1/admin/content-catalog/{unit.unit_id}/development-release', json={
        'content_version': item['content_version'], 'expected_revision': item['revision'],
        'enabled': enabled, 'acknowledge_development_only': True, **overrides})


def start(client, wid, unit):
    return client.post(f'/api/v1/student/workspaces/{wid}/tutoring', headers={'Idempotency-Key': str(uuid4())}, json={'unit_id': unit.unit_id})


def test_registry_integrity_and_original_version():
    assert PACKS[0].unit_id == UNIT_ID and PACKS[0].version == CONTENT_VERSION
    assert {p.grade for p in PACKS} == {'七年级', '八年级', '九年级'}
    assert {p.domain for p in PACKS} == {'数与式', '函数', '几何', '统计概率'}
    ids = [task['id'] for p in PACKS for task in p.tasks]
    assert len(ids) == len(set(ids)) == 35
    by_id = {p.unit_id: p for p in PACKS}
    for p in PACKS:
        assert [task['stage'] for task in p.tasks] == ['diagnostic', 'practice', 'practice', 'checkpoint', 'checkpoint']
        assert p.unit_id not in p.prerequisites
        assert all(prerequisite in by_id for prerequisite in p.prerequisites)
        for task in p.tasks:
            assert verify(task['answer'], task['answer']) == 'passed'
            assert len(task['hints']) == (4 if task['stage'] == 'practice' else 0)
    def walk(p, seen):
        assert p.unit_id not in seen
        for parent in p.prerequisites:
            walk(by_id[parent], seen | {p.unit_id})
    for p in PACKS:
        walk(p, set())


@pytest.mark.parametrize('unit', PACKS[1:], ids=lambda p: p.unit_id)
def test_new_pack_full_cycle_recall_and_evidence(env, unit):
    client, _ = env.student()
    wid = env.workspace(client, 'temporary')['workspace_id']
    assert start(client, wid, unit).json()['error']['code'] == 'content_not_enabled'
    assert release(env, unit).status_code == 200
    result = start(client, wid, unit)
    assert result.status_code == 202, result.text
    path = f'/api/v1/student/workspaces/{wid}/tutoring/{result.json()["run_id"]}'
    Worker(env.db).step()
    run = client.get(path).json()
    assert run['view']['unit_id'] == unit.unit_id
    assert run['view']['content_version'] == unit.version
    assert set(run['view']['task']) == {'task_id', 'stem', 'answer_format'}
    # 诊断错误保留卡片；练习故意使用提示；独立检测不借助提示。
    send(env, client, path, 'answer', answer='999')
    send(env, client, path, 'next')
    for task in unit.tasks[1:3]:
        send(env, client, path, 'hint')
        run = send(env, client, path, 'answer', answer=task['answer'], reasoning='按题设列式并核对')
        assert run['view']['feedback']['status'] == 'passed'
        send(env, client, path, 'reflect', reflection='检查条件和符号，再重新计算')
    for task in unit.tasks[3:]:
        run = client.get(path).json()
        assert not run['view']['can_hint']
        send(env, client, path, 'answer', answer=task['answer'])
        send(env, client, path, 'next')
    assert client.get(path).json()['status'] == 'completed'
    scope = f'/api/v1/student/workspaces/{wid}'
    cards = client.get(scope+'/review-cards').json()['items']
    assert len(cards) == 3
    assert all(card['content_version'] == unit.version for card in cards)
    card = next(card for card in cards if card['task']['task_id'] == unit.tasks[0]['id'])
    result = recall(client, wid, card, unit.tasks[0]['answer'])
    assert result.status_code == 200 and result.json()['attempt']['status'] == 'passed', result.text
    evidence = client.get(scope+'/evidence').json()
    assert evidence['scope'] == 'workspace'
    with env.db.transaction() as session:
        facts = list(session.scalars(select(LearningFact).where(LearningFact.workspace_id == wid)))
        assert len(facts) == 10  # 5 答案 + 2 提示 + 2 反思 + 1 复做。
        assert all(fact.content_version == unit.version for fact in facts)
    assert backfill(env.db)['eligible'] == 0


def test_catalog_role_secrecy_cas_and_unit_isolation(env):
    client, _ = env.student()
    student = client.get('/api/v1/student/content-catalog').json()
    assert len(student['items']) == 7 and all(not item['tasks'] for item in student['items'])
    assert '不是七至九年级完整课程' in student['coverage_note']
    assert client.get('/api/v1/admin/content-catalog').status_code == 403
    unit, other = PACKS[1:3]
    assert release(env, unit, content_version='0'*64).status_code == 409
    assert release(env, unit).status_code == 200
    assert release(env, unit, expected_revision=0).status_code == 409
    wid = env.workspace(client, 'temporary')['workspace_id']
    assert start(client, wid, other).json()['error']['code'] == 'content_not_enabled'
    response = start(client, wid, unit)
    assert response.status_code == 202
    assert release(env, unit, False).status_code == 200
    Worker(env.db).step()  # 入队之后停用也会被 worker 重新检查。
    path = f'/api/v1/student/workspaces/{wid}/tutoring/{response.json()["run_id"]}'
    assert client.get(path).json()['error_code'] == 'content_not_enabled'
    assert client.post(f'/api/v1/student/workspaces/{wid}/tutoring', headers={'Idempotency-Key':'bad'}, json={'unit_id':'unregistered'}).status_code == 404


def test_other_unit_approval_cannot_authorize_recall_or_real_student(env):
    client, _ = env.student()
    unit = PACKS[-1]
    assert release(env, unit).status_code == 200
    assert release(env, PACKS[0]).status_code == 200
    wid = env.workspace(client, 'temporary')['workspace_id']
    response = start(client, wid, unit)
    path = f'/api/v1/student/workspaces/{wid}/tutoring/{response.json()["run_id"]}'
    Worker(env.db).step()
    send(env, client, path, 'answer', answer='0')
    run = client.get(path).json()
    assert client.post(path+'/cancel', json={'expected_revision':run['revision']}).status_code == 200
    card = client.get(f'/api/v1/student/workspaces/{wid}/review-cards').json()['items'][0]
    assert release(env, unit, False).status_code == 200
    assert recall(client, wid, card, '1/6').json()['error']['code'] == 'content_not_enabled'
    assert release(env, unit).status_code == 200
    with env.db.transaction() as session:
        session.scalar(select(Account).where(Account.role == 'student')).source_kind = 'real_student'
    assert start(client, wid, unit).json()['error']['code'] == 'development_content_only'


def test_new_unit_unknown_enters_review_with_correct_snapshot(env):
    client, _ = env.student()
    unit = PACKS[3]
    release(env, unit)
    wid = env.workspace(client, 'temporary')['workspace_id']
    response = start(client, wid, unit)
    path = f'/api/v1/student/workspaces/{wid}/tutoring/{response.json()["run_id"]}'
    Worker(env.db).step()
    send(env, client, path, 'answer', answer='我用图解释')
    review = env.admin.get('/api/v1/admin/answer-reviews').json()['items'][0]
    assert review['content_version'] == unit.version and review['task'] == unit.tasks[0]
    assert review['status'] == 'pending'
