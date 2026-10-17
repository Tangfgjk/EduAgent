"""Read-only, owner-scoped history; never export full task/graph/model snapshots."""
from sqlalchemy import func, select

from app.infrastructure.db.models import LearningFact, LearningInput, ModelConfiguration, ModelOperation, OutboxJob, now


def queue_status(session, run):
    if run.status != 'queued':
        return None
    row = session.execute(select(LearningInput, OutboxJob).join(OutboxJob, OutboxJob.input_id == LearningInput.id)
        .where(LearningInput.workspace_id == run.workspace_id, LearningInput.conversation_id == run.conversation_id)
        .order_by(func.coalesce(LearningInput.payload['expected_revision'].as_integer(), 0).desc()).limit(1)).first()
    if row is None:
        return None
    entry, job = row
    timestamp = now()
    state = ('failed' if job.status == 'dead' else 'recovering' if job.status == 'leased' and job.lease_until <= timestamp
        else 'processing' if job.status == 'leased' else 'retrying' if job.status == 'pending' and job.attempts else 'waiting')
    return {'state': state, 'wait_seconds': max(0, timestamp - entry.created_at), 'attempts': job.attempts,
        'retry_after_seconds': max(0, job.available_at - timestamp) if state == 'retrying' else None,
        'error_code': job.error_code}


def history(session, account, run, after=0, limit=30):
    # The expected revision is unique within an accepted input stream, even when
    # several inputs share a second; the initial start input has sequence 1.
    sequence = func.coalesce(LearningInput.payload['expected_revision'].as_integer(), 0) + 1
    rows = session.execute(select(LearningInput, LearningFact, ModelOperation, ModelConfiguration, sequence)
        .outerjoin(LearningFact, LearningFact.input_id == LearningInput.id)
        .outerjoin(ModelOperation, ModelOperation.input_id == LearningInput.id)
        .outerjoin(ModelConfiguration, ModelConfiguration.id == ModelOperation.configuration_id)
        .where(LearningInput.account_id == account.id, LearningInput.workspace_id == run.workspace_id,
            LearningInput.conversation_id == run.conversation_id, sequence > after)
        .order_by(sequence).limit(limit + 1)).all()
    items = []
    for entry, fact, operation, config, position in rows[:limit]:
        payload = entry.payload
        observation = fact.payload if fact else {}
        snapshot = observation.get('task_snapshot') or {}
        items.append({'sequence': position, 'created_at': entry.created_at, 'source_kind': entry.source_kind,
            'action': payload.get('action', 'start'),
            'task_id': fact.task_id if fact else operation.task_id if operation else None,
            'task_stem': snapshot.get('stem'), 'stage': observation.get('stage'),
            'answer': payload.get('answer'), 'reasoning': payload.get('reasoning'),
            'reflection': payload.get('reflection'), 'thinking': payload.get('coaching_text') or None,
            'preset_hint': observation.get('revealed_hint'), 'numeric_status': observation.get('status') if fact and fact.kind == 'answer' else None,
            'processed': fact is not None,
            'model': {'operation_id': operation.id, 'status': operation.status, 'vendor': config.vendor,
                'model': config.model, 'prompt_version': config.prompt_version,
                'result': operation.result if operation.status == 'succeeded' else None,
                'error_code': operation.error_code} if operation else None})
    return {'items': items, 'cursor': items[-1]['sequence'] if items else after, 'has_more': len(rows) > limit}
