"""Owner export and an explicit, offline erasure boundary.

Online code can request erasure, never disable immutable-record protections.
Only a stopped-service maintenance command may erase an opted-in account.
"""
import json
from pathlib import Path
from sqlalchemy import select,delete,tuple_,text
from app.infrastructure.db.models import (Base,Account,Workspace,LearningInput,LearningFact,MemoryNote,
    StudentDocument,ReviewCard,ResourceProgress,AuditEvent,AuthSession,new_id,now)
from app.modules.errors import DomainError
from app.modules.learning.content_repository import content_hash


def export_data(session,actor):
    account=actor.account
    spaces=list(session.scalars(select(Workspace).where(Workspace.owner_id==account.id)))
    ids=[s.id for s in spaces]
    def rows(model):return session.scalars(select(model).where(model.workspace_id.in_(ids))).all()
    inputs=rows(LearningInput);facts=rows(LearningFact)
    if len(inputs)+len(facts)>50000:raise DomainError(409,'offline_export_required','记录超过在线导出范围，请联系本机维护人员分批导出')
    data={'schema_version':'personal-export.v1','exported_at':now(),
        'account':{k:getattr(account,k) for k in ('id','username','display_name','source_kind','created_at')},
        'workspaces':[{k:getattr(s,k) for k in ('id','title','kind','status','created_at')} for s in spaces],
        'expressions':[{'id':r.id,'workspace_id':r.workspace_id,'created_at':r.created_at,'source_kind':r.source_kind,
            'expression':{k:v for k,v in r.payload.items() if k in {'action','answer','reasoning','reflection','coaching_text','goal','experience','text'}}} for r in inputs],
        'facts':[{'id':r.id,'workspace_id':r.workspace_id,'kind':r.kind,'task_id':r.task_id,'content_version':r.content_version,
            'created_at':r.created_at,'source_kind':r.source_kind,'observation':{k:v for k,v in r.payload.items()
                if k in {'stage','status','hints_used','attempt','assessment_exposure','support_decision','answer_interpretation','strategy_version_id'}},
            'task_stem':r.payload.get('task_snapshot',{}).get('stem')} for r in facts],
        'local_knowledge':[{'id':r.id,'title':r.title,'body':r.body,'sha256':r.sha256,'workspace_id':r.workspace_id} for r in rows(StudentDocument)],
        'notes':[{'fact_id':r.fact_id,'text':r.text,'created_at':r.created_at} for r in rows(MemoryNote)],
        'notice':'仅本人资料与提交；不导出登录凭据、后台提示词、参考答案、保留测验或其他用户数据。备份和外部服务记录单独治理。'}
    return {'data':data,'sha256':content_hash(data)}


def request_erasure(session,actor,ack):
    if not ack:raise DomainError(422,'confirmation_required','请先导出需要保留的数据，并明确确认停用与申请删除')
    previous=session.scalar(select(AuditEvent).where(AuditEvent.actor_id==actor.account.id,AuditEvent.action=='privacy.delete_requested'))
    if previous:return {'request_id':previous.id,'status':'pending_maintenance'}
    record=AuditEvent(actor_id=actor.account.id,action='privacy.delete_requested',target_id=actor.account.id)
    session.add(record);actor.account.enabled=False
    session.execute(delete(AuthSession).where(AuthSession.account_id==actor.account.id));session.flush()
    return {'request_id':record.id,'status':'pending_maintenance','notice':'账号已停用；数据尚未删除。需停服、备份并执行本机维护，外部服务和备份副本单独核对。'}


def erasure_scope(session,aid):
    """Follow real FK ownership; include explicitly documented non-FK references."""
    account=session.get(Account,aid)
    if not account or account.role!='student':raise DomainError(409,'student_required','只能处理已申请删除的学生账号')
    tables=Base.metadata.tables
    selected={name:{} for name in tables}
    def add(name,rows):
        table=tables[name]
        for row in rows:selected[name][tuple(row[c.name] for c in table.primary_key.columns)]=dict(row)
    add('accounts',session.execute(select(Account.__table__).where(Account.id==aid)).mappings())
    for _ in range(len(tables)):
        before=sum(map(len,selected.values()))
        for name,table in tables.items():
            for constraint in table.foreign_key_constraints:
                elements=list(constraint.elements);parent=elements[0].column.table.name;parents=selected[parent].values()
                values={tuple(row[e.column.name] for e in elements) for row in parents}
                values=list(values)
                for offset in range(0,len(values),500):
                    condition=tuple_(*(table.c[e.parent.name] for e in elements)).in_(values[offset:offset+500])
                    add(name,session.execute(select(table).where(condition)).mappings())
        # Dedicated resource budget has a single originating job; it is not shared.
        bids={r['budget_id'] for r in selected.get('openmaic_jobs',{}).values()}
        if bids:add('resource_budgets',session.execute(select(tables['resource_budgets']).where(tables['resource_budgets'].c.id.in_(bids))).mappings())
        entity_ids={str(value) for rows in selected.values() for key in rows for value in key}
        for name,column in (('audit_events','target_id'),('production_reviews','target_id'),('development_reviews','target_id'),('content_approvals','unit_id')):
            values=list(entity_ids)
            for offset in range(0,len(values),500):
                add(name,session.execute(select(tables[name]).where(tables[name].c[column].in_(values[offset:offset+500]))).mappings())
        facts=set(r['id'] for r in selected['learning_facts'].values())
        facts=list(facts)
        for offset in range(0,len(facts),500):
            add('evaluation_observations',session.execute(select(tables['evaluation_observations']).where(tables['evaluation_observations'].c.body['fact_id'].as_string().in_(facts[offset:offset+500]))).mappings())
        if sum(map(len,selected.values()))>100000:raise DomainError(409,'offline_scope_too_large','数据过大，请使用专门迁移方案分批处理')
        if before==sum(map(len,selected.values())):break
    owned={r['id'] for r in selected['workspaces'].values()}
    if any(r.get('owner_id',aid)!=aid or r.get('account_id',aid)!=aid or r.get('workspace_id') and r['workspace_id'] not in owned
        for rows in selected.values() for r in rows.values()):
        raise DomainError(409,'shared_data_dependency','发现其他账号依赖，拒绝连带删除，请先核对共享范围')
    return selected


def erase_requested(db,aid,backup_path,apply=False,maintenance_confirmed=False):
    """Preview by default. Keep FK checks; suspend append-only triggers only in this transaction."""
    db.assert_ready()
    with db.transaction() as session:
        account=session.get(Account,aid,with_for_update=True)
        requested=session.scalar(select(AuditEvent.id).where(AuditEvent.actor_id==aid,AuditEvent.action=='privacy.delete_requested'))
        if not account or account.enabled or not requested:raise DomainError(409,'erasure_not_requested','仅可处理本人已申请删除且停用的账号')
        scope=erasure_scope(session,aid);counts={k:len(v) for k,v in scope.items() if v}
        if not apply:return {'dry_run':True,'counts':counts,'external_cleanup_required':True}
    if not maintenance_confirmed:raise DomainError(409,'maintenance_required','需明确确认API和全部worker已停止；默认只预览')
    from app.infrastructure.db.maintenance import backup
    backup(db,Path(backup_path))  # exclusive new file; never overwrite another backup
    files=[]
    with db.transaction() as session:
        scope=erasure_scope(session,aid)
        for name in ('model_operations','media_operations','outbox_jobs'):
            if any(r.get('lease_until') and r['lease_until']>now() for r in scope.get(name,{}).values()):
                raise DomainError(409,'active_lease','仍有活动任务租约，请先停服并等待租约到期')
        connection=session.connection();dialect=db.engine.dialect.name;ddl=[]
        quote=connection.dialect.identifier_preparer.quote
        names=[t.name for t in Base.metadata.sorted_tables if scope[t.name]]
        if dialect=='sqlite':
            for row in connection.execute(text("SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger'")).mappings():
                if row['tbl_name'] in names:
                    ddl.append(row['sql']);connection.exec_driver_sql('DROP TRIGGER '+quote(row['name']))
        else:
            for name in names:connection.exec_driver_sql('ALTER TABLE '+quote(name)+' DISABLE TRIGGER USER')
        for row in scope['media_assets'].values():files.append(row['body']['file'])
        for table in reversed(Base.metadata.sorted_tables):
            keys=list(scope[table.name])
            for offset in range(0,len(keys),500):
                session.execute(table.delete().where(tuple_(*table.primary_key.columns).in_(keys[offset:offset+500])))
        session.execute(delete(Base.metadata.tables['model_daily_budgets']).where(Base.metadata.tables['model_daily_budgets'].c.scope=='account:'+aid))
        if dialect=='sqlite':
            for sql in ddl:connection.exec_driver_sql(sql)
        else:
            for name in names:connection.exec_driver_sql('ALTER TABLE '+quote(name)+' ENABLE TRIGGER USER')
        session.add(AuditEvent(action='privacy.erasure_completed',target_id=new_id()))
    from app.modules.resources.media import ROOT
    pending=[]
    for filename in files:
        target=(ROOT/filename).resolve()
        if not target.is_relative_to(ROOT.resolve()):pending.append(filename);continue
        try:target.unlink(missing_ok=True)
        except OSError:pending.append(filename)
    return {'dry_run':False,'counts':counts,'local_media_cleanup_pending':pending,
        'external_cleanup_required':True,'notice':'业务库已删除；备份含敏感原文需按约定销毁，外部服务产物/账单不由本命令伪称删除。'}
