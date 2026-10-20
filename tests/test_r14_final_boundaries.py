import json
from types import SimpleNamespace
import pytest
from tests.test_s02 import database,env
from tests.test_g08 import ready
from app.modules.resources.quality import freeze
from app.modules.resources.classroom import project
from tests.test_classroom_manifest import document
from app.modules.errors import DomainError
from app.modules.generation.interaction import validate
from app.llm.provider import ProviderError


def test_live_stream_is_owned_authenticated_and_contains_only_committed_history(env):
    client,wid,parent=ready(env)
    runs=client.get(f'/api/v1/student/workspaces/{wid}/tutoring').json()['items'];rid=runs[0]['run_id']
    path=f'/api/v1/student/workspaces/{wid}/tutoring/{rid}/live?follow=false'
    response=client.get(path)
    assert response.status_code==200,response.text
    assert response.headers['content-type'].startswith('text/event-stream')
    assert 'event: snapshot' in response.text and 'task_snapshot' not in response.text
    data=json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith('data: ')))
    assert data['status']=='cancelled' and data['history']['items']
    assert all(item['processed'] for item in data['history']['items'])
    other,_=env.student('live.other');assert other.get(path).status_code==404
    assert env.admin.get(path).status_code==403


def test_resource_review_covers_all_displayed_elements_and_never_truncates_tail():
    doc=document();doc['scenes'][0]['content']['canvas']['elements']=[{'id':str(i),'type':'text','content':'自编条件说明','width':100,'height':50} for i in range(110)]
    doc['scenes'][0]['content']['canvas']['elements'][109]['content']='尾部数学表述：除数不能为零。'
    classroom=project(doc)
    resource=SimpleNamespace(id='resource',body={'title':'完整课堂','cards':[{'text':'卡片截断之前'}],'classroom':classroom},sha256='a'*64)
    build=SimpleNamespace(id='build',candidate={'objectives':['检查数学条件']},sources=[])
    result=freeze(resource,build,1,SimpleNamespace(prompt_version='probability.followup.v3'))
    assert '尾部数学表述' in result['generation']['messages'][1]['content']
    classroom['scenes'][0]['elements'][-1]['text']='后半段'*8000
    with pytest.raises(DomainError) as error:freeze(resource,build,1,SimpleNamespace(prompt_version='probability.followup.v3'))
    assert error.value.code=='resource_review_capacity'


def test_semantic_answer_is_only_a_confirmable_interpretation_not_a_grade():
    snapshot={'purpose':'generic_coach','student_text':'我觉得最终算下来应该是三','next_task_options':[]}
    body={'quote':snapshot['student_text'],'focus':'basis','answer_understanding':{'candidate':'3','evidence':'应该是三','uncertain':False,'negated':False,'unit':''}}
    result=validate(json.dumps(body,ensure_ascii=False),snapshot)
    assert result['answer_understanding']['status']=='requires_student_confirmation' and 'correct' not in result
    body['answer_understanding']['negated']=True
    assert 'answer_understanding' not in validate(json.dumps(body,ensure_ascii=False),snapshot)
    body['answer_understanding']['negated']=False;body['answer_understanding']['evidence']='捏造的原文'
    with pytest.raises(ProviderError):validate(json.dumps(body,ensure_ascii=False),snapshot)


def test_model_task_reordering_preserves_all_practices_and_checkpoint_order():
    from app.modules.learning.content_catalog import ContentPack
    from app.modules.learning.content_repository import content_hash
    from app.modules.learning.tutor_graph import advance_tutor
    tasks=[{'id':f'route-{i}','stage':stage,'stem':'自编路由测试','answer':'3','format':'数值','hints':['检查条件'] if stage=='practice' else []} for i,stage in enumerate(['diagnostic','practice','practice','practice','checkpoint','checkpoint'])]
    pack=ContentPack('dynamic.generated.route','自编调度验收','七年级','数与式',(),(),tasks,content_hash(tasks))
    state,image,_=advance_tutor('route-run','synthetic',{},None,item=pack)
    def step(action,**values):
        nonlocal state,image
        state,image,_=advance_tutor('route-run','synthetic',{'action':action,**values},image,item=pack)
        return state['view']
    step('answer',answer='3');step('next');step('answer',answer='3')
    view=step('reflect',reflection='核对依据',teaching_decision={'next_task_id':'route-3','decision_reason':'选择未完成练习核对表示'})
    assert view['task']['task_id']=='route-3'
    step('answer',answer='3');view=step('reflect',reflection='继续核对',teaching_decision={'next_task_id':'route-4'})
    assert view['task']['task_id']=='route-2'  # checkpoint cannot be selected as practice
    step('answer',answer='3');view=step('reflect',reflection='已完成全部练习')
    assert view['task']['task_id']=='route-4' and not view['can_hint']
    step('answer',answer='3');view=step('next')
    assert view['task']['task_id']=='route-5' and not view['can_hint']
