"""Fixed-version asynchronous OpenMAIC port; per-job opaque owner mapping."""
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from sqlalchemy import select
from app.infrastructure.db.models import OpenMAICJob,ResourceBudget,CourseBuild,ResourceArtifact,Workspace,now
from app.modules.learning.content_repository import content_hash
from app.modules.resources.service import plain_text
from app.modules.errors import DomainError

REF='a9da60b3b4adb429369c0da13d5637a904ead2b0'


def artifact_for_document(document,remote):
    scenes=document.get('scenes',[])
    if not isinstance(scenes,list) or not 1<=len(scenes)<=3 or not all(isinstance(scene,dict) for scene in scenes) or not isinstance(document.get('stage'),dict):raise ValueError('scene_budget_exceeded')
    cards=[]
    for scene in scenes:
        text=plain_text(scene.get('content',{}))
        if not text.strip():raise ValueError('empty_scene_content')
        cards.append({'text':(str(scene.get('title',''))+'\n'+text)[:4000]})
    return {'title':str(document['stage'].get('name','OpenMAIC 课程资源'))[:120],
        'cards':cards,'generator':'OpenMAIC.v1.2.0','upstream_ref':REF,'remote_job_id':remote,'converter_version':'canvas-text.v2'}


def endpoint():
    value=os.getenv('WENJIN_OPENMAIC_URL','http://127.0.0.1:4004').rstrip('/')
    url=urlsplit(value)
    if url.scheme!='http' or url.hostname not in {'127.0.0.1','localhost'} or url.port!=4004 or url.path or url.username or url.password or url.query or url.fragment:
        raise DomainError(409,'invalid_resource_endpoint','首轮 OpenMAIC 仅允许本机 4004 固定服务')
    return value


class OpenMAICPort:
    def __init__(self,owner_id,transport=None):
        keyfile=Path('.local/openmaic-proxy.key')
        secret=keyfile.read_text(encoding='utf-8').strip() if keyfile.is_file() else ''
        self.client=httpx.Client(base_url=endpoint(),cookies={'anonymous_id':owner_id},headers={'X-EduAgent-Service-Key':secret},timeout=30,trust_env=False,follow_redirects=False,transport=transport)

    def close(self): self.client.close()

    def read(self,method,path,**kwargs):
        with self.client.stream(method,path,**kwargs) as response:
            response.raise_for_status();raw=b''
            for chunk in response.iter_bytes():
                raw+=chunk
                if len(raw)>5_000_000: raise ValueError('response_bounds')
        import json
        value=json.loads(raw)
        if not isinstance(value,dict): raise ValueError('invalid_response_shape')
        return value

    def capabilities(self): return self.read('GET','/api/generate-classroom/capabilities')

    def submit(self,requirement):
        caps=self.capabilities().get('capabilities',{})
        if not isinstance(caps,dict): raise ValueError('invalid_capabilities')
        if any(caps.get(k) for k in ('webSearch','imageGeneration','videoGeneration','tts')):
            raise ValueError('unapproved_media_enabled')
        value=self.read('POST','/api/generate-classroom',json={'requirement':requirement})
        identifier=value.get('jobId')
        if not isinstance(identifier,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',identifier): raise ValueError('invalid_job_id')
        return identifier

    def poll(self,identifier): return self.read('GET','/api/generate-classroom/'+identifier)

    def document(self,identifier):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',identifier): raise ValueError('invalid_classroom_id')
        return self.read('GET','/api/stages/'+identifier)


class OpenMAICRunner:
    def __init__(self,db,transport=None): self.db,self.transport=db,transport

    def step(self):
        with self.db.transaction() as session:
            for stale in session.scalars(select(OpenMAICJob).where(OpenMAICJob.status=='submitting',OpenMAICJob.lease_until<now()).with_for_update(skip_locked=True)):
                stale.status,stale.error_code='unknown','submit_outcome_unknown'
            row=session.scalar(select(OpenMAICJob).where(OpenMAICJob.status.in_(['pending','running','import_pending']),OpenMAICJob.next_poll_at<=now(),
                (OpenMAICJob.lease_until.is_(None)) | (OpenMAICJob.lease_until<now())).order_by(OpenMAICJob.created_at).with_for_update(skip_locked=True).limit(1))
            if not row: return False
            budget=session.get(ResourceBudget,row.budget_id)
            readonly=row.status=='import_pending'
            if (not budget.enabled and not readonly) or session.get(Workspace,row.workspace_id).status!='active':
                row.status='cancelled';budget.enabled=False;return True
            build=session.get(CourseBuild,row.build_id)
            from app.modules.knowledge.service import check_snapshot
            try: check_snapshot(session,build.sources)
            except DomainError:
                row.status='cancelled';budget.enabled=False;return True
            identifier,owner,remote=row.id,row.owner_id,row.remote_id
            target=row.classroom_id
            row.lease_until=now()+90;row.next_poll_at=now()+60
            if not remote: row.status='submitting'
            # Only approved mathematical content; no student diagnosis or identifiers.
            requirement='制作初中数学中文文本课堂，恰好三个幻灯片场景，不含练习答案或检测题，不含媒体、搜索、语音或交互脚本。主题：'+build.candidate['spec']['title']+'。仅讲概念、条件与自我检查。资料：'+build.candidate['lesson'][:2000]
        client=OpenMAICPort(owner,self.transport);value=None;artifact=None;error=None
        try:
            if readonly:
                if not target or not remote:raise ValueError('missing_existing_classroom')
                artifact=artifact_for_document(client.document(target),remote)
            else:
                if not remote: remote=client.submit(requirement)
                value=client.poll(remote)
            if not readonly and value.get('status')=='succeeded':
                if not isinstance(value.get('result',{}),dict): raise ValueError('invalid_result')
                target=(value.get('result') or {}).get('classroomId') or value.get('classroomId')
                if not target:
                    url=(value.get('result') or {}).get('classroomUrl','')
                    target=urlsplit(url).path.rstrip('/').split('/')[-1]
                document=client.document(target)
                artifact=artifact_for_document(document,remote)
        except (httpx.HTTPError,ValueError,KeyError,TypeError): error='resource_remote_unknown'
        finally: client.close()
        with self.db.transaction() as session:
            row=session.get(OpenMAICJob,identifier,with_for_update=True);budget=session.get(ResourceBudget,row.budget_id)
            if row.status=='cancelled' or (not budget.enabled and not readonly): return True
            row.remote_id=remote
            row.lease_until=None
            if error: row.status='unknown' if readonly or not remote else 'running';row.error_code=error
            elif artifact:
                build=session.get(CourseBuild,row.build_id)
                try: check_snapshot(session,build.sources)
                except DomainError:
                    row.status='cancelled';budget.enabled=False;return True
                if session.get(Workspace,row.workspace_id).status!='active' or build.status!='ready':
                    row.status='cancelled';budget.enabled=False;return True
                digest=content_hash(artifact)
                existing=session.scalar(select(ResourceArtifact).where(ResourceArtifact.build_id==row.build_id,ResourceArtifact.sha256==digest))
                if existing is None:
                    session.add(ResourceArtifact(workspace_id=row.workspace_id,build_id=row.build_id,body=artifact,sha256=digest))
                row.classroom_id=target;row.status='completed' if existing and existing.status=='approved' else 'pending_review';budget.enabled=False
            elif value.get('status')=='failed': row.status='failed';row.error_code='remote_generation_failed';budget.enabled=False
            else: row.status='running'
        return True
