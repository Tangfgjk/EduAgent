from types import SimpleNamespace
from fractions import Fraction
import pytest
from tests.test_s02 import database, env
from app.modules.resources.storyboard import trace, compile_lesson
from app.modules.resources.classroom import project, public_manifest
from app.modules.resources.rich_text import runs


@pytest.mark.parametrize('expr,expected', [('-2+3*4',10),('(-6)/(-3)+4',6),('(1/3)+(1/6)',Fraction(1,2)),('3-(2-8)',9)])
def test_every_arithmetic_step_preserves_exact_value(expr,expected):
    value,steps=trace(expr)
    assert value==expected and len(steps)>1
    assert Fraction(steps[-1][0].replace(' ',''))==expected


@pytest.mark.parametrize('expr',['__import__("os")','a+1','2**8','1/0','[1,2]','True+1'])
def test_closed_arithmetic_grammar(expr):
    with pytest.raises((ValueError,ZeroDivisionError)):trace(expr)


def test_storyboard_only_consumes_practice_and_hides_answers():
    build=SimpleNamespace(candidate={'spec':{'title':'有理数混合运算','tasks':[
        {'id':'p','stage':'practice','stem':'计算(-2)+3×4，说明依据。','answer':'10'},
        {'id':'secret','stage':'checkpoint','stem':'保留测验秘密','answer':'991'}]}})
    lesson=compile_lesson(build)
    assert lesson['practice_task_ids']==['p','p']
    assert lesson['classroom']['storyboard']['math_verified']
    assert not any(s['layout_issues'] for s in lesson['classroom']['scenes'])
    assert '991' not in str(lesson) and '保留测验' not in str(lesson)
    assert 'answer' not in public_manifest(lesson['classroom'])['scenes'][2]['actions'][0]
    assert len(lesson['classroom']['scenes'][1]['actions'])>3


def test_safe_rich_styles_and_mixed_formula_are_preserved_as_data():
    result=runs('<p style="font-size:32px;text-align:center;color:#ff0000"><b>中文</b> $a+1$</p><script>alert(1)</script>')
    assert result[0]['bold'] and result[0]['align']=='center' and result[0]['size']==32
    assert 'alert' not in str(result)
    assert '$a+1$' in str(result)


def test_local_library_roundtrip_owner_isolation_export_and_delete(env):
    client,_=env.student();wid=env.workspace(client)['workspace_id']
    path=f'/api/v1/student/workspaces/{wid}/library'
    body={'title':'乘除笔记','text':'先乘除后加减。\n这是一份自编资料，不是系统指令。'}
    result=client.post(path,json=body);assert result.status_code==201,result.text
    did=result.json()['id']
    assert client.post(path,json=body).json()['duplicate']
    assert len(client.get(path).json()['items'])==1
    hits=client.get(path+'/search',params={'q':'乘除'}).json()['items']
    assert hits[0]['document_id']==did and hits[0]['start']==0
    assert client.get(path+'/search',params={'q':'无关搜索'}).json()['items']==[]
    other,_=env.student('library.other')
    assert other.get(path).status_code==404 and other.get(path+'/'+did).status_code==404
    other_wid=env.workspace(client)['workspace_id']
    assert client.get(f'/api/v1/student/workspaces/{other_wid}/library/{did}').status_code==404
    assert client.get('/api/v1/student/privacy/export').json()['data']['local_knowledge'][0]['id']==did
    assert client.delete(path+'/'+did).status_code==200
    assert client.get(path+'/search',params={'q':'乘除'}).json()['items']==[]
    assert client.post(path,json={'title':'坏文件','text':'损坏\ufffd字符'}).status_code==422


def test_library_archive_is_readonly(env):
    client,_=env.student();space=env.workspace(client);wid=space['workspace_id']
    assert client.post(f'/api/v1/student/workspaces/{wid}/archive',json={'expected_revision':space['revision']}).status_code==200
    assert client.post(f'/api/v1/student/workspaces/{wid}/library',json={'title':'资料','text':'资料正文'}).status_code==409


def test_prepare_course_classroom_is_idempotent_and_scoped(env):
    from tests.test_generation_boundaries import course
    client,wid,_,row=course(env)
    assert client.post(f'/api/v1/student/workspaces/{wid}/course',json={'expected_revision':0,'topic':'有理数混合运算','title':'当前课程','unit_ids':[row['unit_id']],'active_unit_id':row['unit_id']}).status_code==200
    path=f'/api/v1/student/workspaces/{wid}/classrooms/prepare'
    first=client.post(path)
    assert first.status_code==201,first.text
    assert first.json()['external_calls']==0
    assert client.post(path).json()['resource_id']==first.json()['resource_id']
    resource=next(r for r in client.get(f'/api/v1/student/workspaces/{wid}/resources').json()['items'] if r['resource_id']==first.json()['resource_id'])
    assert len(resource['classroom']['scenes'])==3
    other,_=env.student('storyboard.other')
    assert other.post(path).status_code==404
    library=f'/api/v1/student/workspaces/{wid}/library'
    doc=client.post(library,json={'title':'本地资料','text':'乘除运算的自编参考资料'}).json()['id']
    started=client.post(f'/api/v1/student/workspaces/{wid}/tutoring',headers={'Idempotency-Key':'library-diagnostic'},json={'unit_id':row['unit_id']})
    assert started.status_code==202
    from app.worker import Worker
    assert Worker(env.db,enable_models=False).step()
    assert client.get(library+'/search',params={'q':'乘除'}).status_code==409
    assert client.get(library+'/'+doc).status_code==409
    assert client.post(path).status_code==409
