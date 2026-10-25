"""Observable teaching behavior, independent numeric truth and owner isolation."""
import json
from copy import deepcopy
import pytest
from app.llm.provider import ProviderError
from app.modules.generation.interaction import validate, fake
from app.modules.learning.output_gate import render_result
from app.modules.learning.adaptive import materialize
from app.modules.learning.tutor_graph import advance_tutor, content_for
from app.modules.learning.content_catalog import get_pack
from app.modules.learning.goal_context import snapshot
from tests.test_s02 import database, env


def frozen(kind='coach', text='我先把除法算完，再看符号'):
    return {'purpose':'generic_'+kind,'prompt_version':'grounded-dialogue.v1',
            'student_text':text,'next_task_options':[], 'teaching_signals':{},
            'variant_family':'rational','excluded_stems':[]}


def test_model_authored_question_survives_storage_renderer_and_differs_by_input():
    left=frozen();right=frozen(text='我用数轴表示负数，但是方向不确定')
    a=validate(json.dumps(fake(left),ensure_ascii=False),left)
    b=validate(json.dumps(fake(right),ensure_ascii=False),right)
    assert a['question']!=b['question']
    assert render_result(a)['question']==a['question']
    assert left['student_text'] in a['question']
    value=fake(left);value['question']='你准备怎样检查答案是否符合题目条件？'
    assert validate(json.dumps(value,ensure_ascii=False),left)['question']==value['question']


@pytest.mark.parametrize('mutation', ['quote','roles','arithmetic','html','answer','fade'])
def test_grounding_role_and_output_guards(mutation):
    state=frozen('roundtable');value=fake(state)
    if mutation=='quote':value['voices'][1]['quote']='捏造的学生原话'
    if mutation=='roles':value['voices'][1]['role']='proposer'
    if mutation=='arithmetic':value['question']='因为1+1=3，你准备怎么检查？'
    if mutation=='html':value['question']='<script>恶意内容</script>你怎么看？'
    if mutation=='answer':value['question']='答案是3，你知道吗？'
    if mutation=='fade':
        value['teaching_decision']['support']='fade'
        result=validate(json.dumps(value,ensure_ascii=False),state)
        assert result['teaching_decision']['support']=='maintain'
        return
    with pytest.raises(ProviderError):validate(json.dumps(value,ensure_ascii=False),state)


def test_variant_truth_overlap_topic_and_no_answer_in_response():
    state=frozen();value=fake(state)
    value['variant']={'kind':'rational','parameters':[17,2,8],'answer':'33/2'}
    value['teaching_decision']['action']='variant'
    result=validate(json.dumps(value,ensure_ascii=False),state)
    assert 'answer' not in result['variant']
    task=materialize(result['variant'],'parent')
    assert task['answer']=='33/2' and task['stage']=='practice'
    for change in ({'answer':'999'}, {'kind':'equation'}):
        bad=deepcopy(value);bad['variant'].update(change)
        with pytest.raises(ProviderError):validate(json.dumps(bad,ensure_ascii=False),state)
    state['excluded_stems']=[task['stem']]
    with pytest.raises(ProviderError):validate(json.dumps(value,ensure_ascii=False),state)


def test_goal_specific_rational_variant_does_not_turn_absolute_value_into_arithmetic():
    state=frozen();state['variant_concept']='absolute'
    value=fake(state);value['teaching_decision']['action']='variant'
    value['variant']={'kind':'rational','form':'absolute','parameters':[-37],'answer':'37'}
    result=validate(json.dumps(value,ensure_ascii=False),state)
    task=materialize(result['variant'],'absolute-parent')
    assert task['answer']=='37' and '37' in task['stem']
    assert task['pedagogy']['concept']=='absolute'
    assert task['adaptive_variant']==result['variant']
    assert materialize(task['adaptive_variant'],'absolute-parent')==task
    value['variant']={'kind':'rational','form':'multiply','parameters':[-3,2],'answer':'-6'}
    with pytest.raises(ProviderError):validate(json.dumps(value,ensure_ascii=False),state)


def test_variant_executes_and_preserves_independent_tasks_and_restores_support():
    pack=get_pack('math.g7.rational.dev1')
    runtime={'unit_id':pack.unit_id,'content_version':pack.version,
             'practice_task_order':[t['id'] for t in pack.tasks if t['stage']=='practice'],
             'execution':{'support_mode':'adaptive'}}
    state,image,_=advance_tutor('variant-test','adult',{'skill_runtime':runtime},None,item=pack)
    def send(action,**values):
        nonlocal state,image
        state,image,_=advance_tutor('variant-test','adult',{'action':action,**values},image,item=pack)
    send('answer',answer=pack.tasks[0]['answer']);send('next')
    send('answer',answer=pack.tasks[1]['answer'])
    variant={'kind':'rational','parameters':[17,2,8],'stem':'ignored by independent compiler'}
    send('reflect',reflection='我想换一题检查符号',teaching_decision={'action':'variant','support':'restore','reason':'检查符号依据','variant':variant})
    assert state['view']['task']['task_id'].startswith('ADAPT-')
    tasks=content_for(state['data']).tasks
    assert [t for t in tasks if t['stage']=='checkpoint']==[t for t in pack.tasks if t['stage']=='checkpoint']
    assert state['view']['support_decision']['mode']=='restored_step_support'
    send('answer',answer='0')
    assert state['view']['feedback']['status']=='failed'
    send('hint');assert state['view']['hints']
    send('answer',answer='33/2');send('reflect',reflection='已按顺序核对符号')
    assert state['view']['task']['task_id']==pack.tasks[2]['id']


def test_goal_is_owner_scoped_and_never_exports_keys_or_task_answers(env):
    from app.infrastructure.db.models import Account
    client,account=env.student('goal.owner');wid=env.workspace(client,'temporary')['workspace_id']
    other,other_account=env.student('goal.other')
    with env.db.transaction() as session:
        own=session.get(Account,account['id'])
        goal=snapshot(session,wid,own)
        assert goal['evidence']==[] and goal['effect_status']=='not_validated'
        stranger=session.get(Account,other_account['id'])
        with pytest.raises(ValueError):snapshot(session,wid,stranger)
    response=client.get(f'/api/v1/student/workspaces/{wid}/next-action')
    assert response.status_code==200 and response.json()['learning_goal']['workspace_id']==wid
    assert other.get(f'/api/v1/student/workspaces/{wid}/next-action').status_code==404


def activated(env):
    from tests.test_generation_boundaries import course
    client,wid,root,row=course(env)
    response=client.post(root+'/'+row['build_id']+'/activate',json={'expected_revision':row['revision'],'course_revision':0})
    assert response.status_code==200,response.text
    return client,wid,row['unit_id']


@pytest.mark.parametrize('kind',['teach','roundtable'])
def test_three_real_pipeline_turns_keep_same_activity_history_and_stop_at_verification(env,kind):
    from app.worker import Worker
    from app.infrastructure.runtime.model_tasks import ModelTaskRunner
    from app.infrastructure.db.models import ModelOperation
    client,wid,unit=activated(env)
    root=f'/api/v1/student/workspaces/{wid}/{kind}'
    response=client.post(root,headers={'Idempotency-Key':'start'},json={'unit_id':unit+'.'+kind})
    assert response.status_code==202,response.text
    Worker(env.db,enable_models=False).step();path=root+'/'+response.json()['run_id']
    for turn in range(3):
        run=client.get(path).json()
        response=client.post(path+'/inputs',headers={'Idempotency-Key':f'turn-{turn}'},json={
            'expected_revision':run['revision'],'gate_id':run['gate_id'],
            'action':'contribute' if turn==0 else 'refine','answer':'0',
            'reasoning':f'我先检查第{turn}处符号的依据','request_model':True})
        assert response.status_code==202,response.text
        Worker(env.db,enable_models=False).step()
        run=client.get(path).json();oid=run['coaching']['operation_id']
        with env.db.transaction() as s:
            op=s.get(ModelOperation,oid)
            assert len(op.request['context'])==turn
            assert op.request['learning_goal']['unit_id']==unit
            assert 'verification_task' not in op.request['generation']['messages'][-1]['content']
        assert ModelTaskRunner(env.db).step(operation_id=oid)
        run=client.get(path).json()
        assert run['coaching']['status']=='succeeded',run
        assert f'第{turn}处' in run['coaching']['result']['question']
        if kind=='roundtable':assert len(run['coaching']['result']['voices'])==3
    assert run['coaching_available'] is False
    response=client.post(path+'/inputs',headers={'Idempotency-Key':'conclude'},json={
        'expected_revision':run['revision'],'gate_id':run['gate_id'],'action':'conclude',
        'answer':'0','reasoning':'我要独立做新题核验'})
    assert response.status_code==202,response.text
    Worker(env.db,enable_models=False).step()
    run=client.get(path).json()
    assert run['view']['phase']=='verification' and not run['coaching_available']
    assert client.post(path+'/inputs',headers={'Idempotency-Key':'forbidden'},json={
        'expected_revision':run['revision'],'gate_id':run['gate_id'],'action':'refine',
        'answer':'0','reasoning':'请直接给检测提示','request_model':True}).status_code==409


def test_model_variant_flows_through_worker_evidence_and_review(env):
    from sqlalchemy import select
    from app.worker import Worker
    from app.infrastructure.runtime.model_tasks import ModelTaskRunner
    from app.llm.provider import FakeProvider
    from app.infrastructure.db.models import ModelOperation,LearningFact
    from tests.test_s05 import send
    client,wid,unit=activated(env)
    with env.db.transaction() as s:pack=get_pack(unit,s)
    root=f'/api/v1/student/workspaces/{wid}/tutoring'
    response=client.post(root,headers={'Idempotency-Key':'start'},json={'unit_id':unit})
    assert response.status_code==202,response.text
    Worker(env.db,enable_models=False).step();path=root+'/'+response.json()['run_id']
    send(env,client,path,'answer',answer=pack.tasks[0]['answer']);send(env,client,path,'next')
    run=client.get(path).json()
    response=client.post(path+'/inputs',headers={'Idempotency-Key':'ask'},json={
        'expected_revision':run['revision'],'gate_id':run['gate_id'],'action':'hint','coaching_text':'请给我一个检查符号的新变式'})
    assert response.status_code==202,response.text
    Worker(env.db,enable_models=False).step()
    oid=client.get(path).json()['coaching']['operation_id']
    def provider(config,state):
        value=fake(state);value['teaching_decision'].update(action='variant',support='restore')
        value['variant']={'kind':'rational','parameters':[17,2,8],'answer':'33/2'}
        return FakeProvider((json.dumps(value,ensure_ascii=False),))
    assert ModelTaskRunner(env.db,provider).step(operation_id=oid)
    assert client.get(path).json()['coaching']['status']=='succeeded'
    send(env,client,path,'answer',answer=pack.tasks[1]['answer'])
    run=send(env,client,path,'reflect',reflection='我需要换一题检查符号')
    assert run['view']['task']['task_id'].startswith('ADAPT-'),run
    unknown=send(env,client,path,'answer',answer='不确定')
    assert unknown['status']=='awaiting_input' and unknown['view']['feedback']['status']=='unverifiable'
    assert unknown['view']['pending_reviews']==1
    send(env,client,path,'answer',answer='0')
    run=send(env,client,path,'answer',answer='33/2')
    assert run['view']['feedback']['status']=='passed'
    send(env,client,path,'reflect',reflection='已经独立检查变式的计算')
    with env.db.transaction() as s:
        facts=list(s.scalars(select(LearningFact).where(LearningFact.workspace_id==wid,LearningFact.task_id.like('ADAPT-%'))))
        assert any(f.payload['status']=='failed' for f in facts)
        assert all(f.payload['task_snapshot']['answer']=='33/2' for f in facts)
    cards=client.get(f'/api/v1/student/workspaces/{wid}/review-cards').json()['items']
    card=next(c for c in cards if c['task']['task_id'].startswith('ADAPT-'))
    response=client.post(f'/api/v1/student/workspaces/{wid}/review-cards/{card["card_id"]}/attempts',
        headers={'Idempotency-Key':'recall'},json={'expected_revision':card['revision'],'answer':'33/2','reasoning':'再次检查符号'})
    assert response.status_code==200,response.text
    assert response.json()['attempt']['status']=='passed'


def test_model_planning_changes_real_execution_and_rsi_uses_same_goal(env):
    from app.worker import Worker
    from app.infrastructure.runtime.model_tasks import ModelTaskRunner
    from app.llm.provider import FakeProvider
    from tests.test_g08 import ready
    from tests.test_model_runtime import publish
    from tests.test_s09 import decide
    from app.modules.planning.model import fake_proposal
    from app.modules.skill_evolution.service import source_snapshot
    client,wid,parent=ready(env);publish(env)
    base=parent['view']['policy']['execution']
    changed='verification' if base['question_focus']!='verification' else 'basis'
    root=f'/api/v1/student/workspaces/{wid}/strategies'
    response=client.post(root,headers={'Idempotency-Key':'model-plan-goal'},json={
        'daily_minutes':15,'reflection_focus':'basis','learning_experience':'我希望根据符号错误核对依据',
        'execution':base,'request_model':True,'self_service':True})
    assert response.status_code==202,response.text
    Worker(env.db,enable_models=False).step();path=root+'/'+response.json()['run_id']
    op=client.get(path).json()['generation']['operation_id']
    def provider(config,state):
        body=json.loads(state['generation']['messages'][-1]['content'])
        assert body['learning_goal']['unit_id']==state['learning_goal']['unit_id']
        assert 'workspace_id' not in body['learning_goal'] and not body['learning_goal']['evidence']
        value=fake_proposal(state);value['policy']['execution']={**base,'question_focus':changed}
        return FakeProvider((json.dumps(value,ensure_ascii=False),))
    assert ModelTaskRunner(env.db,provider).step(operation_id=op)
    Worker(env.db,enable_models=False).step()
    run=client.get(path).json()
    assert run['view']['phase']=='student_confirm',run
    assert run['compilation']['policy']['execution']['question_focus']==changed
    final=decide(env,client,path,'confirm');assert final['status']=='completed'
    with env.db.transaction() as s:
        dataset=source_snapshot(s,env.actor(s,client),wid)
        assert dataset['learning_goal']['workspace_id']==wid
        assert dataset['baseline']['policy']['execution']['question_focus']==changed
        unit=dataset['unit_id']
    response=client.post(f'/api/v1/student/workspaces/{wid}/tutoring',headers={'Idempotency-Key':'new-policy'},json={'unit_id':unit})
    assert response.status_code==202,response.text
    assert response.json()['skill_execution']['execution']['question_focus']==changed
