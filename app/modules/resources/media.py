"""Fixed first-run media envelope. Currency/call reservations precede external I/O.

Unknown submissions are never retried. Asset downloads never receive API keys.
The existing OpenMAIC text budget is neither read nor changed by this module.
"""
import hashlib
import os
import json
import re
import io
import struct
import wave
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from sqlalchemy import select
from app.infrastructure.db.models import (MediaBudget,MediaOperation,MediaAsset,ResourceArtifact,CourseBuild,
    Workspace,Account,AuditEvent,now,new_id)
from app.modules.learning.content_repository import content_hash
from app.modules.errors import DomainError

VERSION='bailian-beijing-media.v1'
CAPS={'image':2,'audio':3,'video':1}
GROUP_CAP={'image':1_000_000,'audio':1_000_000,'video':2_000_000}
MODELS={'image':'qwen-image-2.0-2026-03-03','audio':'qwen3-tts-flash','video':'wan2.2-t2v-plus'}
PRICE={'image':200_000,'audio':80,'video':700_000}  # microyuan/image, microyuan/character, microyuan/5s
ROOT=Path('.local/media-assets')
BASE='https://dashscope.aliyuncs.com'


def api_host():
    """Only Beijing provider hosts may receive the media credential."""
    path = Path('.local/providers/qwen.host')
    value = os.getenv('WENJIN_BAILIAN_API_HOST', '').strip()
    if not value and path.is_file():
        value = path.read_text(encoding='utf-8').strip()
    value = (value or BASE).rstrip('/')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.username or parsed.password or
            parsed.port not in (None, 443) or parsed.path or parsed.query or parsed.fragment or
            not (parsed.hostname == 'dashscope.aliyuncs.com' or
                 re.fullmatch(r'ws-[a-z0-9]+\.cn-beijing\.maas\.aliyuncs\.com', parsed.hostname or ''))):
        raise ValueError('invalid_bailian_beijing_host')
    return value


def fail(code,message):raise DomainError(409,code,message)


def sources(session,budget):
    resource=session.get(ResourceArtifact,budget.resource_id)
    build=session.get(CourseBuild,resource.build_id) if resource else None
    space=session.get(Workspace,budget.workspace_id)
    owner=session.get(Account,space.owner_id) if space else None
    if not resource or resource.workspace_id!=budget.workspace_id or resource.status!='approved' or not build or build.status!='ready' or not owner or not owner.enabled or owner.source_kind not in {'synthetic','adult_test'} or space.status!='active':fail('media_source_unavailable','媒体课稿、归属或开发范围已变化')
    from app.infrastructure.db.models import CourseBinding
    from app.modules.generation.service import independent_active
    binding=session.get(CourseBinding,budget.workspace_id)
    if not binding or binding.active_unit_id!=build.unit_id:fail('course_scope_conflict','当前课程已变化')
    if independent_active(session,budget.workspace_id):fail('independent_check','独立尝试期间不生成或开放辅助媒体')
    from app.modules.knowledge.service import check_snapshot
    check_snapshot(session,build.sources)
    if resource.body.get('media_base_hash',resource.sha256)!=budget.body['resource_hash']:fail('media_source_changed','课稿版本已变化')
    return resource,owner


def propose(session,actor,resource):
    if actor.account.role!='admin':raise DomainError(403,'forbidden','仅管理员配置平台媒体预算')
    if resource.status!='approved' or not resource.body.get('classroom'):fail('classroom_required','请先安全导入含场景的已开放课堂')
    if len(resource.body['classroom']['scenes'])!=3:fail('media_scene_bounds','首轮仅支持三场景课堂')
    existing=session.scalar(select(MediaBudget).where(MediaBudget.resource_id==resource.id))
    if existing:return public(session,existing)
    row=MediaBudget(workspace_id=resource.workspace_id,resource_id=resource.id,enabled=False,revision=0,
        body={'version':VERSION,'resource_hash':resource.body.get('media_base_hash',resource.sha256),
            'total_cap_microyuan':5_000_000,'reserved_microyuan':0,'prices_microyuan':PRICE,
            'price_source':'https://help.aliyun.com/zh/model-studio/model-pricing','price_checked_at':None,
            'caps':CAPS,'group_caps_microyuan':GROUP_CAP,'region':'cn-beijing','approved_at':None,
            'notice':'未授权费用；不能生成媒体。输出内容未专业审核，不能作数学真值。'})
    session.add(row);session.flush();return public(session,row)


def public(session,row):
    operations=list(session.scalars(select(MediaOperation).where(MediaOperation.budget_id==row.id).order_by(MediaOperation.created_at,MediaOperation.slot)))
    return {'budget_id':row.id,'resource_id':row.resource_id,'workspace_id':row.workspace_id,'enabled':row.enabled,
        'revision':row.revision,'body':row.body,'operations':[{'operation_id':o.id,'slot':o.slot,'kind':o.request['kind'],
            'status':o.status,'reserved_microyuan':o.reserved_microyuan,'receipt':o.receipt} for o in operations]}


def authorize(session,actor,row,revision,enabled,ack):
    if actor.account.role!='admin':raise DomainError(403,'forbidden','仅管理员配置平台媒体预算')
    if row.revision!=revision:fail('revision_conflict','媒体预算已变化')
    if enabled:
        if not ack:fail('media_budget_confirmation_required','需要明确确认独立5元预算与当日账号价格、权限')
        from app.llm.catalog import credential
        if not credential('qwen'):fail('media_key_missing','请先在本机配置百炼北京密钥，勿在网页填写')
        if row.body.get('approved_at') is not None:fail('media_run_already_authorized','首轮批次不能续期或追加；需另行确认新预算')
        sources(session,row)
        row.body={**row.body,'approved_at':now(),'price_checked_at':now(),'expires_at':now()+86400,'notice':'一次独立媒体验收；最多6次提交、零自动重试，已发请求预占不退回。'}
    row.enabled=enabled;row.revision+=1
    session.add(AuditEvent(actor_id=actor.account.id,action='media.authorize' if enabled else 'media.pause',target_id=row.id))
    return public(session,row)


def reserve(session,actor,row,kind,index):
    from app.modules.workspaces.service import Workspaces
    Workspaces().owned(session,actor,row.workspace_id,active=True)
    if kind not in CAPS or type(index) is not int or not 0<=index<CAPS[kind]:fail('media_slot_invalid','首轮媒体槽位不支持')
    slot=kind+':'+str(index)
    existing=session.scalar(select(MediaOperation).where(MediaOperation.budget_id==row.id,MediaOperation.slot==slot))
    if existing:return public(session,row)
    if not row.enabled or now()>=row.body.get('expires_at',0):fail('media_budget_paused','媒体预算尚未授权、已暂停或已过期')
    resource,owner=sources(session,row)
    from app.modules.generation.service import independent_active
    if independent_active(session,row.workspace_id):fail('independent_check','诊断和独立检测期间不生成辅助媒体')
    scenes=resource.body['classroom']['scenes'];scene_index=index if kind!='video' else 0
    scene=scenes[scene_index]
    # Derived only from approved teaching material, never student conversations.
    if kind=='audio':
        text=''.join(a.get('text','') for a in scene['actions'] if a.get('kind')=='speech')
        if not text:text=scene['title']+'。请先独立思考，再说明你的依据。'
        prompt=text[:150];amount=len(prompt)*PRICE['audio']
    else:
        prompt=('原创初中数学教学配图，画面简洁，不含任何文字、数字、公式、真人或品牌。主题：' if kind=='image' else
            '原创数学课堂抽象动态素材，五秒缓慢展示点与线的变化。不出现任何文字、数字、公式、真人、品牌或音频。主题：')+scene['title'][:100]
        amount=PRICE[kind]
    ops=list(session.scalars(select(MediaOperation).where(MediaOperation.budget_id==row.id)))
    if sum(o.reserved_microyuan for o in ops if o.request['kind']==kind)+amount>GROUP_CAP[kind] or row.body['reserved_microyuan']+amount>row.body['total_cap_microyuan']:fail('media_budget_exhausted','媒体分项或总金额上限不足')
    request={'kind':kind,'model':MODELS[kind],'prompt':prompt,'scene':scene_index,'region':'cn-beijing','resource_hash':row.body['resource_hash'],
        'size':'1280*720' if kind=='image' else '832*480' if kind=='video' else None,'duration':5 if kind=='video' else None,'voice':'Cherry' if kind=='audio' else None}
    op=MediaOperation(budget_id=row.id,slot=slot,request=request,request_hash=content_hash(request),reserved_microyuan=amount)
    session.add(op);row.body={**row.body,'reserved_microyuan':row.body['reserved_microyuan']+amount};row.revision+=1
    session.flush();return public(session,row)


def download_url(value):
    if not isinstance(value,str):raise ValueError('invalid_media_url')
    url=urlsplit(value)
    if url.scheme!='https' or url.username or url.password or url.fragment or url.port not in {None,443} or not url.hostname or not url.hostname.endswith('.aliyuncs.com'):
        raise ValueError('untrusted_media_host')
    return value


def inspect_asset(raw,kind,mime):
    """Check required dimensions/duration without executing downloaded programs."""
    if kind=='image' and mime=='image/png' and len(raw)>=33 and raw[12:16]==b'IHDR':
        width,height=struct.unpack('>II',raw[16:24])
        if (width,height)!=(1280,720):raise ValueError('image_dimensions_mismatch')
        return {'width':width,'height':height,'duration_seconds':None}
    if kind=='audio' and mime=='audio/wav':
        with wave.open(io.BytesIO(raw),'rb') as stream:
            duration=stream.getnframes()/stream.getframerate()
            if not 0<duration<=30 or stream.getnchannels()>2 or stream.getsampwidth()>4:raise ValueError('audio_duration_bounds')
            if len(stream.readframes(stream.getnframes()))!=stream.getnframes()*stream.getnchannels()*stream.getsampwidth():raise ValueError('truncated_audio')
        return {'duration_seconds':duration}
    if kind=='video' and mime=='video/mp4':
        def atoms(data):
            offset=0
            while offset+8<=len(data):
                size=int.from_bytes(data[offset:offset+4],'big');tag=data[offset+4:offset+8];head=8
                if size==1:
                    if offset+16>len(data):raise ValueError('invalid_mp4')
                    size=int.from_bytes(data[offset+8:offset+16],'big');head=16
                if size==0:size=len(data)-offset
                if size<head or offset+size>len(data):raise ValueError('invalid_mp4')
                yield tag,data[offset+head:offset+size];offset+=size
        duration=None;dimensions=[];has_audio=False
        for tag,body in atoms(raw):
            if tag!=b'moov':continue
            for child,value in atoms(body):
                if child==b'mvhd':
                    start=20 if value[0]==1 else 12
                    timescale=int.from_bytes(value[start:start+4],'big')
                    ticks=int.from_bytes(value[start+4:start+(12 if value[0]==1 else 8)],'big')
                    duration=ticks/timescale if timescale else None
                if child==b'trak':
                    for track,content in atoms(value):
                        if track==b'tkhd' and len(content)>=8:
                            dimensions.append(tuple(int.from_bytes(content[-8+n:-4+n] if n==0 else content[-4:],'big')/65536 for n in (0,4)))
                        if track==b'mdia':
                            has_audio|=any(t==b'hdlr' and len(c)>=12 and c[8:12]==b'soun' for t,c in atoms(content))
        if duration is None or not 4.8<=duration<=5.2 or (832.,480.) not in dimensions or has_audio:raise ValueError('video_metadata_mismatch')
        return {'duration_seconds':duration,'width':832,'height':480,'audio':False}
    raise ValueError('unsupported_media_metadata')


def classroom_overlay(session,resource):
    """Assets are separate immutable results; never rewrite an approved resource."""
    value=deepcopy(resource.body['classroom'])
    for budget in session.scalars(select(MediaBudget).where(MediaBudget.resource_id==resource.id)):
        if budget.body['resource_hash']!=resource.sha256:continue
        for op in session.scalars(select(MediaOperation).where(MediaOperation.budget_id==budget.id,MediaOperation.status=='completed')):
            if not session.get(MediaAsset,op.id):continue
            value['scenes'][op.request['scene']].setdefault('media',[]).append({'id':op.id,'kind':op.request['kind'],
                'src':f'/api/v1/student/workspaces/{budget.workspace_id}/media-assets/{op.id}',
                'alt':'模型生成教学补充素材，未作为数学真值','captions':op.request['prompt'] if op.request['kind']=='audio' else '生成素材，不是数学核验结论'})
    return value


class BailianMedia:
    def __init__(self,key,transport=None):
        self.client=httpx.Client(base_url=api_host(),headers={'Authorization':'Bearer '+key},timeout=60,trust_env=False,follow_redirects=False,transport=transport)
        self.transport=transport
    def close(self):self.client.close()
    def json(self,method,path,**kwargs):
        with self.client.stream(method,path,**kwargs) as response:
            if response.status_code!=200:raise ValueError('media_upstream_rejected')
            raw=bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw)>1_000_000:raise ValueError('media_response_bounds')
        value=json.loads(raw)
        if not isinstance(value,dict) or value.get('code'):raise ValueError('invalid_media_response')
        return value
    def submit(self,request):
        kind=request['kind'];model=request['model'];prompt=request['prompt']
        if kind=='image':
            value=self.json('POST','/api/v1/services/aigc/multimodal-generation/generation',json={'model':model,
                'input':{'messages':[{'role':'user','content':[{'text':prompt}]}]},'parameters':{'size':'1280*720','n':1,'prompt_extend':False,'watermark':True}})
            url=value['output']['choices'][0]['message']['content'][0]['image'];return {'url':download_url(url),'request_id':value.get('request_id')}
        if kind=='audio':
            value=self.json('POST','/api/v1/services/aigc/multimodal-generation/generation',json={'model':model,'input':{'text':prompt,'voice':'Cherry','language_type':'Chinese'}})
            return {'url':download_url(value['output']['audio']['url']),'request_id':value.get('request_id')}
        value=self.json('POST','/api/v1/services/aigc/video-generation/video-synthesis',headers={'X-DashScope-Async':'enable'},
            json={'model':model,'input':{'prompt':prompt},'parameters':{'size':'832*480','duration':5,'n':1,'prompt_extend':False,'watermark':True}})
        identifier=value['output']['task_id']
        if not isinstance(identifier,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,200}',identifier):raise ValueError('invalid_media_task')
        return {'remote_id':identifier,'request_id':value.get('request_id')}
    def poll(self,remote):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,200}',remote):raise ValueError('invalid_media_task')
        value=self.json('GET','/api/v1/tasks/'+remote);output=value['output'];status=output['task_status']
        if status=='SUCCEEDED':return {'url':download_url(output['video_url']),'request_id':value.get('request_id')}
        if status in {'FAILED','CANCELED'}:raise ValueError('media_task_failed')
        return None
    def download(self,url,kind):
        # Separate client: no Authorization, cookies, environment proxy or redirects.
        with httpx.Client(timeout=60,trust_env=False,follow_redirects=False,transport=self.transport) as client:
            with client.stream('GET',download_url(url)) as response:
                if response.status_code!=200:raise ValueError('media_download_failed')
                raw=bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>20_000_000:raise ValueError('media_asset_too_large')
        if kind=='image' and raw.startswith(b'\x89PNG\r\n\x1a\n'):return bytes(raw),'image/png','.png'
        if kind=='image' and raw.startswith(b'\xff\xd8\xff'):return bytes(raw),'image/jpeg','.jpg'
        if kind=='audio' and raw.startswith(b'RIFF') and raw[8:12]==b'WAVE':return bytes(raw),'audio/wav','.wav'
        if kind=='audio' and (raw.startswith(b'ID3') or len(raw)>2 and raw[0]==255 and raw[1]&224==224):return bytes(raw),'audio/mpeg','.mp3'
        if kind=='video' and len(raw)>12 and raw[4:8]==b'ftyp':return bytes(raw),'video/mp4','.mp4'
        raise ValueError('unexpected_media_bytes')


class MediaRunner:
    def __init__(self,db,transport=None):self.db,self.transport=db,transport
    def step(self):
        with self.db.transaction() as session:
            for stale in session.scalars(select(MediaOperation).where(MediaOperation.status=='submitting',MediaOperation.lease_until<now()).with_for_update(skip_locked=True)):
                stale.status='unknown';stale.receipt={'error_code':'submit_outcome_unknown','billing_unknown':True}
            row=session.scalar(select(MediaOperation).where(MediaOperation.status.in_(['pending','running']),MediaOperation.next_poll_at<=now(),
                (MediaOperation.lease_until.is_(None))|(MediaOperation.lease_until<now())).order_by(MediaOperation.created_at,MediaOperation.id).with_for_update(skip_locked=True).limit(1))
            if not row:return False
            budget=session.get(MediaBudget,row.budget_id,with_for_update=True)
            if session.scalar(select(MediaOperation.id).where(MediaOperation.budget_id==row.budget_id,MediaOperation.id!=row.id,
                    MediaOperation.lease_until>now())):return False
            try:sources(session,budget)
            except DomainError:row.status='cancelled';return True
            if not budget.enabled or row.status=='pending' and now()>=budget.body.get('expires_at',0):row.status='cancelled';return True
            if row.polls>=40 or now()-row.created_at>600:row.status='unknown';row.receipt={'error_code':'media_wait_limit','billing_unknown':True};return True
            if content_hash(row.request)!=row.request_hash:row.status='failed';row.receipt={'error_code':'snapshot_mismatch','billing_unknown':False};return True
            identifier,request,remote=row.id,row.request,row.remote_id
            token=new_id();row.lease_token=token;row.lease_until=now()+150
            row.next_poll_at=now()+15;row.polls+=1
            if not remote:row.status='submitting'
        from app.llm.catalog import credential
        key=credential('qwen');result=None;asset=None;error=None
        try:
            if not key:raise ValueError('media_key_missing')
            port=BailianMedia(key,self.transport)
            try:
                result=port.poll(remote) if remote else port.submit(request)
                if result and result.get('url'):asset=port.download(result['url'],request['kind'])
            finally:port.close()
            if asset:
                metadata=inspect_asset(asset[0],request['kind'],asset[1])
        except (ValueError,KeyError,IndexError,TypeError,ZeroDivisionError,struct.error,wave.Error,EOFError,httpx.HTTPError):error='media_outcome_unknown'
        with self.db.transaction() as session:
            row=session.get(MediaOperation,identifier,with_for_update=True);budget=session.get(MediaBudget,row.budget_id,with_for_update=True)
            if row.lease_token!=token or row.lease_until<=now():return True
            row.lease_token=None;row.lease_until=None
            if error:row.status='unknown';row.receipt={'error_code':error,'billing_unknown':True};return True
            if result and result.get('remote_id'):row.remote_id=result['remote_id']
            if not asset:row.status='running';return True
            try:resource,_=sources(session,budget)
            except DomainError:row.status='cancelled';return True
            if not budget.enabled:row.status='cancelled';return True
            raw,mime,suffix=asset;ROOT.mkdir(parents=True,exist_ok=True);file=ROOT/(row.id+suffix)
            file.write_bytes(raw);digest=hashlib.sha256(raw).hexdigest()
            session.add(MediaAsset(id=row.id,body={'file':file.name,'mime':mime,'bytes':len(raw),'model':request['model'],'region':'cn-beijing',**metadata},sha256=digest))
            row.status='completed';row.receipt={'request_id':(result or {}).get('request_id'),'sha256':digest,'bytes':len(raw),
                'billing_unknown':True,'metadata':metadata,'notice':'金额预占未释放，实际账单尚需对账；语音每段实测不超过30秒。'}
        return True
