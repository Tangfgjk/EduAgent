"""One owner-scoped learning goal, shared by teaching and planning consumers.

This is a bounded evidence snapshot, never a diagnosis or an instruction source.
No independent task text, answer key, or other project's records are exported.
"""
from sqlalchemy import select
from app.infrastructure.db.models import CourseBinding, CurriculumVersion, LearningFact, Workspace
from app.modules.learning.content_repository import content_hash
from app.modules.learning.model_coaching import redact


def snapshot(session, wid, account):
    space = session.get(Workspace, wid)
    if not space or space.owner_id != account.id:
        raise ValueError('goal_owner_mismatch')
    binding = session.get(CourseBinding, wid)
    curriculum = session.get(CurriculumVersion, binding.curriculum_id) if binding else None
    body = curriculum.snapshot if curriculum else {}
    facts = list(session.scalars(select(LearningFact).where(
        LearningFact.workspace_id == wid, LearningFact.source_kind == account.source_kind)
        .order_by(LearningFact.created_at.desc(), LearningFact.id.desc()).limit(60)))
    from app.modules.learning.content_catalog import get_pack
    current_version=None
    if binding and binding.active_unit_id:
        from app.modules.errors import DomainError
        try:current_version=get_pack(binding.active_unit_id,session).version
        except DomainError:pass
    history=[f for f in facts if f.content_version!=current_version]
    facts=[f for f in facts if f.content_version==current_version][:6]
    evidence = [{'id': f.id, 'kind': f.kind, 'stage': f.payload.get('stage'),
                 'status': f.payload.get('status'), 'hints_used': f.payload.get('hints_used', 0),
                 'content_version': f.content_version,'created_at':f.created_at,'association':'current_course'} for f in reversed(facts)]
    value = {'schema_version': 'learning-goal.v1', 'workspace_id': wid,
             'course_revision': binding.revision if binding else 0,
             'unit_id': binding.active_unit_id if binding else None,
             'content_version':current_version,
             'topic': redact(str(body.get('topic', '')), account)[:120],
             'course_title': redact(str(body.get('title', '')), account)[:120],
             'historical_background':{'count_in_recent_window':len(history),'window_limit':60,
                'records':[{'id':f.id,'content_version':f.content_version,'created_at':f.created_at,'association':'historical_course'} for f in history[:6]],
                'notice':'历史单元背景，不作为当前目标的通过或掌握证据'},
             'evidence': evidence, 'effect_status': 'not_validated'}
    return {**value, 'fingerprint': content_hash(value)}


def handoff(session, wid, account):
    """Local navigation advice, distinct from the bounded external snapshot."""
    from app.infrastructure.db.models import ModelOperation, ModelPolicy
    goal = snapshot(session, wid, account)
    policy = session.get(ModelPolicy, 1)
    decisions = []
    if policy and policy.active_id:
        rows = session.scalars(select(ModelOperation).where(
            ModelOperation.workspace_id == wid, ModelOperation.account_id == account.id,
            ModelOperation.configuration_id == policy.active_id,
            ModelOperation.policy_epoch == policy.stop_epoch,
            ModelOperation.status == 'succeeded').order_by(
                ModelOperation.created_at.desc(), ModelOperation.id.desc()).limit(20))
        for row in rows:
            frozen = row.request.get('learning_goal', {})
            if frozen.get('course_revision') != goal['course_revision'] or frozen.get('unit_id') != goal['unit_id']:
                continue
            from app.modules.learning.output_gate import render_result
            checked=render_result(row.result)
            choice = (checked or {}).get('teaching_decision')
            if choice:
                decisions.append({'operation_id': row.id, **choice,
                                  'goal_fingerprint': frozen.get('fingerprint'),
                                  'source_ids': [f['id'] for f in frozen.get('evidence', [])]})
            if len(decisions) == 3:
                break
    return {**goal, 'decisions': decisions}


def external(goal, include_evidence=True):
    """Use ephemeral evidence aliases; database IDs are only kept locally."""
    return {key: goal[key] for key in ('schema_version', 'course_revision', 'unit_id','content_version',
            'topic', 'course_title', 'effect_status') if key in goal} | {
        'evidence': [{**{k:v for k,v in row.items() if k!='id'}, 'ref': 'observation:'+str(i)}
                     for i,row in enumerate(goal.get('evidence', []) if include_evidence else [],1)]}
