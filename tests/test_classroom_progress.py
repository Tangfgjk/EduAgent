import io,json,zipfile
from tests.test_s02 import database,env
from tests.test_generation_boundaries import course
from tests.test_classroom_manifest import document
from app.worker import Worker


def test_classroom_resume_math_interaction_and_independent_isolation(env):
    client,wid,root,row=course(env)
    assert client.post(f'/api/v1/student/workspaces/{wid}/course',json={'expected_revision':0,'topic':'负数运算',
        'title':'开发课程','unit_ids':[row['unit_id']],'active_unit_id':row['unit_id']}).status_code==200
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as archive:archive.writestr('manifest.json',json.dumps({**document(),'formatVersion':1},ensure_ascii=False))
    response=env.admin.post('/api/v1/admin/course-builds/'+row['build_id']+'/openmaic-import',content=data.getvalue(),headers={'Content-Type':'application/zip'})
    assert response.status_code==201,response.text
    rid=response.json()['resource_id']
    assert env.admin.post('/api/v1/admin/resources/'+rid+'/review',json={'expected_revision':1,'approve':True,'note':'自编课堂布局公式与互动任务检查通过，不作为独立评价'}).status_code==200
    resources=client.get(f'/api/v1/student/workspaces/{wid}/resources').json()['items']
    resource=next(r for r in resources if r['resource_id']==rid)
    assert 'answer' not in resource['classroom']['scenes'][0]['actions'][2]
    path=f'/api/v1/student/workspaces/{wid}/classrooms/{rid}/progress'
    assert client.get(path).json()['revision']==0
    body={'expected_revision':0,'resource_hash':resource['sha256'],'scene':0,'action':2,'question_id':'q1','answer':'这个答案是3'}
    response=client.put(path,json=body,headers={'Idempotency-Key':'answer-one'})
    assert response.status_code==200,response.text
    assert response.json()['body']['check']['status']=='passed'
    assert client.put(path,json=body,headers={'Idempotency-Key':'answer-one'}).json()['revision']==1
    assert client.get(path).json()['body']['action']==2
    changed={**body,'expected_revision':1,'action':0,'question_id':None,'answer':'','media_positions':{'0:audio-1':2.5}}
    moved=client.put(path,json=changed,headers={'Idempotency-Key':'position-two'})
    assert moved.status_code==200,moved.text
    assert moved.json()['body']['check']['status']=='passed' and moved.json()['body']['student_answer']=='这个答案是3'
    assert client.get(path).json()['body']['media_positions']=={'0:audio-1':2.5}
    assert client.put(path,json=body,headers={'Idempotency-Key':'answer-one'}).json()['revision']==1
    assert client.get(path).json()['revision']==2
    other,_=env.student('other.classroom')
    assert other.get(path).status_code==404
    started=client.post(f'/api/v1/student/workspaces/{wid}/tutoring',headers={'Idempotency-Key':'diagnose'},json={'unit_id':row['unit_id']})
    assert started.status_code==202,started.text
    assert Worker(env.db,enable_models=False).step()
    assert client.get(path).status_code==409
    assert client.put(path,json=body,headers={'Idempotency-Key':'cannot-use-in-test'}).status_code==409
