import json,hashlib
import pytest
from copy import deepcopy
import httpx
from sqlalchemy import select
from tests.test_s02 import database,env
from tests.test_generation_boundaries import course
from tests.test_classroom_manifest import document
from app.infrastructure.db.models import ResourceBudget,GenerationPolicy,ModelOperation,ResourceArtifact,OpenMAICJob
from app.modules.resources.openmaic import OpenMAICRunner
from app.infrastructure.runtime.model_tasks import ModelTaskRunner
from app.worker import Worker


@pytest.mark.parametrize('outcome',['pass','revise','quota','timeout','student_cancel','admin_cancel'])
def test_student_requests_classroom_and_two_checked_private_reviews_open_without_admin(env,monkeypatch,tmp_path,outcome):
    client,wid,root,build=course(env)
    assert client.post(f'/api/v1/student/workspaces/{wid}/course',json={'expected_revision':0,'topic':'有理数','title':'开发课堂',
        'unit_ids':[build['unit_id']],'active_unit_id':build['unit_id']}).status_code==200
    monkeypatch.chdir(tmp_path);(tmp_path/'.local').mkdir();(tmp_path/'.local/openmaic-proxy.key').write_text('fake-service-only')
    with env.db.transaction() as s:
        s.get(GenerationPolicy,1).private_auto_release=True
        s.add(ResourceBudget(secret_hash=hashlib.sha256(b'fake-service-only').hexdigest(),enabled=True))
    path=root+'/'+build['build_id']+'/openmaic'
    response=client.post(path);assert response.status_code==202,response.text
    assert client.post(path).json()==response.json()
    doc=document();doc['scenes']=[{**deepcopy(doc['scenes'][0]),'id':f's{i}','actions':[{'type':'speech','text':'零的相反数仍为零；先核对适用条件。'}]} for i in range(3)]
    submits=[]
    def transport(request):
        if request.url.path.endswith('/capabilities'):return httpx.Response(200,json={'capabilities':{}})
        if request.method=='POST':submits.append(request);return httpx.Response(200,json={'jobId':'mock-remote'})
        if request.url.path.startswith('/api/stages/'):return httpx.Response(200,json=doc)
        return httpx.Response(200,json={'status':'succeeded','result':{'classroomId':'mock-classroom'}})
    assert OpenMAICRunner(env.db,httpx.MockTransport(transport)).step()
    assert len(submits)==1 and client.get(path).json()['items'][0]['status']=='checking'
    if outcome=='quota':
        from app.modules.resources import quality
        from app.modules.errors import DomainError
        original=quality._enqueue
        def exhausted(session,resource,index):
            if index==2:raise DomainError(429,'model_budget_exhausted','模拟第二次检查额度不足')
            return original(session,resource,index)
        monkeypatch.setattr(quality,'_enqueue',exhausted)
    for index in (1,2):
        assert Worker(env.db,enable_models=False).step()
        with env.db.transaction() as s:
            op=s.scalar(select(ModelOperation).where(ModelOperation.request['purpose'].as_string()=='resource_semantic_review',ModelOperation.status=='pending'))
            assert op and op.request['review_index']==index;oid=op.id
        runner=ModelTaskRunner(env.db)
        if outcome in {'student_cancel','admin_cancel'}:
            claim=runner.claim(operation_id=oid);assert claim
            job_id=client.get(path).json()['items'][0]['job_id']
            cancelled=(client.post(path+'/'+job_id+'/cancel') if outcome=='student_cancel' else env.admin.post('/api/v1/admin/openmaic-jobs/'+job_id+'/cancel'))
            assert cancelled.status_code==200,cancelled.text
            from app.llm.provider import GenerationResult
            from app.modules.generation.quality import CHECKS
            result=GenerationResult(provider='fake',requested_model='scripted',text=json.dumps({'verdict':'pass','checks':{k:True for k in CHECKS},'issues':[]}),finish_reason='stop',elapsed_ms=1,simulated=True)
            assert runner.complete(claim,result=result) is False
            assert client.get(path).json()['items'][0]['status']=='cancelled'
            with env.db.transaction() as session:
                op=session.get(ModelOperation,oid)
                assert op.status=='cancelled' and op.billing_unknown is True
            break
        if outcome=='revise':
            from app.llm.provider import FakeProvider
            from app.modules.generation.quality import CHECKS
            runner=ModelTaskRunner(env.db,lambda config,snapshot:FakeProvider((json.dumps({'verdict':'revise','checks':{k:True for k in CHECKS},'issues':['课堂条件尚需复核']}),)))
        if outcome=='timeout':
            from app.llm.provider import FakeProvider,ProviderError
            runner=ModelTaskRunner(env.db,lambda config,snapshot:FakeProvider((ProviderError('timeout'),)))
        assert runner.step(operation_id=oid)
        if outcome!='pass':break
    status=client.get(path).json()['items'][0]
    if outcome not in {'pass','student_cancel','admin_cancel'}:
        assert status['status']=='pending_review' and status['error_code']
    elif outcome=='pass':assert status['status']=='completed'
    resources=client.get(f'/api/v1/student/workspaces/{wid}/resources').json()['items']
    assert any(r['classroom'] for r in resources) is (outcome=='pass')
    other,_=env.student('private.classroom.other');assert other.get(path).status_code==404
