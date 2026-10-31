"""Local retrieval only. Documents are untrusted data, never system instructions."""
import hashlib
import re
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import select, func, delete
from app.modules.learning.contracts import Contract
from app.modules.workspaces.service import Workspaces
from app.modules.errors import DomainError
from app.infrastructure.db.models import StudentDocument, AuditEvent


class DocumentIn(Contract):
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=100000)


def chunks(text):
    # Stable paragraph/character locators, bounded overlap for Chinese retrieval.
    return [(i, text[i:i+600]) for i in range(0, len(text), 500)]


def library_router(student):
    router = APIRouter(prefix='/api/v1/student/workspaces/{wid}/library')

    def scope(ctx, wid, active=False):
        Workspaces().owned(ctx.session, ctx.actor, str(wid), active=active)

    @router.get('')
    def listing(wid: UUID, ctx=Depends(student, scope='function')):
        scope(ctx, wid)
        rows=ctx.session.scalars(select(StudentDocument).where(StudentDocument.workspace_id==str(wid)).order_by(StudentDocument.created_at.desc(), StudentDocument.id))
        return {'items':[{'id':r.id,'title':r.title,'sha256':r.sha256,'characters':len(r.body),'created_at':r.created_at} for r in rows], 'local_only':True}

    @router.post('', status_code=201)
    def add(wid: UUID, body: DocumentIn, ctx=Depends(student, scope='function')):
        scope(ctx,wid,True)
        text=body.text.replace('\r\n','\n').strip();title=body.title.strip()
        if not text or not title or '\x00' in text or '\ufffd' in text:
            raise DomainError(422,'invalid_document','资料为空或含损坏字符，请使用UTF-8文本')
        digest=hashlib.sha256(text.encode('utf-8')).hexdigest()
        existing=ctx.session.scalar(select(StudentDocument).where(StudentDocument.workspace_id==str(wid),StudentDocument.sha256==digest))
        if existing:return {'id':existing.id,'sha256':digest,'duplicate':True}
        count=ctx.session.scalar(select(func.count()).select_from(StudentDocument).where(StudentDocument.workspace_id==str(wid)))
        if count>=100:raise DomainError(409,'library_capacity','每空间最多100份资料，请先整理')
        row=StudentDocument(workspace_id=str(wid),title=title,body=text,sha256=digest)
        ctx.session.add(row);ctx.session.flush()
        ctx.session.add(AuditEvent(actor_id=ctx.actor.account.id,action='library.add',target_id=row.id))
        return {'id':row.id,'sha256':digest,'duplicate':False}

    @router.get('/search')
    def search(wid: UUID, q: str=Query(min_length=1,max_length=160), ctx=Depends(student,scope='function')):
        scope(ctx,wid)
        from app.modules.generation.service import independent_active
        if independent_active(ctx.session,str(wid)):raise DomainError(409,'independent_check','独立检测期间不开放资料检索')
        terms=set(re.findall(r'[a-zA-Z0-9]+|[\u4e00-\u9fff]{1,}',q.lower()))
        terms|={t[i:i+2] for t in list(terms) if len(t)>2 for i in range(len(t)-1)}
        terms=sorted(terms,key=lambda t:(-len(t),t))[:16]
        if not terms:return {'items':[],'local_only':True}
        hits=[]
        for row in ctx.session.scalars(select(StudentDocument).where(StudentDocument.workspace_id==str(wid))):
            for start,text in chunks(row.body):
                score=sum(text.lower().count(term) for term in terms)
                if score:hits.append({'document_id':row.id,'title':row.title,'sha256':row.sha256,'start':start,'end':start+len(text),'text':text,'score':score})
        hits.sort(key=lambda h:(-h['score'],h['document_id'],h['start']))
        return {'items':hits[:8],'local_only':True,'answer_kind':'source_excerpt','notice':'原文摘录，未经专业核验；未调用外部模型。'}

    @router.get('/{did}')
    def read(wid: UUID,did: UUID,ctx=Depends(student,scope='function')):
        scope(ctx,wid)
        from app.modules.generation.service import independent_active
        if independent_active(ctx.session,str(wid)):raise DomainError(409,'independent_check','独立检测期间不开放资料')
        row=ctx.session.get(StudentDocument,str(did))
        if not row or row.workspace_id!=str(wid):raise DomainError(404,'not_found','资料不可访问')
        return {'id':row.id,'title':row.title,'text':row.body,'sha256':row.sha256}

    @router.delete('/{did}')
    def remove(wid: UUID,did: UUID,ctx=Depends(student,scope='function')):
        scope(ctx,wid,True)
        result=ctx.session.execute(delete(StudentDocument).where(StudentDocument.id==str(did),StudentDocument.workspace_id==str(wid)))
        if not result.rowcount:raise DomainError(404,'not_found','资料不可访问')
        ctx.session.add(AuditEvent(actor_id=ctx.actor.account.id,action='library.delete',target_id=str(did)))
        return {'deleted':True}
    return router
