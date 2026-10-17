"""Isolated evaluation harness using production domain operations and model task budgets."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import time

from sqlalchemy import select

from app.evaluation.probability import Case, Corpus, score, summarize
from app.infrastructure.db.database import Database
from app.infrastructure.db.models import Account, AgentRun, ModelOperation, ModelPolicy
from app.infrastructure.runtime.model_tasks import BUSINESS_TIMEZONE, ModelTaskRunner, publish_configuration
from app.modules.errors import DomainError
from app.modules.identity.service import Actor, Identity
from app.modules.learning.content_catalog import get_pack
from app.modules.learning.content_service import Content
from app.modules.learning.model_coaching import PROMPT_VERSION, UNIT
from app.modules.learning.tutor_contracts import AnswerInput, HintInput, NextInput, ReflectInput
from app.modules.learning.tutor_service import Tutoring
from app.modules.workspaces.service import Workspaces
from app.worker import Worker

EVALUATOR_VERSION = 'probability.evaluator.v1'
TERMINAL = {'succeeded', 'failed', 'cancelled', 'discarded', 'unknown'}


@contextmanager
def exclusive_lock(path):
    """OS lock releases on process exit; stale files do not require unsafe deletion."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'x')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def fingerprints():
    root = Path(__file__).resolve().parents[2]
    paths = ('app/llm/provider.py', 'app/modules/learning/model_coaching.py',
             'app/modules/learning/tutor_graph.py', 'app/modules/learning/tutor_service.py',
             'app/modules/learning/tutor_history.py',
             'app/modules/learning/tutor_contracts.py', 'app/modules/learning/content_catalog.py',
             'app/infrastructure/runtime/model_tasks.py', 'app/worker.py',
             'app/evaluation/probability.py', 'app/evaluation/runner.py', 'scripts/evaluate_probability.py')
    return {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths}


class EvaluationHarness:
    """A fixed synthetic account in a dedicated DB; repeated runs share its daily limit."""
    def __init__(self, db: Database, mode: str, provider_factory=None, *, prompt_version=PROMPT_VERSION, unlimited=False):
        if mode not in {'fake', 'live'}:
            raise ValueError('unknown evaluation mode')
        self.db, self.mode = db, mode
        self.prompt_version = prompt_version
        self.unlimited = unlimited
        self.tutoring, self.spaces = Tutoring(), Workspaces()
        self.worker = Worker(db, enable_models=False)
        self.model_runner = ModelTaskRunner(db, provider_factory=provider_factory)

    @staticmethod
    def actor(session, name):
        account = session.scalar(select(Account).where(Account.username == name).with_for_update())
        if account is None or not account.enabled:
            raise ValueError('evaluation account unavailable')
        return Actor(account, 'local-evaluation-maintenance')

    def initialize(self):
        identity = Identity(self.db, data_mode='synthetic')
        with self.db.transaction() as session:
            existing = session.scalar(select(Account).where(Account.username == 'eval.admin'))
        if existing is None:
            identity.bootstrap('eval.admin', secrets.token_urlsafe(32), '隔离评测管理员')
        with self.db.transaction() as session:
            admin = self.actor(session, 'eval.admin')
            if not session.scalar(select(Account).where(Account.username == 'eval.synthetic')):
                identity.create_student(session, admin, 'eval.synthetic', secrets.token_urlsafe(32), '开发样本学生')
            accounts = list(session.scalars(select(Account)))
            if {a.username for a in accounts} != {'eval.admin', 'eval.synthetic'} or any(a.source_kind != 'synthetic' for a in accounts):
                raise ValueError('not an isolated synthetic evaluation database')
            policy = session.get(ModelPolicy, 1)
            publish_configuration(session, admin, {'expected_revision': policy.revision,
                'vendor': 'deepseek' if self.mode == 'live' else 'fake',
                'prompt_version': self.prompt_version,
                'max_output_tokens': 512, 'account_daily_calls': 0 if self.unlimited else 30, 'site_daily_calls': 0 if self.unlimited else 100})
            pack = get_pack(UNIT)
            content = Content(pack.unit_id, pack.title, pack.version, pack.tasks)
            state = content.view(session)
            content.toggle(session, admin, {'expected_revision': state['revision'], 'content_version': pack.version, 'enabled': True})

    def _read(self, wid, rid):
        with self.db.transaction() as session:
            actor = self.actor(session, 'eval.synthetic')
            run = self.tutoring.owned(session, actor, wid, rid)
            return {'status': run.status, 'view': run.view, 'gate_id': run.gate_id, 'revision': run.revision}

    def _drain_teaching(self, wid, rid):
        for _ in range(128):
            run = self._read(wid, rid)
            if run['status'] != 'queued':
                if run['status'] != 'awaiting_input':
                    raise ValueError('evaluation teaching flow did not pause for input')
                return run
            if not self.worker.step():
                raise ValueError('evaluation teaching input not available')
        raise ValueError('evaluation teaching queue exceeded bound')

    def _send(self, wid, rid, action, **fields):
        run = self._read(wid, rid)
        contract = {'answer': AnswerInput, 'hint': HintInput, 'next': NextInput, 'reflect': ReflectInput}[action]
        payload = contract.model_validate({'action': action, 'gate_id': run['gate_id'],
            'expected_revision': run['revision'], **fields}).model_dump(mode='json')
        with self.db.transaction() as session:
            self.tutoring.submit(session, self.actor(session, 'eval.synthetic'), wid, rid, payload,
                'eval:' + str(run['revision']) + ':' + action)
        return self._drain_teaching(wid, rid)

    def _operation(self, rid):
        with self.db.transaction() as session:
            op = session.scalar(select(ModelOperation).where(ModelOperation.run_id == rid)
                .order_by(ModelOperation.run_revision.desc()).limit(1))
            if op is None:
                return None
            return {'operation_id': op.id, 'configuration_id': op.configuration_id, 'status': op.status,
                'result': op.result, 'error_code': op.error_code, 'usage': op.usage,
                'elapsed_ms': op.elapsed_ms, 'returned_model': op.returned_model,
                'request_hash': op.request_hash, 'snapshot': op.request, 'lease_until': op.lease_until}

    def evaluate(self, case: Case, run_key: str, attempt_id: str):
        pack = get_pack(UNIT)
        with self.db.transaction() as session:
            actor = self.actor(session, 'eval.synthetic')
            workspace = self.spaces.create(session, actor, 'temporary', '自编概率评测 ' + attempt_id,
                'eval:' + run_key + ':' + attempt_id)
            wid = workspace['workspace_id']
            started = self.tutoring.start(session, actor, wid, {'unit_id': UNIT}, 'eval-start')
            rid = started['run_id']
        op = self._operation(rid)
        if op is None:
            run = self._drain_teaching(wid, rid)
            for _ in range(12):
                view = run['view']
                if view['task']['task_id'] == case.task_id:
                    break
                if view['can_answer']:
                    task = next(t for t in pack.tasks if t['id'] == view['task']['task_id'])
                    run = self._send(wid, rid, 'answer', answer=task['answer'])
                elif view['can_reflect']:
                    run = self._send(wid, rid, 'reflect', reflection='自编评测前置步骤，不作为学习表现')
                elif view['can_continue']:
                    run = self._send(wid, rid, 'next')
                else:
                    raise ValueError('evaluation target unreachable')
            else:
                raise ValueError('evaluation preparation exceeded bound')
            while len(run['view']['hints']) < case.hints_before:
                run = self._send(wid, rid, 'hint')
            if len(run['view']['hints']) != case.hints_before:
                raise ValueError('unexpected evaluation hint state')
            self._send(wid, rid, 'hint', coaching_text=case.text)
            op = self._operation(rid)
        elif op['status'] == 'waiting':
            self._drain_teaching(wid, rid)
        # Resume a pending operation, but never reissue a previously leased remote request.
        self.model_runner.recover()
        op = self._operation(rid)
        if op['status'] == 'pending':
            self.model_runner.step(operation_id=op['operation_id'])
            op = self._operation(rid)
        # A killed process may leave a lease for up to 20 seconds. Wait only for fencing/recovery.
        deadline = time.monotonic() + 22
        while op['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(0.25)
            self.model_runner.recover()
            op = self._operation(rid)
        snapshot = op.pop('snapshot')
        row = {**case.model_dump(mode='json'), 'case_id': case.id, 'attempt_id': attempt_id,
            **op, **score(case, op, snapshot),
            'prompt_version': snapshot['prompt_version'], 'content_version': snapshot['content_version'],
            'system_prompt_sha256': hashlib.sha256(snapshot['generation']['messages'][0]['content'].encode()).hexdigest(),
            'outbound_identity_canary_absent': all(value not in json.dumps(snapshot['generation'], ensure_ascii=False)
                for value in ('开发样本学生', 'eval.synthetic', 'student@example.invalid'))}
        # Raw model text, credentials, internal account IDs and complete prompts are not exported.
        row.pop('id', None)
        row.pop('result', None)
        row.pop('lease_until', None)
        return row


def run_evaluation(harness, corpus: Corpus, corpus_hash: str, folder: Path, max_calls=30, resume=False):
    if not 1 <= max_calls <= 30:
        raise ValueError('maximum 30 scheduled model reservations per run')
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / 'manifest.json'
    if resume:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest['source_fingerprints'] != fingerprints() or manifest['corpus_sha256'] != corpus_hash or manifest['mode'] != harness.mode:
            raise ValueError('resume requires identical source, corpus and mode')
    else:
        if manifest_path.exists():
            raise ValueError('run already exists; use explicit resume')
        from uuid import uuid4
        plan = [{'case_id': case.id, 'attempt_id': case.id, 'repeat_of': None} for case in corpus.cases]
        plan.extend({'case_id': identifier, 'attempt_id': identifier + '-R1', 'repeat_of': identifier} for identifier in corpus.repeat_ids)
        manifest = {'run_id': uuid4().hex, 'created_at': datetime.now(BUSINESS_TIMEZONE).isoformat(timespec='seconds'),
            'mode': harness.mode, 'corpus_sha256': corpus_hash, 'corpus_version': corpus.version,
            'content_version': corpus.content_version, 'prompt_version': PROMPT_VERSION,
            'evaluator_version': EVALUATOR_VERSION, 'source_fingerprints': fingerprints(), 'plan': plan[:max_calls],
            'model': 'deepseek-flash' if harness.mode == 'live' else 'scripted'}
        with manifest_path.open('x', encoding='utf-8') as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
    harness.initialize()
    cases = {case.id: case for case in corpus.cases}
    rows = []
    for index, planned in enumerate(manifest['plan']):
        path = folder / 'rows' / (planned['attempt_id'] + '.json')
        if path.is_file():
            row = json.loads(path.read_text(encoding='utf-8'))
        else:
            case = cases[planned['case_id']]
            try:
                row = harness.evaluate(case, manifest['run_id'], planned['attempt_id'])
            except DomainError as error:
                row = {**case.model_dump(mode='json'), 'case_id': case.id, 'attempt_id': planned['attempt_id'],
                    'status': 'blocked', 'error_code': error.code, 'operation_id': None, 'usage': None, 'elapsed_ms': None,
                    'contract_pass': False, 'citation_pass': False, 'policy_pass': False, 'direction_match': False,
                    'substantive_quote': False, 'selected_focus': None, 'quote': '', 'question': None}
                row.pop('id', None)
            row['repeat_of'] = planned['repeat_of']
            atomic_json(path, row)
        rows.append(row)
        # Progress includes identifiers and safe codes, never input text, key or raw exceptions.
        print(json.dumps({'progress': index + 1, 'planned': len(manifest['plan']), 'attempt': row['attempt_id'],
            'status': row['status'], 'direction_match': row['direction_match']}, ensure_ascii=False), flush=True)
    return {**manifest, 'rows': rows, 'summary': summarize(rows)}
