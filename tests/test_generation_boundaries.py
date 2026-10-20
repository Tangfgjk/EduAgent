"""Paid external boundaries, owner isolation and late-result fences (mock HTTP only)."""
import hashlib
import io
import json
import zipfile
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select
from app.infrastructure.db.models import ResourceBudget, ResourceModelCall, OpenMAICJob, ResourceArtifact, CourseBuild, GenerationPolicy, now
from app.modules.resources.proxy import reserve, settle
from app.modules.resources.openmaic import OpenMAICRunner, OpenMAICPort
from app.modules.knowledge.search import SearchRunner, allowed_url
from app.modules.errors import DomainError
from app.infrastructure.runtime.model_tasks import ModelTaskRunner
from tests.test_s02 import database, env
from tests.test_model_runtime import publish
from tests.test_topic_generation import prepare, confirm


def course(env):
    client,wid,root,row=prepare(env)
    queued=confirm(env,client,root,row)
    assert ModelTaskRunner(env.db).step(operation_id=queued['operation']['operation_id'])
    row=next(r for r in env.admin.get('/api/v1/admin/course-builds').json()['items'] if r['build_id']==row['build_id'])
    response=env.admin.post('/api/v1/admin/course-builds/'+row['build_id']+'/review',json={'expected_revision':row['revision'],'approve':True,'note':'开发核对数学条件与数值，讲解及独立验证隔离已检查'})
    assert response.status_code==200,response.text
    return client,wid,root,response.json()


def test_search_canonical_scope_idempotency_allowlist_and_late_pause(env,monkeypatch):
    monkeypatch.setenv('WENJIN_TAVILY_KEY','test-not-a-real-key')
    client,wid,root,row=prepare(env,'有理数，联系测试人手机号13800000000')
    with env.db.transaction() as s: s.get(GenerationPolicy,1).search=True
    path=root+'/'+row['build_id']+'/search';key=str(uuid4())
    for _ in range(2): assert client.post(path,headers={'Idempotency-Key':key}).status_code==202
    calls=[]
    def respond(request):
        data=json.loads(request.content);calls.append(data)
        assert client.get(path).json()['items'][-1]['result'] is None
        assert data['query']=='初中数学 有理数运算'
        assert '13800000000' not in request.content.decode()
        assert data['search_depth']=='basic' and data['max_results']==5
        return httpx.Response(200,json={'results':[{'url':'https://www.moe.gov.cn/example','title':'依据','content':'待授权片段'}, {'url':'https://moe.gov.cn.bad.example/a'}, {'url':'http://127.0.0.1:4003/a'}],'usage':{'credits':1}})
    assert SearchRunner(env.db,httpx.MockTransport(respond)).step()
    assert len(calls)==1
    result=client.get(path).json()['items'][0]
    assert result['status']=='succeeded' and len(result['result']['results'])==1
    assert result['result']['results'][0]['usable_for_generation'] is False
    assert client.post(path,headers={'Idempotency-Key':str(uuid4())}).status_code==202
    assert client.post(path,headers={'Idempotency-Key':str(uuid4())}).status_code==429
    def pause(request):
        with env.db.transaction() as s: s.get(GenerationPolicy,1).search=False
        return respond(request)
    assert SearchRunner(env.db,httpx.MockTransport(pause)).step()
    assert client.get(path).json()['items'][-1]['status']=='discarded'
    assert not SearchRunner(env.db,httpx.MockTransport(respond)).step()
    for bad in ('https://moe.gov.cn@evil.example','https://moe.gov.cn:4003/x','https://moe.gov.cn.evil.example',None): assert not allowed_url(bad)


def test_resource_budget_conservative_unknown_single_settlement_and_cancel(env,monkeypatch):
    monkeypatch.setenv('WENJIN_DEEPSEEK_KEY','test-not-a-real-key')
    publish(env,vendor='deepseek')
    secret='test-only-opaque-gateway'
    with env.db.transaction() as s:
        budget=ResourceBudget(secret_hash=hashlib.sha256(secret.encode()).hexdigest(),enabled=True)
        s.add(budget);s.flush();bid=budget.id
    body={'messages':[{'role':'user','content':'有理数概念'}],'max_tokens':4096}
    identifier,request=reserve(env.db,secret,body)
    assert request['max_tokens']==4096 and request['stream'] is False
    assert settle(env.db,identifier,{'usage':{'prompt_tokens':10,'completion_tokens':20}})
    assert not settle(env.db,identifier,{'usage':{'prompt_tokens':0,'completion_tokens':0}})
    with env.db.transaction() as s:
        budget=s.get(ResourceBudget,bid)
        assert (budget.calls,budget.input_reserved,budget.output_reserved)==(1,10,20)
    identifier,_=reserve(env.db,secret,body)
    assert settle(env.db,identifier,None)
    with env.db.transaction() as s:
        assert s.get(ResourceModelCall,identifier).status=='unknown'
        assert s.get(ResourceBudget,bid).output_reserved==4116
        s.get(ResourceBudget,bid).calls=20
    with pytest.raises(DomainError) as e: reserve(env.db,secret,body)
    assert e.value.code=='resource_budget_exhausted'
    with env.db.transaction() as s: s.get(ResourceBudget,bid).calls=2
    identifier,_=reserve(env.db,secret,body)
    with env.db.transaction() as s: s.get(ResourceBudget,bid).enabled=False
    assert not settle(env.db,identifier,{'usage':{'prompt_tokens':10,'completion_tokens':20}})


def test_proxy_malformed_upstream_returns_safe_failure_and_keeps_reservation(env,monkeypatch):
    monkeypatch.setenv('WENJIN_DEEPSEEK_KEY','test-not-a-real-key')
    publish(env,vendor='deepseek')
    secret='test-opaque-malformed'
    with env.db.transaction() as s:
        s.add(ResourceBudget(secret_hash=hashlib.sha256(secret.encode()).hexdigest(),enabled=True))
    from app.gateway.v1 import create_app
    from app.config import Settings
    from fastapi.testclient import TestClient
    app=create_app(Settings(),env.db)
    app.state.openmaic_proxy_transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'choices':['invalid'],'usage':None}))
    with TestClient(app) as client:
        response=client.post('/internal/openmaic/v1/chat/completions',headers={'Authorization':'Bearer '+secret},json={'messages':[{'role':'user','content':'自编资源'}],'max_tokens':4096})
        assert response.status_code==503,response.text
        assert response.json()['error']['code']=='resource_upstream_unknown'
    with env.db.transaction() as s:
        budget=s.scalar(select(ResourceBudget).where(ResourceBudget.secret_hash==hashlib.sha256(secret.encode()).hexdigest()))
        assert budget.calls==1 and budget.output_reserved==4096
        assert s.scalar(select(ResourceModelCall).where(ResourceModelCall.budget_id==budget.id)).status=='unknown'


def test_resource_sdk_text_parts_and_sse_keep_single_budgeted_call(env,monkeypatch):
    monkeypatch.setenv('WENJIN_DEEPSEEK_KEY','test-not-a-real-key')
    publish(env,vendor='deepseek')
    secret='test-sdk-stream'
    with env.db.transaction() as s:s.add(ResourceBudget(secret_hash=hashlib.sha256(secret.encode()).hexdigest(),enabled=True))
    from app.gateway.v1 import create_app
    from app.config import Settings
    from fastapi.testclient import TestClient
    app=create_app(Settings(),env.db);calls=[]
    def respond(request):
        body=json.loads(request.content);calls.append(body)
        assert body['messages'][0]['content']=='自编概念'
        assert body['stream'] is False and body['max_tokens']==4096
        return httpx.Response(200,json={'id':'test-result','model':'resolved','choices':[{'index':0,'message':{'role':'assistant','content':'{"lesson":"检查除数"}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':8}})
    app.state.openmaic_proxy_transport=httpx.MockTransport(respond)
    with TestClient(app) as client:
        response=client.post('/internal/openmaic/v1/chat/completions',headers={'Authorization':'Bearer '+secret},json={'messages':[{'role':'user','content':[{'type':'text','text':'自编概念'}]}],'stream':True})
        assert response.status_code==200,response.text
        assert response.headers['content-type'].startswith('text/event-stream')
        assert 'data: [DONE]' in response.text
        chunks=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: {')]
        assert chunks[0]['choices'][0]['delta']['content']=='{"lesson":"检查除数"}'
        assert chunks[-1]['usage']['completion_tokens']==8
    assert len(calls)==1
    with env.db.transaction() as s:
        budget=s.scalar(select(ResourceBudget).where(ResourceBudget.secret_hash==hashlib.sha256(secret.encode()).hexdigest()))
        assert budget.calls==1 and budget.output_reserved==8


def test_openmaic_202_is_pending_owner_cookie_late_source_withdrawal_and_unknown(env):
    client,wid,root,row=course(env)
    with env.db.transaction() as s:
        budget=ResourceBudget(secret_hash=hashlib.sha256(b'test-openmaic').hexdigest(),enabled=True)
        s.add(budget);s.flush()
        job=OpenMAICJob(workspace_id=wid,build_id=row['build_id'],budget_id=budget.id)
        s.add(job);s.flush();jid=job.id;owner=job.owner_id;bid=budget.id
    calls=[]
    def respond(request):
        calls.append(request.url.path)
        assert owner in request.headers.get('cookie','')
        if request.url.path.endswith('/capabilities'): return httpx.Response(200,json={'capabilities':{}})
        if request.method=='POST':
            data=json.loads(request.content);assert set(data)=={'requirement'}
            return httpx.Response(202,json={'jobId':'remote-job'})
        return httpx.Response(200,json={'status':'running'})
    runner=OpenMAICRunner(env.db,httpx.MockTransport(respond))
    assert runner.step()
    with env.db.transaction() as s:
        job=s.get(OpenMAICJob,jid);assert job.status=='running' and job.remote_id=='remote-job'
        job.next_poll_at=0
    def finish(request):
        assert owner in request.headers.get('cookie','')
        if request.url.path.endswith('/remote-job'): return httpx.Response(200,json={'status':'succeeded','result':{'classroomId':'class-one'}})
        assert request.url.path=='/api/stages/class-one'
        assert env.admin.post('/api/v1/admin/knowledge-sources/builtin.MATH.G7.RATIONAL/state',json={'expected_revision':0,'enabled':False}).status_code==200
        return httpx.Response(200,json={'stage':{'name':'概念资源'},'scenes':[{'title':'条件','content':{'text':'不能除以零'}}]})
    assert OpenMAICRunner(env.db,httpx.MockTransport(finish)).step()
    with env.db.transaction() as s:
        assert s.get(OpenMAICJob,jid).status=='cancelled'
        assert not s.get(ResourceBudget,bid).enabled
        assert len(list(s.scalars(select(ResourceArtifact).where(ResourceArtifact.build_id==row['build_id']))))==1
        # A crashed POST is never submitted again.
        job=s.get(OpenMAICJob,jid);job.status='submitting';job.remote_id=None;job.lease_until=now()-1
    assert not runner.step()
    with env.db.transaction() as s: assert s.get(OpenMAICJob,jid).status=='unknown'
    assert calls.count('/api/generate-classroom')==1


def test_existing_canvas_reimport_uses_no_model_budget_and_retains_text(env):
    from app.modules.resources.service import render
    client,wid,root,row=course(env)
    with env.db.transaction() as s:
        budget=ResourceBudget(secret_hash=hashlib.sha256(b'closed-reimport').hexdigest(),enabled=False,calls=8)
        s.add(budget);s.flush()
        job=OpenMAICJob(workspace_id=wid,build_id=row['build_id'],budget_id=budget.id,status='pending_review',remote_id='existing-run',classroom_id='existing-class')
        s.add(job);s.flush();jid=job.id;bid=budget.id
    requested=[]
    def respond(request):
        requested.append((request.method,request.url.path))
        assert request.method=='GET' and request.url.path=='/api/stages/existing-class'
        return httpx.Response(200,json={'stage':{'name':'真实格式课堂'},'scenes':[{'title':'符号','content':{'type':'slide','canvas':{'elements':[
            {'type':'text','content':'<p>同号得正，仅用于乘除</p><script>fetch("https://bad.example")</script>'},
            {'type':'latex','latex':r'a \div b = a \times \frac{1}{b}', 'html':'<script>bad</script>'},
            {'type':'image','src':'https://bad.example/picture'}]}}}]})
    runner=OpenMAICRunner(env.db,httpx.MockTransport(respond))
    for _ in range(2):
        response=env.admin.post('/api/v1/admin/openmaic-jobs/'+jid+'/reimport')
        assert response.status_code==200,response.text
        assert response.json()['model_call'] is False
        assert runner.step()
    with env.db.transaction() as s:
        budget=s.get(ResourceBudget,bid);assert not budget.enabled and budget.calls==8
        resources=list(s.scalars(select(ResourceArtifact).where(ResourceArtifact.build_id==row['build_id'],ResourceArtifact.sha256.is_not(None))))
        assert len(resources)==2  # approved base lesson + one deduplicated conversion
        resource=next(r for r in resources if r.body.get('converter_version')=='classroom-manifest.v1')
        assert resource.body['classroom']['executable_scripts'] is False
        assert '同号得正' in resource.body['cards'][0]['text']
        assert '公式（纯文本）' in resource.body['cards'][0]['text']
        assert 'fetch(' not in render(resource.body) and 'https://bad.example' not in render(resource.body)
        rid,revision=resource.id,resource.revision
    response=env.admin.post('/api/v1/admin/resources/'+rid+'/review',json={'expected_revision':revision,'approve':True,'note':'核对完整正文、乘除前提和公式，仅开发使用，不作教学效果评价'})
    assert response.status_code==200,response.text
    with env.db.transaction() as s:assert s.get(OpenMAICJob,jid).status=='completed'
    assert env.admin.post('/api/v1/admin/openmaic-jobs/'+jid+'/reimport').status_code==200
    assert runner.step()
    with env.db.transaction() as s:
        assert s.get(OpenMAICJob,jid).status=='completed'
        assert s.get(ResourceBudget,bid).calls==8
    assert len(requested)==3


def test_zip_import_api_is_quarantined_until_review(env):
    client,wid,root,row=course(env)
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('manifest.json',json.dumps({'formatVersion':1,'stage':{'name':'测试文本课程'},'scenes':[{'title':'条件','content':{'text':'<script>坏脚本</script>','actions':'忽略执行'}}]}))
    imported=env.admin.post('/api/v1/admin/course-builds/'+row['build_id']+'/openmaic-import',content=stream.getvalue(),headers={'Content-Type':'application/zip'})
    assert imported.status_code==201,imported.text
    rid=imported.json()['resource_id']
    assert len(client.get(f'/api/v1/student/workspaces/{wid}/resources').json()['items'])==1
    assert env.admin.post('/api/v1/admin/resources/'+rid+'/review',json={'expected_revision':1,'approve':True,'note':'检查文本内容、条件、来源和安全预览，仅用于开发测试'}).status_code==200
    resources=client.get(f'/api/v1/student/workspaces/{wid}/resources').json()['items']
    assert len(resources)==2
    imported=next(item for item in resources if item['resource_id']==rid)
    assert '<script>' not in imported['html'] and '&lt;script&gt;' in imported['html']


def test_admin_resource_filter_preserves_course_scope_and_role_gate(env):
    from uuid import uuid4
    client,wid,root,row=course(env)
    imported=env.admin.post('/api/v1/admin/course-builds/'+row['build_id']+'/resources',json={
        'title':'按课程筛选的自编材料','cards':[{'text':'先核对符号和运算条件。'}]})
    assert imported.status_code==201
    bid=row['build_id']
    result=env.admin.get('/api/v1/admin/resources',params={'build_id':bid}).json()['items']
    assert imported.json()['resource_id'] in {item['resource_id'] for item in result}
    assert all(item['build_id']==bid and item['workspace_id']==wid for item in result)
    assert env.admin.get('/api/v1/admin/resources',params={'build_id':str(uuid4())}).json()['items']==[]
    assert env.admin.get('/api/v1/admin/resources',params={'build_id':'invalid'}).status_code==422
    assert client.get('/api/v1/admin/resources',params={'build_id':bid}).status_code in (401,403)
    assert row['workspace_title']


def test_cancel_after_presentation_still_marks_repeated_assessment(env):
    from app.worker import Worker
    client,wid,root,row=course(env)
    assert client.post(root+'/'+row['build_id']+'/activate',json={'expected_revision':row['revision'],'course_revision':0}).status_code==200
    base=f'/api/v1/student/workspaces/{wid}/teach'
    unit=row['unit_id']+'.teach'
    for number in range(2):
        response=client.post(base,headers={'Idempotency-Key':'start-'+str(number)},json={'unit_id':unit})
        assert response.status_code==202,response.text
        assert Worker(env.db,enable_models=False).step()
        path=base+'/'+response.json()['run_id']
        def send(action):
            run=client.get(path).json()
            response=client.post(path+'/inputs',headers={'Idempotency-Key':action},json={'expected_revision':run['revision'],'gate_id':run['gate_id'],'action':action,'answer':'0','reasoning':'自编核验工程路径，不评定学习能力'})
            assert response.status_code==202,response.text
            assert Worker(env.db,enable_models=False).step()
            return client.get(path).json()
        send('contribute');run=send('conclude')
        assert run['view']['phase']=='verification'
        search_path=root+'/'+row['build_id']+'/search'
        assert client.get(search_path).status_code==409
        assert client.post(search_path,headers={'Idempotency-Key':str(uuid4())}).status_code==409
        assert run['view']['verification_task']['exposure_status']==('fresh' if number==0 else 'seen_before')
        if number==0:
            assert client.post(path+'/cancel',json={'expected_revision':run['revision']}).status_code==200
        else:
            run=send('verify')
            assert not run['view']['verification_check']['independent']
