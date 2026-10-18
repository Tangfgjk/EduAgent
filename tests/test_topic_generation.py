import io
import json
import zipfile
from uuid import uuid4
import pytest
import httpx
from sqlalchemy import select
from app.infrastructure.db.models import CourseBuild,ModelOperation
from app.infrastructure.runtime.model_tasks import ModelTaskRunner
from app.modules.knowledge.service import resolve,NODES
from app.modules.generation.mathematics import compile_problem,CourseProposal,compile_course,sample_blueprints
from app.modules.resources.service import import_classroom,render
from app.modules.errors import DomainError
from app.worker import Worker
from tests.test_s02 import database,env
from tests.test_model_runtime import publish


def prepare(env,topic='负数乘除和运算顺序'):
    client,_=env.student()
    wid=env.workspace(client,'temporary')['workspace_id']
    root=f'/api/v1/student/workspaces/{wid}/course-builds'
    response=client.post(root,headers={'Idempotency-Key':'topic'},json={'topic':topic})
    assert response.status_code==201,response.text
    return client,wid,root,response.json()


def confirm(env,client,root,row):
    response=client.post(root+'/'+row['build_id']+'/diagnostic',json={'expected_revision':row['revision'],'answers':[{'answer':'0','reasoning':'说明符号依据'},{'answer':'0'}]})
    assert response.status_code==200,response.text
    row=response.json()
    stale=row['revision']
    response=client.post(root+'/'+row['build_id']+'/plan',json={'expected_revision':stale,'focus':'verification'})
    assert response.status_code==200,response.text
    row=response.json()
    assert row['plan']['student_focus']=='verification'
    assert client.post(root+'/'+row['build_id']+'/plan',json={'expected_revision':stale,'focus':'basis'}).status_code==409
    policy=env.admin.get('/api/v1/admin/generation-policy').json()
    assert env.admin.post('/api/v1/admin/generation-policy',json={'expected_revision':policy['revision'],'courses':True,'interaction':True,'search':False,'acknowledge_development_only':True}).status_code==200
    publish(env,prompt_version='probability.followup.v3',account_daily_calls=0,site_daily_calls=0)
    response=client.post(root+'/'+row['build_id']+'/generate',headers={'Idempotency-Key':'generate'},json={'expected_revision':row['revision']})
    assert response.status_code==202,response.text
    assert Worker(env.db,enable_models=False).step()
    return response.json()


@pytest.mark.parametrize('topic,family',[('负数运算顺序','rational'),('等式性质与解方程','equation'),('一次函数图像','linear'),('勾股定理','pythagoras'),('摸球概率','probability'),('平均数','statistics')])
def test_topic_family_and_independent_math(topic,family):
    value=resolve(topic)
    assert value['status']=='candidate'
    assert NODES[value['nodes'][0]]['family']==family
    for blueprint in sample_blueprints(family,'test'):
        assert str(compile_problem(family,blueprint.parameters)[1])==blueprint.answer
    assert resolve('导数和有理数')['status']=='out_of_scope'
    assert resolve('函数和概率')['status']=='needs_clarification'


def test_generation_diagnostic_review_activation_private_content_and_resources(env):
    client,wid,root,row=prepare(env)
    assert row['status']=='diagnostic'
    assert 'answer' not in row['diagnostics'][0]
    assert 'parameters' not in json.dumps(row)
    queued=confirm(env,client,root,row)
    oid=queued['operation']['operation_id']
    assert ModelTaskRunner(env.db).step(operation_id=oid)
    ready=client.get(root+'/'+row['build_id']).json()
    assert ready['status']=='pending_review',ready
    assert ready['candidate'] is None
    admin=next(item for item in env.admin.get('/api/v1/admin/course-builds').json()['items'] if item['build_id']==row['build_id'])
    assert admin['validation']['status']=='passed'
    approved=env.admin.post('/api/v1/admin/course-builds/'+row['build_id']+'/review',json={
        'expected_revision':admin['revision'],'approve':True,'note':'自编开发课程：独立核对条件、数值、讲解和检测隔离，仅供开发'} )
    assert approved.status_code==200,approved.text
    result=approved.json();unit=result['unit_id']
    other,_=env.student('student.other')
    other_wid=env.workspace(other,'temporary')['workspace_id']
    assert unit not in json.dumps(other.get('/api/v1/student/content-catalog').json())
    assert unit not in json.dumps(other.get('/api/v1/student/content-catalog?workspace_id='+other_wid).json())
    assert other.get(root).status_code==404
    assert client.get('/api/v1/student/content-catalog?workspace_id='+other_wid).status_code==404
    assert unit in json.dumps(client.get('/api/v1/student/content-catalog?workspace_id='+wid).json())
    assert other.post(f'/api/v1/student/workspaces/{other_wid}/course',json={'topic':'复制课程','title':'他人课程','unit_ids':[unit],'active_unit_id':unit,'expected_revision':0}).status_code==404
    activated=client.post(root+'/'+row['build_id']+'/activate',json={'expected_revision':result['revision'],'course_revision':0})
    assert activated.status_code==200,activated.text
    resources=client.get(f'/api/v1/student/workspaces/{wid}/resources').json()['items']
    assert len(resources)==1 and resources[0]['unit_id']==unit
    assert "default-src &#39;none&#39;" in resources[0]['html']
    # Generic dynamic unit can request a real-provider protocol (Fake here), without probability constants.
    response=client.post(f'/api/v1/student/workspaces/{wid}/teach',headers={'Idempotency-Key':'teach'},json={'unit_id':unit+'.teach'})
    assert response.status_code==202,response.text
    assert Worker(env.db,enable_models=False).step()
    path=f'/api/v1/student/workspaces/{wid}/teach/'+response.json()['run_id']
    run=client.get(path).json()
    assert run['coaching_available']
    response=client.post(path+'/inputs',headers={'Idempotency-Key':'explain'},json={'expected_revision':run['revision'],'gate_id':run['gate_id'],'action':'contribute','answer':'0','reasoning':'先乘除再加减，检查符号','request_model':True})
    assert response.status_code==202,response.text
    assert Worker(env.db,enable_models=False).step()
    op=client.get(path).json()['coaching']
    assert ModelTaskRunner(env.db).step(operation_id=op['operation_id'])
    run=client.get(path).json()
    assert run['coaching']['status']=='succeeded',run
    assert run['coaching']['result']['focus']=='basis'
    assert run['coaching']['result']['rendering_mode']=='generic-intent.v1'
    # Withdrawing sources blocks future use, without deleting student contributions.
    assert env.admin.post('/api/v1/admin/knowledge-sources/builtin.MATH.G7.RATIONAL/state',json={'expected_revision':0,'enabled':False}).status_code==200
    assert client.post(root+'/'+row['build_id']+'/activate',json={'expected_revision':result['revision'],'course_revision':1}).status_code==409


def test_bad_reference_rejected_and_budget_record_preserved(env):
    client,wid,root,row=prepare(env)
    queued=confirm(env,client,root,row)
    from app.llm.provider import FakeProvider
    from app.modules.generation.service import fake_proposal
    with env.db.transaction() as session:
        proposal=fake_proposal(session.get(CourseBuild,row['build_id']))
    proposal['tasks'][0]['answer']='999'
    runner=ModelTaskRunner(env.db,lambda c,s:FakeProvider((json.dumps(proposal),)))
    assert runner.step(operation_id=queued['operation']['operation_id'])
    failed=client.get(root+'/'+row['build_id']).json()
    assert failed['status']=='failed' and failed['operation']['error_code']=='invalid_course_proposal',failed
    assert len(failed['attempts'])==2 and failed['operation']['usage'] is not None
    assert failed['unit_id'] is None
    assert failed['validation']['reason_codes']==['reference_mismatch']


def test_resource_import_escape_and_archive_traversal():
    payload={'formatVersion':1,'stage':{'name':'有理数图解'},'scenes':[{'title':'条件','content':{'text':'<script>fetch("https://bad.example")</script>'}}]}
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive: archive.writestr('manifest.json',json.dumps(payload))
    resource=import_classroom(stream.getvalue())
    rendered=render(resource)
    assert '&lt;script&gt;' in rendered and '<script>' not in rendered
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('../manifest.json','{}')
    with pytest.raises(DomainError): import_classroom(stream.getvalue())
