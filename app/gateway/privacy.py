import json
from fastapi import APIRouter,Depends
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import select
from app.modules.learning.contracts import Contract
from app.infrastructure.db.models import AuditEvent,Account
from app.modules.identity.privacy import export_data,request_erasure


class ErasureIn(Contract):
    acknowledge_export_and_disable: bool = Field(strict=True)


def privacy_router(student,admin):
    router=APIRouter(prefix='/api/v1')
    @router.get('/student/privacy/export')
    def export(ctx=Depends(student,scope='function')):
        value=export_data(ctx.session,ctx.actor)
        return Response(json.dumps(value,ensure_ascii=False),media_type='application/json',headers={
            'Content-Disposition':'attachment; filename="my-learning-records.json"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    @router.post('/student/privacy/erasure-request',status_code=202)
    def request(body:ErasureIn,ctx=Depends(student,scope='function')):
        return request_erasure(ctx.session,ctx.actor,body.acknowledge_export_and_disable)
    @router.get('/admin/privacy/erasure-requests')
    def queue(ctx=Depends(admin,scope='function')):
        rows=ctx.session.execute(select(AuditEvent,Account).join(Account,Account.id==AuditEvent.actor_id)
            .where(AuditEvent.action=='privacy.delete_requested').order_by(AuditEvent.created_at).limit(100)).all()
        return {'items':[{'request_id':event.id,'account_id':account.id,'username':account.username,'created_at':event.created_at,
            'status':'pending_maintenance','enabled':account.enabled} for event,account in rows],
            'notice':'页面不能执行不可逆删除。导出后停服，使用维护命令预览、备份再删除。开发保存期尚未统一配置，不能误报自动删除已启用。'}
    return router
