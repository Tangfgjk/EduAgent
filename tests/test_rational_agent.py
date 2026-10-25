"""Dynamic pedagogy, arithmetic truth and publication fences; no external calls."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from app.infrastructure.db.models import ModelOperation
from app.infrastructure.runtime.model_tasks import ModelTaskRunner
from app.modules.generation import rational_agent as a
from app.modules.learning.output_gate import render_result
from app.worker import Worker
from tests.test_s02 import database, env
from tests.test_topic_generation import confirm
from tests.test_s05 import send


@pytest.mark.parametrize('form,p,answer',[
    ('move',[-3,5],'2'),('opposite',[0],'0'),('opposite',[-6],'6'),
    ('absolute',[-7],'7'),('distance',[-4,3],'7'),('larger',[-9,-2],'-2'),
    ('add',[-4,6],'2'),('subtract',[-4,-6],'2'),('multiply',[-3,-5],'15'),
    ('divide',[-7,3],'-7/3'),('divide_add',[-7,3,2],'-1/3'),('add_multiply',[-3,5,-2],'-4'),
])
def test_truth_is_independent_of_model_answer(form,p,answer):
    problem=a.Problem(form=form,parameters=p,answer='999')
    assert str(a.truth(problem)[1])==answer
    with pytest.raises(ValueError,match='reference_mismatch'):
        a.task_spec(problem,'practice','practice','absolute',a.Design(concept='absolute',rationale='检查距离的非负条件',
            nudge='请说明本题的距离含义',representation='请用数轴表示起点与终点',self_check='怎样独立核对距离',discuss='比较不同的距离表示',teach='向同伴说明距离条件'))


def stub(concepts):
    row=SimpleNamespace(id='agent-test',intent={'target_concepts':concepts},sources=[a.source()],plan={})
    row.diagnostics=a.diagnostics(concepts,row.id+'diagnostic')
    return row


def test_variable_goals_task_mix_and_no_assessment_support():
    sequences=[]
    for concepts in (['absolute'],['opposite','absolute','comparison']):
        row=stub(concepts); proposal=a.Proposal.model_validate(a.fake_proposal(row))
        spec=a.compile_proposal(proposal,concepts,row.sources,row.id,row.diagnostics)
        sequences.append([t.problem.form for t in proposal.tasks])
        assert len(spec.tasks)==(5 if len(concepts)==1 else 7)
        assert len(spec.activities)==2
        assert {d.concept for d in proposal.designs}==set(concepts)
        assert all(t.pedagogy is None and not t.hints for t in spec.tasks if t.stage!='practice')
        assert all(t.pedagogy.profile==a.VERSION for t in spec.tasks if t.stage=='practice')
    assert sequences[0]!=sequences[1]
    for bad in ([True],['2'],[1.5],[101]):
        with pytest.raises(ValueError): a.Problem(form='absolute',parameters=bad,answer='0')
    assert len(a.goals_for('数轴相反数绝对值大小比较',[]))==4


def test_compiler_rejects_duplicate_uncovered_goal_wrong_form_and_reference():
    row=stub(['absolute']); original=a.fake_proposal(row)
    for mutate in ('duplicate','goal','form','reference','stage','citation','activity'):
        raw=deepcopy(original)
        if mutate=='duplicate':raw['tasks'][-1]['problem']=raw['tasks'][1]['problem']
        if mutate=='goal':raw['designs'][0]['concept']='opposite'
        if mutate=='form':raw['tasks'][1]['problem']={'form':'multiply','parameters':[3,4],'answer':'12'}
        if mutate=='reference':raw['tasks'][1]['problem']['answer']='999'
        if mutate=='stage':raw['tasks'][0]['stage']='practice'
        if mutate=='citation':raw['source_ids']=['unregistered-source']
        if mutate=='activity':raw['activities'][1]['verification']=raw['tasks'][1]['problem']
        with pytest.raises(ValueError):a.compile_proposal(a.Proposal.model_validate(raw),['absolute'],row.sources,row.id,row.diagnostics)
    with pytest.raises(ValueError,match='zero_divisor'):
        a.truth(a.Problem(form='divide',parameters=[2,0],answer='0'))


def prepare(env,concepts=None,topic='有理数'):
    client,_=env.student();wid=env.workspace(client,'temporary')['workspace_id']
    root=f'/api/v1/student/workspaces/{wid}/course-builds'
    body={'topic':topic,'agent_mode':'rational_agent_v2','learning_goal':'说明概念之间的区别并独立核对','target_concepts':concepts or []}
    response=client.post(root,headers={'Idempotency-Key':'agent-topic'},json=body)
    assert response.status_code==201,response.text
    return client,wid,root,response.json()


def test_broad_goal_requires_clarification_no_model_call_or_false_probe(env):
    client,wid,root,row=prepare(env)
    assert row['status']=='needs_clarification' and row['diagnostics']==[]
    assert row['intent']['goal_options']['absolute']=='绝对值与距离'
    assert row['operation'] is None
    changed=client.post(root,headers={'Idempotency-Key':'agent-topic'},json={'topic':'有理数','agent_mode':'rational_agent_v2','target_concepts':['absolute']})
    assert changed.status_code==409
    assert client.post(root,headers={'Idempotency-Key':'invalid'},json={'topic':'有理数','agent_mode':'rational_agent_v2','target_concepts':['arbitrary_execute']}).status_code==422


def test_agent_course_review_runtime_and_targeted_questions(env):
    client,wid,root,row=prepare(env,['absolute','opposite'])
    assert row['status']=='diagnostic' and len(row['diagnostics'])==2
    assert all('answer' not in task and 'probe_problem' not in task for task in row['diagnostics'])
    queued=confirm(env,client,root,row)
    assert ModelTaskRunner(env.db).step(operation_id=queued['operation']['operation_id'])
    candidate=next(item for item in env.admin.get('/api/v1/admin/course-builds').json()['items'] if item['build_id']==row['build_id'])
    assert client.get(root+'/'+row['build_id']).json()['candidate'] is None
    assert candidate['candidate']['teaching_design']['profile']==a.VERSION
    assert candidate['candidate']['validation']['verifier']==a.VERSION
    with env.db.transaction() as s:
        op=s.get(ModelOperation,queued['operation']['operation_id'])
        assert op.request['prompt_version']==a.VERSION
        payload=json.loads(op.request['generation']['messages'][-1]['content'])
        assert set(payload['target_concepts'])=={'absolute','opposite'}
        assert 'student_text' not in payload and 'diagnostic_observations' in payload
    response=env.admin.post('/api/v1/admin/course-builds/'+row['build_id']+'/review',json={'expected_revision':candidate['revision'],'approve':True,'note':'核对目标、各题条件与数值、概念支架及独立检测隔离，仅开发用途'})
    assert response.status_code==200,response.text
    published=response.json();unit=published['unit_id']
    assert client.post(root+'/'+row['build_id']+'/activate',json={'expected_revision':published['revision'],'course_revision':0}).status_code==200
    public=client.get(root+'/'+row['build_id']).json()
    assert public['candidate']['teaching_design']['profile']==a.VERSION
    started=client.post(f'/api/v1/student/workspaces/{wid}/tutoring',headers={'Idempotency-Key':'learn'},json={'unit_id':unit})
    assert started.status_code==202,started.text
    assert Worker(env.db,enable_models=False).step()
    path=f'/api/v1/student/workspaces/{wid}/tutoring/'+started.json()['run_id']
    send(env,client,path,'answer',answer='0');send(env,client,path,'next')
    view=client.get(path).json()['view']
    assert view['phase']=='practice' and view['support_decision']['profile']==a.VERSION
    send(env,client,path,'answer',answer='999')
    support=client.get(path).json()['view']['support_decision']
    assert support['action']=='representation' and '未判定' in support['basis']
    send(env,client,path,'hint',coaching_text='我不确定绝对值和相反数的表示')
    run=client.get(path).json();op=run['coaching']
    assert ModelTaskRunner(env.db).step(operation_id=op['operation_id'])
    run=client.get(path).json();result=run['coaching']['result']
    assert result['rendering_mode']=='grounded-dialogue.v1'
    assert '我不确定绝对值和相反数的表示' in result['question']
    assert render_result(result)['question']==result['question']
    assert '绝对值' in result['question'] or '相反数' in result['question']
    assert client.get(f'/api/v1/student/workspaces/{wid}/resources').status_code==200
    other,_=env.student("student.other")
    assert other.get(root+'/'+row['build_id']).status_code==404
    assert client.post(path+'/cancel',json={'expected_revision':run['revision']}).status_code==200
    # The compiled activity uses the Agent-authored goal-specific question bank.
    activity=client.post(f'/api/v1/student/workspaces/{wid}/teach',headers={'Idempotency-Key':'teach'},json={'unit_id':unit+'.teach'})
    assert activity.status_code==202,activity.text
    assert Worker(env.db,enable_models=False).step()
    apath=f'/api/v1/student/workspaces/{wid}/teach/'+activity.json()['run_id']
    active=client.get(apath).json()
    response=client.post(apath+'/inputs',headers={'Idempotency-Key':'explain'},json={'expected_revision':active['revision'],'gate_id':active['gate_id'],'action':'contribute','answer':'0','reasoning':'我先说明绝对值表示到原点的距离','request_model':False})
    assert response.status_code==202,response.text
    assert Worker(env.db,enable_models=False).step()
    assert any('绝对值' in m['text'] or '相反数' in m['text'] for m in client.get(apath).json()['view']['messages'] if m['category']=='role_opinion')
    assert client.get(root+'/'+row['build_id']).json()['candidate'] is not None
    # Diagnosis/independent checks never expose the approved lesson resources.
    current=client.get(apath).json()
    response=client.post(apath+'/inputs',headers={'Idempotency-Key':'conclude'},json={'expected_revision':current['revision'],'gate_id':current['gate_id'],'action':'conclude','answer':'0','reasoning':'我会核对定义和数轴位置'})
    assert response.status_code==202 and Worker(env.db,enable_models=False).step()
    assert client.get(f'/api/v1/student/workspaces/{wid}/resources').status_code==409
    assert client.get(root+'/'+row['build_id']).json()['candidate'] is None


def test_new_reference_can_be_withdrawn_without_rewriting_historical_sources(env):
    client,wid,root,row=prepare(env,['absolute'])
    sources=env.admin.get('/api/v1/admin/knowledge-sources').json()['documents']
    assert any(s['source_id']==a.SOURCE_ID for s in sources)
    assert env.admin.post('/api/v1/admin/knowledge-sources/'+a.SOURCE_ID+'/state',json={'expected_revision':0,'enabled':False}).status_code==200
    assert client.post(root+'/'+row['build_id']+'/diagnostic',json={'expected_revision':row['revision'],'answers':[{'answer':'0'},{'answer':'0'}]}).status_code==409
    new=client.post(root,headers={'Idempotency-Key':'after-withdrawal'},json={'topic':'绝对值','agent_mode':'rational_agent_v2'}).json()
    assert new['status']=='source_missing' and new['diagnostics']==[]
    # The legacy reference/version is unaffected by withdrawal of the new source.
    legacy=client.post(root,headers={'Idempotency-Key':'legacy-reference'},json={'topic':'有理数运算'}).json()
    assert legacy['status']=='diagnostic'
