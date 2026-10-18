"""Fixed synthetic three-round flows through production services; resumable without retries."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.evaluation.teach import TeachHarness, TeachCase, RUBRIC, score, sources
from app.evaluation.probability import summarize
from app.evaluation.runner import atomic_json, TERMINAL
from app.infrastructure.db.models import LearningInput, ModelOperation
from app.infrastructure.runtime.model_tasks import BUSINESS_TIMEZONE
from app.modules.learning.activity_catalog import get_activity
from app.modules.learning.activity_contracts import ActivityInput
from app.modules.learning.teach_coaching import UNIT

CORPUS = Path(__file__).resolve().parents[2] / 'seeds/evaluation/probability_teach_multiturn.v1.json'


def drain(harness, wid, rid):
    for _ in range(128):
        state = harness._read(wid, rid)
        if state['status'] in {'awaiting_input', 'completed'}:
            return state
        if state['status'] != 'queued' or not harness.worker.step():
            raise ValueError('teaching flow unavailable')
    raise ValueError('teaching queue exceeded bound')


def run(harness: TeachHarness, folder, *, resume=False):
    raw = CORPUS.read_bytes()
    corpus = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    fingerprint = {**sources(), 'app/evaluation/teach_multiturn.py': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scripts/evaluate_teach_multiturn.py': hashlib.sha256((CORPUS.parents[2] / 'scripts/evaluate_teach_multiturn.py').read_bytes()).hexdigest()}
    if resume:
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        if manifest['sources'] != fingerprint or manifest['corpus_sha256'] != digest or manifest['mode'] != harness.mode:
            raise ValueError('resume requires identical source, corpus and mode')
        if harness.configuration() != manifest['configuration_id']:
            raise ValueError('configuration changed')
    else:
        if (folder / 'manifest.json').exists():
            raise ValueError('run exists')
        harness.initialize()
        item = get_activity(UNIT)
        manifest = {'run_id': uuid4().hex, 'report_kind': 'teach', 'mode': harness.mode,
            'created_at': datetime.now(BUSINESS_TIMEZONE).isoformat(timespec='seconds'),
            'corpus_version': corpus['version'], 'corpus_sha256': digest, 'sources': fingerprint,
            'content_version': item.parent.version, 'activity_version': item.version,
            'prompt_version': 'probability.teach.followup.v1+v2', 'configuration_id': harness.configuration(),
            'model': 'deepseek-flash' if harness.mode == 'live' else 'scripted',
            'human_review_rubric': RUBRIC, 'daily_call_limits': 'unlimited', 'planned_calls': 6}
        atomic_json(folder / 'manifest.json', manifest)
    rows, flows = [], []
    for scenario in corpus['scenarios']:
        with harness.db.transaction() as session:
            actor = harness.actor(session, 'eval.synthetic')
            wid = harness.spaces.create(session, actor, 'temporary', '自编多轮讲解 ' + scenario['id'],
                manifest['run_id'] + ':' + scenario['id'])['workspace_id']
            rid = harness.activities.start(session, actor, wid, {'unit_id': UNIT}, 'start')['run_id']
        for index, turn in enumerate(scenario['turns']):
            key = 'turn:' + str(index)
            # Recover the persisted input even if its derived JSON was not written before interruption.
            with harness.db.transaction() as session:
                existing = session.scalar(select(LearningInput).where(LearningInput.payload['run_id'].as_string() == rid,
                    LearningInput.idempotency_key == key))
                input_id = existing.id if existing else None
            if input_id is None:
                state = drain(harness, wid, rid)
                payload = ActivityInput.model_validate({'action': 'contribute' if index == 0 else 'refine',
                    'answer': turn['answer'], 'reasoning': turn['text'], 'request_model': True,
                    'expected_revision': state['revision'], 'gate_id': state['gate_id']}).model_dump(mode='json')
                with harness.db.transaction() as session:
                    harness.activities.submit(session, harness.actor(session, 'eval.synthetic'), wid, rid, payload, key)
            drain(harness, wid, rid)
            with harness.db.transaction() as session:
                entry = session.scalar(select(LearningInput).where(LearningInput.payload['run_id'].as_string() == rid,
                    LearningInput.idempotency_key == key))
                oid = session.scalar(select(ModelOperation.id).where(ModelOperation.input_id == entry.id))
            harness.model_runner.recover()
            harness.model_runner.step(operation_id=oid)  # Terminal operations are never sent again.
            with harness.db.transaction() as session:
                saved = session.get(ModelOperation, oid)
                if saved.status not in TERMINAL:
                    raise ValueError('operation unfinished; resume after lease expires')
                op = {k: getattr(saved, k) for k in ('status', 'result', 'usage', 'elapsed_ms', 'error_code', 'request_hash')}
                snapshot = saved.request
            case = TeachCase(id='T' + str(index + 1).zfill(2), category='multiturn_' + scenario['id'], **turn)
            text = json.dumps(snapshot['generation'], ensure_ascii=False)
            row = {'case_id': scenario['id'] + '-' + str(index + 1), 'attempt_id': scenario['id'] + '-' + str(index + 1),
                **turn, 'category': case.category, 'split': 'development', 'repeat_of': None, **score(case, op, snapshot),
                **{k: op[k] for k in ('status', 'usage', 'elapsed_ms', 'error_code', 'request_hash')},
                'operation_id': oid, 'context_turns': snapshot.get('context_turns', 1), 'semantic_review_required': True,
                'outbound_identity_canary_absent': all(v not in text for v in ('开发样本学生', 'eval.synthetic', '13912345678'))}
            rows.append(row)
            atomic_json(folder / 'rows' / (row['case_id'] + '.json'), row)
            print(json.dumps({'attempt': row['case_id'], 'status': row['status'], 'context_turns': row['context_turns']}), flush=True)
        for action, answer, reasoning in [('conclude', '2/5', '按等可能的球计数，分子是符合事件的结果数，分母是总数。'),
                                           ('verify', '3/7', '独立检查：七个等可能的球，其中三个符合题目条件。')]:
            state = drain(harness, wid, rid)
            # Deterministic final inputs make resumed completed flows read-only.
            with harness.db.transaction() as session:
                prior = session.scalar(select(LearningInput.id).where(LearningInput.payload['run_id'].as_string() == rid,
                    LearningInput.idempotency_key == action))
            if prior is None:
                payload = ActivityInput.model_validate({'action': action, 'answer': answer, 'reasoning': reasoning,
                    'gate_id': state['gate_id'], 'expected_revision': state['revision']}).model_dump(mode='json')
                with harness.db.transaction() as session:
                    harness.activities.submit(session, harness.actor(session, 'eval.synthetic'), wid, rid, payload, action)
        state = drain(harness, wid, rid)
        flows.append({'scenario_id': scenario['id'], 'status': state['status'], 'model_rounds': state['view']['model_rounds'],
            'verification_check': state['view']['verification_check']})
    return {**manifest, 'rows': rows, 'summary': summarize(rows), 'flows': flows}
