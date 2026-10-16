from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from app.infrastructure.db.maintenance import backup, restore
from app.infrastructure.db.models import AgentRun, FactJudgment, LearningFact, LearningProjection, MemoryNote, RecallAttempt, ReviewCard, now
from app.modules.memory.backfill import backfill
from app.modules.memory.projections import DIGEST, MEMORY, validate_memory_sources
from app.worker import Worker
from tests.test_s02 import database, env, recovery_database
from tests.test_s05 import setup, to_practice, send, enable


def review_path(wid):
    return f'/api/v1/student/workspaces/{wid}'


def make_card(env, kind='temporary'):
    client, wid, path = setup(env, kind)
    to_practice(env, client, path)
    send(env, client, path, 'answer', answer='0', reasoning='先尝试把常数移到右边')
    cards = client.get(review_path(wid)+'/review-cards').json()
    assert len(cards['items']) == 1
    return client, wid, path, cards['items'][0]


def recall(client, wid, card, answer, key=None):
    return client.post(review_path(wid)+f'/review-cards/{card["card_id"]}/attempts',
        headers={'Idempotency-Key':key or str(uuid4())}, json={'answer':answer, 'expected_revision':card['revision'], 'reasoning':'独立代入检查'})


def test_facts_wrong_assisted_unknown_are_separate_and_traceable(env):
    client, wid, path, card = make_card(env)
    assert card['reasons'] == ['numeric_error'] and card['due']
    original = client.get(review_path(wid)+'/facts/'+card['source_fact_ids'][0]).json()
    assert original['status'] == 'failed' and original['answer'] == '0'
    assert 'answer' not in original['task'] and 'hints' not in original['task']
    send(env, client, path, 'hint')
    send(env, client, path, 'answer', answer='6')
    updated = client.get(review_path(wid)+'/review-cards').json()['items'][0]
    assert set(updated['reasons']) == {'numeric_error','assisted_success'}
    assert updated['due']  # 使用提示答对不能自动消除复习需要。
    send(env, client, path, 'reflect', reflection='等式两边做相同运算，再代入验证')
    send(env, client, path, 'answer', answer='我无法写出数值')
    assert len(client.get(review_path(wid)+'/review-cards').json()['items']) == 1
    unknowns = client.get(review_path(wid)+'/unverifiable-answers').json()['items']
    assert len(unknowns) == 1 and unknowns[0]['fact_id']
    digest = client.get(review_path(wid)+'/memory').json()['items'][0]['digest']
    practice = next(row for row in digest['rows'] if row['stage']=='practice')
    assert practice == {'source_kind':'synthetic','stage':'practice','passed':1,'failed':1,'unverifiable':1,'hinted_passed':1}


def test_recall_schedule_no_early_farming_and_unknown_not_wrong(env):
    client, wid, _, card = make_card(env)
    first = recall(client, wid, card, '12/2','one')
    assert first.status_code == 200, first.text
    result = first.json()
    assert result['attempt']['status'] == 'passed' and result['attempt']['counted_for_schedule']
    assert result['card']['schedule_step'] == 1 and not result['card']['due']
    # 完全重放不重复写；旧 revision 使用新键仍冲突。
    assert recall(client,wid,card,'12/2','one').json()['attempt']['attempt_id'] == result['attempt']['attempt_id']
    assert recall(client,wid,card,'12/2','different').status_code == 409
    early = recall(client,wid,result['card'],'6').json()
    assert not early['attempt']['counted_for_schedule'] and early['card']['schedule_step']==1
    assert early['card']['next_due_at']==result['card']['next_due_at']
    unknown = recall(client,wid,early['card'],'不确定').json()
    assert unknown['attempt']['status']=='unverifiable'
    assert unknown['card']['next_due_at']==result['card']['next_due_at']
    wrong = recall(client,wid,unknown['card'],'7').json()
    assert wrong['card']['due'] and wrong['card']['schedule_step']==0
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(RecallAttempt)) == 4


def test_elapsed_schedule_advances_to_three_and_seven_days(env, monkeypatch):
    import app.modules.memory.service as module
    client,wid,_,card = make_card(env)
    timestamp=now()+10
    monkeypatch.setattr(module,'now',lambda:timestamp)
    for days,step in [(1,1),(3,2),(7,3),(7,3)]:
        result=recall(client,wid,card,'6').json()
        assert result['card']['next_due_at']==timestamp+days*86400
        assert result['card']['schedule_step']==step
        timestamp=result['card']['next_due_at']+1
        card=result['card']


def test_self_report_and_human_judgment_do_not_rewrite_facts(env):
    client,wid,path=setup(env)
    run=send(env,client,path,'answer',answer='文字答案')
    row=env.admin.get('/api/v1/admin/answer-reviews').json()['items'][0]
    assert env.admin.post(f'/api/v1/admin/answer-reviews/{row["review_id"]}/resolve',json={
        'expected_revision':1,'conclusion':'incorrect','note':'依据题目条件判断，该表达不能作为正确数值答案'}).status_code==200
    card=client.get(review_path(wid)+'/review-cards').json()['items'][0]
    fid=card['source_fact_ids'][0]
    body={'fact_id':fid,'text':'当时是我没写完，请保留这个情况说明'}
    for _ in range(2):
        assert client.post(review_path(wid)+'/memory-notes',headers={'Idempotency-Key':'note'},json=body).status_code==201
    fact=client.get(review_path(wid)+'/facts/'+fid).json()
    assert fact['status']=='unverifiable' and len(fact['judgments'])==1 and len(fact['notes'])==1
    memory=client.get(review_path(wid)+'/memory').json()['items'][0]
    assert any(claim['source_kind']=='student_self_report' for claim in memory['observations']['claims'])
    assert any(claim['source_kind']=='human_judgment' for claim in memory['observations']['claims'])
    assert all(ref['kind']!='memory' for ref in memory['observations']['source_refs'])


def test_cross_user_workspace_temporary_global_and_archived_boundaries(env):
    client,wid,_,card=make_card(env,'project')
    other,_=env.student('student.two')
    assert other.get(review_path(wid)+'/memory').status_code==404
    assert other.get(review_path(wid)+'/facts/'+card['source_fact_ids'][0]).status_code==404
    second=env.workspace(client)['workspace_id']
    assert client.get(review_path(second)+f'/review-cards/{card["card_id"]}').status_code==404
    assert client.get(review_path(second)+'/memory').json()['items'][0]['digest'] is None
    temp=env.workspace(client,'temporary')['workspace_id']
    before=client.get('/api/v1/student/memory').json()
    assert before['project_count']==2 and temp not in [row['workspace_id'] for row in before['items']]
    client.post(review_path(wid)+'/archive',json={'expected_revision':1})
    after=client.get('/api/v1/student/memory').json()
    assert next(row for row in after['items'] if row['workspace_id']==wid)['status']=='archived'
    assert recall(client,wid,card,'6').status_code==409
    assert client.get(review_path(wid)+'/review-cards').status_code==200


def test_independent_check_content_withdrawal_and_role_gate(env):
    client,wid,path=setup(env)
    send(env,client,path,'answer',answer='0')  # 诊断产生待复习题，仍在独立诊断阶段。
    card=client.get(review_path(wid)+'/review-cards').json()['items'][0]
    assert recall(client,wid,card,'3').json()['error']['code']=='independent_check'
    send(env,client,path,'next')
    enable(env,False)
    assert recall(client,wid,card,'3').json()['error']['code']=='content_not_enabled'
    assert env.admin.get('/api/v1/student/memory').status_code==403


def test_source_records_immutable_and_projection_rebuild_idempotent(env):
    client,wid,_,card=make_card(env)
    before=client.get(review_path(wid)+'/memory').json()
    for _ in range(2):
        assert client.post(review_path(wid)+'/memory/rebuild').json()==before
    with pytest.raises(DBAPIError):
        with env.db.transaction() as session:
            session.execute(update(LearningFact).values(kind='rewritten'))
    with env.db.transaction() as session:
        assert session.scalar(select(LearningFact.kind))=='answer'
    with pytest.raises(ValueError): validate_memory_sources([{'kind':'memory','id':'other-L3'}])
    with pytest.raises(ValueError): validate_memory_sources([{'kind':'digest','name':MEMORY}])


def test_backfill_only_verified_old_run_and_idempotent(env, monkeypatch):
    import app.worker as module
    original=module.publish_turn
    monkeypatch.setattr(module,'publish_turn',lambda *args:None)
    client,wid,path=setup(env)
    to_practice(env,client,path)
    send(env,client,path,'answer',answer='0')
    assert client.get(review_path(wid)+'/review-cards').json()['items']==[]
    assert backfill(env.db)['eligible']==2
    assert client.get(review_path(wid)+'/review-cards').json()['items']==[]
    assert backfill(env.db,apply=True)['eligible']==2
    assert len(client.get(review_path(wid)+'/review-cards').json()['items'])==1
    assert backfill(env.db,apply=True)['eligible']==0
    monkeypatch.setattr(module,'publish_turn',original)


def test_recall_duplicate_concurrency_and_restore(env,tmp_path):
    client,wid,_,card=make_card(env)
    # 通过受控服务层并发，事务和空间锁必须只接受同键一次。
    from app.modules.memory.service import Memory
    def execute(_):
        with env.db.transaction() as session:
            actor=env.actor(session,client)
            return Memory().recall(session,actor,wid,card['card_id'],{'answer':'6','expected_revision':card['revision']},'parallel')['attempt']['attempt_id']
    with ThreadPoolExecutor(max_workers=2) as pool: ids=list(pool.map(execute,range(2)))
    assert ids[0]==ids[1]
    destination=tmp_path/'s06.json'
    backup(env.db,destination)
    with recovery_database(env.db,tmp_path) as recovered:
        restore(recovered,destination)
        with recovered.transaction() as session:
            assert session.scalar(select(func.count()).select_from(RecallAttempt))==1
            assert session.get(ReviewCard,card['card_id']).schedule_step==1
            assert session.get(LearningProjection,(wid,MEMORY)) is not None
