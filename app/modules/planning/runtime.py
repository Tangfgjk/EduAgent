"""Freeze supported policy consumers per new task, never read live policy mid-run."""
from sqlalchemy import select
from app.infrastructure.db.models import LearningFact,LearningInput,CourseBinding
from app.modules.planning.compiler import compile_policy
from app.modules.errors import DomainError


def course_baseline(session,wid):
    row=session.get(CourseBinding,wid)
    return {'curriculum_id':row.curriculum_id,'revision':row.revision,'active_unit_id':row.active_unit_id} if row else {}


def capture(session,wid,account,item):
    from app.modules.planning.service import effective_policy
    version,policy,reason=effective_policy(session,wid)
    if not version or not policy.get('execution'):return None
    compiled=compile_policy(policy)
    error_rows=list(session.scalars(select(LearningFact).where(LearningFact.workspace_id==wid,
        LearningFact.content_version==item.version,LearningFact.source_kind==account.source_kind).order_by(LearningFact.created_at,LearningFact.id)))
    errors={f.task_id for f in error_rows if f.payload.get('status')=='failed'}
    practices=[task['id'] for task in item.tasks if task['stage']=='practice']
    order=order_practices(item,errors,policy['execution']['practice_order'])
    return {'strategy_version_id':version,'policy_hash':compiled['policy_hash'],'compiler_version':compiled['compiler_version'],
        'execution':policy['execution'],'unit_id':item.unit_id,'content_version':item.version,'practice_task_order':order,
        'error_task_ids':sorted(errors & set(practices)),'source_fact_ids':list({f.task_id:f.id for f in error_rows if f.task_id in set(practices) and f.payload.get('status')=='failed'}.values()),
        'scope':'本项目、同来源、同内容版本；只调整已有练习顺序，不改变诊断和检测'}


def for_run(session,run):
    entry=session.scalar(select(LearningInput).where(LearningInput.conversation_id==run.conversation_id,LearningInput.idempotency_key=='start:'+run.id))
    return entry.payload.get('skill_runtime') if entry else None


def apply_task_order(item,snapshot):
    if not snapshot:return item
    from dataclasses import replace
    order=snapshot['practice_task_order'];tasks={t['id']:t for t in item.tasks if t['stage']=='practice'}
    if len(order)!=len(tasks) or set(order)!=set(tasks) or snapshot['content_version']!=item.version or snapshot['unit_id']!=item.unit_id:
        raise DomainError(409,'skill_snapshot_invalid','策略任务快照与内容不一致')
    iterator=iter(order)
    return replace(item,tasks=[tasks[next(iterator)] if t['stage']=='practice' else t for t in item.tasks])


def order_practices(item,errors,mode):
    ids=[task['id'] for task in item.tasks if task['stage']=='practice']
    return sorted(ids,key=lambda tid:tid not in errors) if mode=='errors_first' else ids


def order_review_cards(rows,mode,stamp):
    return sorted(rows,key=lambda card:((card.next_due_at>stamp,not(card.next_due_at<=stamp and 'numeric_error' in card.reasons)) if mode=='due_errors_first' else ())+(card.next_due_at,card.id))
