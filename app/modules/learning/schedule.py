"""Freeze an owned, executable learning schedule without exposing assessment text."""
from sqlalchemy import select
from app.infrastructure.db.models import CourseBinding, ReviewCard, LearningFact, Workspace, Account, now
from app.modules.learning.content_catalog import get_pack
from app.modules.errors import DomainError


def capture(session, wid):
    space=session.get(Workspace,wid)
    owner=session.get(Account,space.owner_id) if space else None
    if not owner:raise DomainError(404,'not_found','未找到当前学习项目')
    binding=session.get(CourseBinding,wid)
    if not binding:return {'course_required':True,'tasks':[],'due_reviews':0}
    try:item=get_pack(binding.active_unit_id,session)
    except DomainError:return {'course_required':True,'tasks':[],'due_reviews':0}
    due=list(session.scalars(select(ReviewCard).where(ReviewCard.workspace_id==wid,ReviewCard.source_kind==owner.source_kind,ReviewCard.next_due_at<=now())))
    facts=list(session.scalars(select(LearningFact).where(LearningFact.workspace_id==wid,LearningFact.source_kind==owner.source_kind,LearningFact.content_version==item.version)
        .order_by(LearningFact.created_at.desc(),LearningFact.id.desc()).limit(60)))
    weak={f.task_id for f in facts if f.payload.get('status') in {'failed','unverifiable'} or f.payload.get('hints_used',0)>0}
    return {'unit_id':item.unit_id,'course_revision':binding.revision,'content_version':item.version,
        'title':item.title,'knowledge_points':list(item.knowledge_points),
        'tasks':[{'task_id':t['id'],'stage':t['stage'],
            'concept':(t.get('pedagogy') or {}).get('concept_label',''),'needs_revisit':t['id'] in weak} for t in item.tasks],
        'diagnostic_observations':sum(f.payload.get('stage')=='diagnostic' for f in facts),
        'uncertain_or_failed':sum(f.payload.get('status') in {'failed','unverifiable'} for f in facts),
        'due_reviews':len(due),'due_reviews_scope':'same_source_project_history','review_destination':'review','effect_status':'not_validated'}


def arrange(goal, minutes, context):
    title=context.get('title') or goal
    tasks=context.get('tasks',[])
    practice=[t for t in tasks if t['stage']=='practice']
    selected=sorted(practice,key=lambda t:(not bool(t.get('concept') and t['concept'] in goal),not t.get('needs_revisit',False)))[:max(1,min(3,minutes//5))]
    review=bool(context.get('due_reviews'))
    first=max(1,minutes//5);last=max(1,minutes//5)
    middle=minutes-first-last
    steps=[f'用{first}分钟围绕目标「{goal}」回看「{title}」的条件与已有理解'+('；已有未确定或未通过记录，先核对卡点' if context.get('uncertain_or_failed') else ''),
        f'用{middle}分钟'+(f'先处理本项目历史中{context["due_reviews"]}道到期复习的一道，再' if review else '')+
        (f'依次完成当前课程的{len(selected)}项练习，留下依据与卡点' if selected else '确认课程与单元，再开始对应练习'),
        f'用{last}分钟独立检查并反思；未完成任务留待下次，不提前展示检测答案']
    return steps,{'course':context,'practice_task_ids':[t['task_id'] for t in selected],
        'minutes':{'orientation':first,'practice_and_review':middle,'reflection':last},
        'next_destination':'courses' if context.get('course_required') else 'review' if review else 'study'}


def apply_confirmed(session,plan_id,item,runtime):
    """Prioritize confirmed tasks while preserving all practice and checkpoints."""
    from app.infrastructure.db.models import AgentRun
    plan=session.get(AgentRun,plan_id) if plan_id else None
    schedule=(plan.draft or {}).get('schedule',{}) if plan else {}
    frozen=schedule.get('course',{})
    binding=session.get(CourseBinding,plan.workspace_id) if plan else None
    if not binding or (frozen.get('unit_id'),frozen.get('content_version'),frozen.get('course_revision'))!=(item.unit_id,item.version,binding.revision):
        return runtime
    if runtime is None:
        from app.modules.planning.contracts import ExecutionPolicy
        from app.modules.planning.compiler import compile_policy
        from app.modules.planning.service import effective_policy
        execution=ExecutionPolicy().model_dump()
        strategy_id,policy,_=effective_policy(session,plan.workspace_id)
        platform_policy={**policy,'execution':execution}
        compiled=compile_policy(platform_policy)
        runtime={'unit_id':item.unit_id,'content_version':item.version,'strategy_version_id':strategy_id,
            'execution':execution,'source':'confirmed_plan_with_platform_defaults','policy_hash':compiled['policy_hash'],
            'compiler_version':compiled['compiler_version'],'error_task_ids':[],'source_fact_ids':[],
            'scope':'已确认同版本课程计划，使用明确的平台默认执行参数，不表示已激活个性策略',
            'practice_task_order':[task['id'] for task in item.tasks if task['stage']=='practice']}
    order=runtime['practice_task_order']
    priority=[tid for tid in schedule.get('practice_task_ids',[]) if tid in order]
    return {**runtime,'practice_task_order':priority+[tid for tid in order if tid not in priority],
        'plan_execution':{'status':'applied_priority','plan_id':plan.id,'priority_task_ids':priority,
            'notice':'优先安排已确认练习，其余原练习与独立检测保留；时长为建议，可随时结束。'}}
