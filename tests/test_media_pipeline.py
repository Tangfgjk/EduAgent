"""No paid calls: exercise the HTTP boundary, durable budget and private assets."""
import io
import json
import struct
import wave
import zlib
import zipfile
from copy import deepcopy
import httpx
import pytest
from sqlalchemy import select
from tests.test_s02 import database,env
from tests.test_generation_boundaries import course
from tests.test_classroom_manifest import document
from app.modules.resources import media
from app.infrastructure.db.models import MediaOperation,MediaBudget,ResourceArtifact,now


def classroom(env):
    client,wid,_,build=course(env)
    assert client.post(f'/api/v1/student/workspaces/{wid}/course',json={'expected_revision':0,'topic':'负数',
        'title':'自编媒体课堂','unit_ids':[build['unit_id']],'active_unit_id':build['unit_id']}).status_code==200
    doc=document();doc['scenes']=[{**deepcopy(doc['scenes'][0]),'id':f's{i}'} for i in range(3)]
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as archive:archive.writestr('manifest.json',json.dumps({**doc,'formatVersion':1}))
    response=env.admin.post(f"/api/v1/admin/course-builds/{build['build_id']}/openmaic-import",content=data.getvalue(),headers={'Content-Type':'application/zip'})
    assert response.status_code==201,response.text
    rid=response.json()['resource_id']
    assert env.admin.post('/api/v1/admin/resources/'+rid+'/review',json={'expected_revision':1,'approve':True,'note':'自编三场景开发素材，离线测试模型媒体接口与归属边界'}).status_code==200
    budget=env.admin.post(f'/api/v1/admin/resources/{rid}/media-budget').json()
    return client,wid,rid,budget


def png():
    def chunk(tag,value):return struct.pack('>I',len(value))+tag+value+struct.pack('>I',zlib.crc32(tag+value))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1280,720,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\xff'*3840)*720))+chunk(b'IEND',b'')


def wav(seconds=1):
    stream=io.BytesIO()
    with wave.open(stream,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\0\0'*int(8000*seconds))
    return stream.getvalue()


def test_closed_budget_no_calls_then_mock_generation_is_idempotent_and_scoped(env,monkeypatch,tmp_path):
    monkeypatch.setenv('WENJIN_BAILIAN_API_HOST','https://dashscope.aliyuncs.com')
    monkeypatch.setenv('WENJIN_QWEN_KEY','test-only-media-key');monkeypatch.setattr(media,'ROOT',tmp_path/'assets')
    client,wid,rid,budget=classroom(env);bid=budget['budget_id']
    root=f'/api/v1/student/workspaces/{wid}/media-budgets/{bid}/jobs'
    assert not budget['enabled']
    assert client.post(root,json={'kind':'image','index':0}).status_code==409
    assert client.put('/api/v1/admin/media-budgets/'+bid,json={'expected_revision':0,'enabled':True}).status_code==403
    approved=env.admin.put('/api/v1/admin/media-budgets/'+bid,json={'expected_revision':0,'enabled':True,'acknowledge_five_yuan_and_verified_prices':True})
    assert approved.status_code==200,approved.text
    before=client.get(f'/api/v1/student/workspaces/{wid}/resources').json()['items']
    before=next(r for r in before if r['resource_id']==rid)
    first=client.post(root,json={'kind':'image','index':0});assert first.status_code==202,first.text
    assert client.post(root,json={'kind':'image','index':0}).json()==first.json()
    calls=[]
    def respond(request):
        calls.append(request)
        if request.url.host=='dashscope.aliyuncs.com':
            assert request.headers['Authorization']=='Bearer test-only-media-key'
            payload=json.loads(request.content);assert payload['parameters']['prompt_extend'] is False
            return httpx.Response(200,json={'request_id':'mock-request','output':{'choices':[{'message':{'content':[{'image':'https://fixtures.oss-cn-beijing.aliyuncs.com/image.png'}]}}]}})
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        return httpx.Response(200,content=png())
    runner=media.MediaRunner(env.db,httpx.MockTransport(respond));assert runner.step();assert not runner.step()
    assert len(calls)==2
    state=client.get(f'/api/v1/student/workspaces/{wid}/media-budgets').json()['items'][0]
    assert state['body']['reserved_microyuan']==200000 and state['operations'][0]['status']=='completed'
    resource=next(r for r in client.get(f'/api/v1/student/workspaces/{wid}/resources').json()['items'] if r['resource_id']==rid)
    assert resource['sha256']==before['sha256']  # approved immutable lesson is unchanged
    assert resource['classroom']['quarantined_media']==1
    aid=state['operations'][0]['operation_id']
    assert client.get(f'/api/v1/student/workspaces/{wid}/media-assets/{aid}').status_code==409
    review=env.admin.put('/api/v1/admin/media-assets/'+aid+'/visual-review',json={'approved':True,'text_correct':True,'math_consistent':True,'teaching_purpose':'自编夹具配图，无文字错误；用于课堂场景示意。'})
    assert review.status_code==200,review.text
    resource=next(r for r in client.get(f'/api/v1/student/workspaces/{wid}/resources').json()['items'] if r['resource_id']==rid)
    src=resource['classroom']['scenes'][0]['media'][0]['src']
    assert client.get(src).content==png()
    # Native img/audio/video requests carry the role cookie but no custom header.
    # Fixture account names are generated; re-use its authenticated session as the role cookie.
    legacy=client.cookies.get('wenjin_session')
    client.cookies.set('wenjin_student_session',legacy)
    client.cookies.delete('wenjin_session')
    assert client.get(src).content==png()
    client.cookies.set('wenjin_session',legacy)
    other,_=env.student('media.other');assert other.get(src).status_code==404
    assert env.admin.get(src).status_code==403
    assert client.post(root,json={'kind':'image','index':2}).status_code==409


def test_submit_outcome_unknown_is_never_retried_and_pause_fences_queued_work(env,monkeypatch,tmp_path):
    monkeypatch.setenv('WENJIN_QWEN_KEY','test-only-media-key');monkeypatch.setattr(media,'ROOT',tmp_path/'assets')
    client,wid,rid,budget=classroom(env);bid=budget['budget_id']
    env.admin.put('/api/v1/admin/media-budgets/'+bid,json={'expected_revision':0,'enabled':True,'acknowledge_five_yuan_and_verified_prices':True})
    root=f'/api/v1/student/workspaces/{wid}/media-budgets/{bid}/jobs'
    assert client.post(root,json={'kind':'video','index':0}).status_code==202
    calls=[]
    def fail(request):calls.append(request);raise httpx.ReadTimeout('mock outcome unknown')
    runner=media.MediaRunner(env.db,httpx.MockTransport(fail));assert runner.step();assert not runner.step()
    state=client.get(f'/api/v1/student/workspaces/{wid}/media-budgets').json()['items'][0]
    assert state['operations'][0]['status']=='unknown' and state['body']['reserved_microyuan']==700000
    assert client.post(root,json={'kind':'video','index':0}).status_code==202
    assert not runner.step() and len(calls)==1
    queued=client.post(root,json={'kind':'audio','index':0}).json()
    env.admin.put('/api/v1/admin/media-budgets/'+bid,json={'expected_revision':queued['revision'],'enabled':False})
    assert runner.step();assert len(calls)==1


def test_audio_measured_and_remote_download_hosts_are_bounded():
    assert media.inspect_asset(wav(), 'audio','audio/wav')['duration_seconds']==1
    with pytest.raises(ValueError):media.inspect_asset(wav(31),'audio','audio/wav')
    for url in ('https://aliyuncs.com.evil.test/a','https://a.aliyuncs.com@evil.test/a','http://a.aliyuncs.com/a','https://127.0.0.1/a','https://a.aliyuncs.com:4003/a'):
        with pytest.raises(ValueError):media.download_url(url)



@pytest.mark.parametrize('change',['course','independent'])
def test_queued_media_rechecks_course_and_independent_before_any_external_call(env,monkeypatch,change):
    from app.infrastructure.db.models import CourseBinding,AgentRun
    monkeypatch.setenv('WENJIN_QWEN_KEY','test-only-media-key')
    client,wid,rid,budget=classroom(env);bid=budget['budget_id']
    assert env.admin.put('/api/v1/admin/media-budgets/'+bid,json={'expected_revision':0,'enabled':True,'acknowledge_five_yuan_and_verified_prices':True}).status_code==200
    assert client.post(f'/api/v1/student/workspaces/{wid}/media-budgets/{bid}/jobs',json={'kind':'image','index':0}).status_code==202
    with env.db.transaction() as session:
        if change=='course':session.get(CourseBinding,wid).active_unit_id='math.g7.equations.dev1'
        else:
            # A live diagnostic closes access to auxiliary resources.
            from app.modules.generation import service
            monkeypatch.setattr(service,'independent_active',lambda session,workspace_id:True)
    calls=[]
    runner=media.MediaRunner(env.db,httpx.MockTransport(lambda request:calls.append(request)))
    assert runner.step() and calls==[]
    state=client.get(f'/api/v1/student/workspaces/{wid}/media-budgets').json()['items'][0]
    assert state['operations'][0]['status']=='cancelled'
