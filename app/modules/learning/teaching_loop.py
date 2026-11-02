"""Evidence -> bounded action -> observed result, stored with the tutor checkpoint.

This controller uses observable attempts, not an inferred mastery score. It makes
no external calls and cannot add tasks, skip practice or change independent tests.
"""
from copy import deepcopy


def reasoning_status(feedback):
    return ((feedback or {}).get('interpretation') or {}).get('reasoning_check', {}).get('status', 'not_assessed')


def signals(records, feedback=None, *, adaptive=False, runtime=None, hints=0):
    latest = next((r for r in reversed(records) if r.get('stage') == 'practice'), None)
    contradiction = reasoning_status(feedback) == 'contradiction_found'
    needs_support = contradiction or (feedback or {}).get('status') in {'failed', 'unverifiable'}
    clean = bool(latest and latest['status'] == 'passed' and latest['hints_used'] == 0
                 and latest['attempts'] == 1 and latest.get('reasoning_status') != 'contradiction_found')
    allowed = adaptive and (runtime or {}).get('execution', {}).get('support_mode') != 'step_first'
    return {'can_fade': bool(allowed and clean and not needs_support and not hints),
            'needs_support': needs_support, 'hints_used': hints,
            'reasoning_status': reasoning_status(feedback)}


def decide(data, proposal=None):
    """Recheck a model proposal against the *final* observed practice result."""
    latest = data['records'][-1]
    observed = signals(data['records'], data.get('feedback'), adaptive=data.get('adaptive_support', False),
                       runtime=data.get('skill_runtime'), hints=len(data['hints']))
    difficulty = (observed['needs_support'] or latest['status'] != 'passed'
                  or latest['attempts'] > 1 or latest['hints_used'] > 0)
    if observed['reasoning_status'] == 'contradiction_found':
        reason = '最终数值与理由核验分别记录；理由中发现算术等式矛盾，需要继续核对依据。'
        focus = 'verification'
    elif difficulty:
        reason = '本题有反复尝试、求助或未完成记录，暂不据此减少帮助。'
        focus = 'representation'
    elif observed['reasoning_status'] == 'arithmetic_checked':
        reason = '本题首次作答数值通过，所写算术等式一致；完整推理仍未评阅。'
        focus = 'verification'
    else:
        reason = '本题数值通过，但依据尚未评阅，需要继续说明所用方法与适用条件。'
        focus = 'basis'
    decision = deepcopy(proposal) if proposal else {'action': 'practice', 'reason': reason,
        'support': 'restore' if difficulty else 'fade' if observed['can_fade'] else 'maintain'}
    decision.setdefault('action', 'practice')
    decision.setdefault('reason', reason)
    decision['source'] = 'model' if proposal else 'local'
    decision['source_task_id'] = latest['task_id']
    decision['observation'] = deepcopy(latest)
    decision['question_focus'] = focus
    if difficulty:
        decision['support'] = 'restore'
        decision['support_reason'] = reason
    elif decision.get('support') == 'fade' and not observed['can_fade']:
        decision['support'] = 'maintain'
        decision['support_reason'] = '当前记录不满足减少支持的条件，保留现有帮助。'
    return decision


def track(data, decision, task):
    """Record actual routing separately from a suggested activity."""
    execution = decision['execution']
    execution['next_task_id'] = task['id'] if task else None
    execution['next_stage'] = task['stage'] if task else 'summary'
    if task and task['stage'] != 'practice':
        execution['support_applied'] = False
    else:
        execution['support_applied'] = bool(task)
    entry = {k: deepcopy(decision.get(k)) for k in ('source', 'source_task_id', 'action', 'reason',
        'support', 'support_reason', 'observation', 'execution')}
    entry['sequence'] = len(data.setdefault('teaching_history', [])) + 1
    entry['outcome'] = None
    data['teaching_history'].append(entry)


def observe(data, record):
    for entry in reversed(data.get('teaching_history', [])):
        if entry['execution'].get('next_task_id') == record['task_id']:
            entry['outcome'] = deepcopy(record)
            break
