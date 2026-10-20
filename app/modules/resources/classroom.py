"""Non-executable OpenMAIC scene projection. Never pass HTML or remote URLs through."""
import base64
import math
import re
from html.parser import HTMLParser
from app.modules.errors import DomainError
from app.modules.learning.answer_interpretation import interpret

VERSION='classroom-manifest.v1'


class Text(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.parts=[];self.blocked=0;self.font=20
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style','iframe','object'}:self.blocked+=1
        if not self.blocked:
            if tag in {'p','div','br','li'}:self.parts.append('\n')
            for key,value in attrs:
                if key=='style':
                    found=re.search(r'font-size:\s*(\d+(?:\.\d+)?)px',value or '')
                    if found:self.font=max(self.font,min(72,float(found[1])))
    def handle_endtag(self,tag):
        if tag in {'script','style','iframe','object'}:self.blocked=max(0,self.blocked-1)
    def handle_data(self,text):
        if not self.blocked:self.parts.append(text)


def plain(value):
    parser=Text();parser.feed(str(value)[:40000]);return ''.join(parser.parts).strip()[:8000],parser.font


def number(value,default=0,minimum=-4000,maximum=8000):
    return max(minimum,min(maximum,float(value))) if type(value) in {int,float} and math.isfinite(value) else default


def color(value,default='#254338'):
    return value if isinstance(value,str) and re.fullmatch(r'#[0-9a-fA-F]{3,8}',value) else default


def media(value,assets=None):
    if not isinstance(value,str):return None
    if assets and value in assets:value=assets[value]
    if not value.startswith('data:') or len(value)>8_000_000:return None
    match=re.fullmatch(r'data:(image/(?:png|jpeg|webp)|audio/(?:mpeg|wav|ogg)|video/(?:mp4|webm));base64,([A-Za-z0-9+/=]+)',value)
    if not match:return None
    try:
        data=base64.b64decode(match[2],validate=True)
        if not data:return None
        mime=match[1]
        valid=(mime=='image/png' and data.startswith(b'\x89PNG\r\n\x1a\n') or mime=='image/jpeg' and data.startswith(b'\xff\xd8\xff')
            or mime=='image/webp' and data.startswith(b'RIFF') and data[8:12]==b'WEBP'
            or mime=='audio/wav' and data.startswith(b'RIFF') and data[8:12]==b'WAVE'
            or mime=='audio/ogg' and data.startswith(b'OggS')
            or mime=='audio/mpeg' and (data.startswith(b'ID3') or len(data)>2 and data[0]==255 and data[1]&224==224)
            or mime=='video/mp4' and len(data)>12 and data[4:8]==b'ftyp'
            or mime=='video/webm' and data.startswith(b'\x1aE\xdf\xa3'))
        if not valid:return None
    except ValueError:return None
    return value


def project(document,assets=None):
    scenes=document.get('scenes',[])
    if not isinstance(scenes,list) or not 1<=len(scenes)<=30:raise DomainError(422,'invalid_classroom','场景数量不支持')
    output=[];unsupported=set();media_bytes=0
    for i,scene in enumerate(scenes):
        if not isinstance(scene,dict):raise DomainError(422,'invalid_classroom','场景结构不支持')
        content=scene.get('content',{});canvas=content.get('canvas',{}) if isinstance(content,dict) else {}
        width=number(canvas.get('viewportSize'),1000,200,4000)
        ratio=number(canvas.get('viewportRatio'),.5625,.2,3)
        elements=[]
        for n,e in enumerate(canvas.get('elements',[])[:200]):
            if not isinstance(e,dict):continue
            kind=e.get('type');eid=str(e.get('id',n))[:100]
            item={'id':eid,'kind':kind,'left':number(e.get('left')),'top':number(e.get('top')),
                'width':number(e.get('width'),100,1,4000),'height':number(e.get('height'),60,1,4000),
                'rotation':number(e.get('rotate'),0,-360,360),'color':color(e.get('defaultColor'))}
            if kind=='text':
                text,font=plain(e.get('content',''));item.update(text=text,font_size=font)
            elif kind=='latex':item.update(text=str(e.get('latex',''))[:2000],font_size=number(e.get('fontSize'),24,10,72))
            elif kind=='shape':
                item['fill']=color(e.get('fill'),'#e9f1ec')
                path=e.get('path','')
                if isinstance(path,str) and len(path)<12000 and re.fullmatch(r'[MmLlHhVvCcSsQqTtAaZz0-9., +eE-]+',path):
                    item['path']=path
                    box=e.get('viewBox',[1,1]);item['view_box']=[number(v,1,.001,4000) for v in box[:2]] if isinstance(box,list) and len(box)>=2 else [1,1]
            elif kind in {'image','video','audio'}:
                src=media(e.get('src') or e.get('url'),assets)
                if not src:unsupported.add('unavailable_'+kind);continue
                media_bytes+=len(src)
                if media_bytes>20_000_000:unsupported.add('aggregate_media_bounds');continue
                item.update(src=src,alt=str(e.get('alt','课堂媒体'))[:200],captions=plain(e.get('caption',''))[0])
            else:unsupported.add(str(kind)[:60]);continue
            elements.append(item)
        actions=[]
        for a in scene.get('actions',[])[:300]:
            if not isinstance(a,dict):continue
            if a.get('type')=='speech':actions.append({'kind':'speech','text':plain(a.get('text',''))[0]})
            elif a.get('type') in {'spotlight','show','hide'}:actions.append({'kind':a['type'],'element_id':str(a.get('elementId',''))[:100]})
            elif a.get('type')=='question':
                # Numeric answer stays server-side; client gets prompt only.
                actions.append({'kind':'question','id':str(a.get('id','q'+str(len(actions))))[:100],
                    'text':plain(a.get('text',''))[0],'answer':str(a.get('answer',''))[:128]})
            else:unsupported.add('action_'+str(a.get('type'))[:60])
        output.append({'id':str(scene.get('id',i))[:100],'title':plain(scene.get('title',''))[0][:160],
            'width':width,'height':width*ratio,'background':color((canvas.get('background') or {}).get('color') if isinstance(canvas.get('background'),dict) else '', '#ffffff'),
            'elements':elements,'actions':actions})
    return {'schema_version':VERSION,'title':plain(document.get('stage',{}).get('name','课堂'))[0][:160],
        'scenes':output,'unsupported':sorted(unsupported),'executable_scripts':False,'external_network':False}


def public_manifest(value):
    return {**value,'scenes':[{**s,'actions':[{k:v for k,v in a.items() if k!='answer'} for a in s['actions']]} for s in value['scenes']]}


def check_question(manifest,scene_index,question_id,answer):
    try:
        scene=manifest['scenes'][scene_index]
        task=next(a for a in scene['actions'] if a['kind']=='question' and a['id']==question_id)
    except (IndexError,StopIteration,KeyError):raise DomainError(404,'question_not_found','未找到此课堂互动') from None
    result=interpret(answer)
    return {'status':result.check(task['answer']),'reasoning_reviewed':False,'learning_effect_validated':False}
