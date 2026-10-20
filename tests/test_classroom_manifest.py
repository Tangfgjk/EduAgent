import base64
import pytest
from app.modules.resources.classroom import project,public_manifest,media,check_question
from app.modules.errors import DomainError


def document():
    return {'stage':{'name':'自编互动课堂'},'scenes':[{'id':'s1','title':'相反数与零','content':{'canvas':{
        'viewportSize':1000,'viewportRatio':.5625,'elements':[
            {'id':'title','type':'text','left':10,'top':20,'width':900,'height':80,'content':'<p style="font-size:36px">相反数与零</p><script>steal()</script>'},
            {'id':'formula','type':'latex','left':20,'top':100,'width':700,'height':90,'latex':r'a\div b=a\times\frac{1}{b}'},
            {'id':'remote','type':'image','src':'https://evil.invalid/track'},
            {'id':'script','type':'iframe','src':'javascript:alert(1)'}]},},
        'actions':[{'type':'spotlight','elementId':'formula'},{'type':'speech','text':'零的相反数仍是零。'},
            {'type':'question','id':'q1','text':'负三的相反数是多少？','answer':'3'},{'type':'script','text':'steal()'}]}]}


def test_layout_formula_actions_and_private_answers_are_projected_safely():
    manifest=project(document())
    scene=manifest['scenes'][0]
    assert scene['width']==1000 and scene['height']==562.5
    assert scene['elements'][0]['text']=='相反数与零'
    assert scene['elements'][0]['font_size']==36
    assert scene['elements'][1]['kind']=='latex'
    assert 'iframe' in manifest['unsupported'] and 'unavailable_image' in manifest['unsupported']
    public=public_manifest(manifest)
    assert 'answer' not in public['scenes'][0]['actions'][2]
    assert check_question(manifest,0,'q1','这个答案是3')['status']=='passed'
    assert check_question(manifest,0,'q1','不是3')['status']=='unverifiable'
    with pytest.raises(DomainError):check_question(manifest,0,'foreign-question','3')


@pytest.mark.parametrize('url',['javascript:alert(1)','http://127.0.0.1:4003/api/v1/admin/students','data:image/svg+xml;base64,PHN2Zz4=', '//external.test/asset'])
def test_executable_remote_and_business_origin_assets_are_never_loaded(url):
    assert media(url) is None
