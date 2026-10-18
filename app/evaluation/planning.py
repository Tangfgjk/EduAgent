"""Three synthetic planning flows; model suggestions are never a teaching-effect evaluation."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.evaluation.runner import EvaluationHarness, atomic_json, TERMINAL
from app.evaluation.teach import TeachHarness, sources
from app.evaluation.teach_multiturn import drain
from app.infrastructure.db.models import ModelOperation, StrategyVersion
from app.infrastructure.runtime.model_tasks import BUSINESS_TIMEZONE
from app.modules.planning.contracts import StartStrategy
from app.modules.planning.model import PROMPT_VERSION, validate_proposal
from app.modules.planning.service import Materials, Planning, public_strategy
from app.modules.learning.activity_catalog import get_activity
from app.modules.learning.activity_service import Activities
from app.modules.learning.content_service import Content
from app.modules.learning.activity_contracts import ActivityInput
from app.llm.provider import ProviderError

CORPUS = Path(__file__).resolve().parents[2] / 'seeds/evaluation/planning_sources.v1.json'


class PlanningHarness(TeachHarness):
    def __init__(self, db, mode, provider_factory=None):
        super().__init__(db, mode, provider_factory)
        self.planning = Planning()

    def initialize(self):
        EvaluationHarness.initialize(self)
        item = get_activity('math.g9.probability.activities.dev1')
        with self.db.transaction() as session:
            actor = self.actor(session, 'eval.admin')
            content = Content(item.unit_id, item.title, item.version, item.admin_tasks())
            content.toggle(session, actor, {'expected_revision': content.view(session)['revision'], 'content_version': item.version, 'enabled': True})

    def _read(self, wid, rid):
        with self.db.transaction() as session:
            return public_strategy(session, self.planning.owned(session, self.actor(session, 'eval.synthetic'), wid, rid))

    def evidence(self, wid):
        activities = Activities('roundtable')
        with self.db.transaction() as session:
            rid = activities.start(session, self.actor(session, 'eval.synthetic'), wid,
                {'unit_id': 'math.g9.probability.activities.dev1'}, 'eval-fixed-evidence')['run_id']
        for phase, action, answer, reasoning in [('opening', 'contribute', '1/2', '我先把红蓝颜色当成两个基本结果，还没核对等可能。'),
            ('challenge', 'conclude', '2/5', '我修正为按每个球计数，核对题干的等可能条件。')]:
            for _ in range(20):
                with self.db.transaction() as session:
                    state = activities.owned(session, self.actor(session, 'eval.synthetic'), wid, rid)
                    status, view, gate, revision = state.status, state.view, state.gate_id, state.revision
                if status != 'queued': break
                if not self.worker.step(): raise ValueError('evidence queue unavailable')
            if status == 'awaiting_input' and view['phase'] == phase:
                body = ActivityInput.model_validate({'action': action, 'answer': answer, 'reasoning': reasoning,
                    'gate_id': gate, 'expected_revision': revision}).model_dump(mode='json')
                with self.db.transaction() as session:
                    activities.submit(session, self.actor(session, 'eval.synthetic'), wid, rid, body, action)
        for _ in range(20):
            with self.db.transaction() as session:
                state = activities.owned(session, self.actor(session, 'eval.synthetic'), wid, rid)
                if state.status == 'completed': return
            if not self.worker.step(): raise ValueError('evidence unfinished')
        raise ValueError('evidence exceeded bound')

    def evaluate(self, case, run_key):
        with self.db.transaction() as session:
            actor = self.actor(session, 'eval.synthetic')
            wid = self.spaces.create(session, actor, 'project', '自编规划 ' + case['id'], 'planning:' + run_key + ':' + case['id'])['workspace_id']
        if case['with_facts']: self.evidence(wid)
        materials = Materials()
        with self.db.transaction() as session:
            admin = self.actor(session, 'eval.admin')
            item = materials.create(session, admin, {'title': '自编规划参考 ' + case['id'], 'kind': 'development_reference',
                'edition': 'dev1', 'source_ref': '固定自编开发评测，不是正式教材或教师审核', 'body': case['material']})
            if not item['enabled']:
                materials.release(session, admin, item['material_id'], {'expected_revision': item['revision'], 'enabled': True})
            body = StartStrategy(learning_experience=case['experience'], daily_minutes=case['minutes'], reflection_focus=case['focus'],
                material_ids=[item['material_id']], request_model=True).model_dump(mode='json')
            rid = self.planning.start(session, self.actor(session, 'eval.synthetic'), wid, body, 'start')['run_id']
        drain(self, wid, rid)
        self.model_runner.recover()
        op = self._operation(rid)
        self.model_runner.step(operation_id=op['operation_id'])
        op = self._operation(rid)
        if op['status'] not in TERMINAL: raise ValueError('unfinished operation; resume after lease expiry')
        state = drain(self, wid, rid)
        valid = False
        if op['status'] == 'succeeded':
            try:
                validate_proposal(json.dumps(op['result'], ensure_ascii=False), op['snapshot'])
                valid = True
            except ProviderError: pass
            for phase, action, name, is_admin in [('admin_review', 'approve', 'eval.admin', True), ('student_confirm', 'confirm', 'eval.synthetic', False)]:
                if state['view']['phase'] == phase:
                    with self.db.transaction() as session:
                        actor = self.actor(session, name)
                        run = self.planning.admin_run(session, rid) if is_admin else self.planning.owned(session, actor, wid, rid)
                        self.planning.decide(session, actor, run, {'action': action, 'gate_id': state['gate_id'], 'expected_revision': state['revision'],
                            'note': '仅验证自编开发流程与白名单；不是专业教学审核。'}, action, admin=is_admin)
                    state = drain(self, wid, rid)
            with self.db.transaction() as session:
                actor = self.actor(session, 'eval.synthetic')
                active = self.planning.active(session, actor, wid)
                if active['version_id'] == rid:
                    self.planning.rollback(session, actor, wid, {'expected_revision': active['revision'], 'target_version': None,
                        'note': '隔离开发流程验证后回滚默认。'}, 'eval-rollback')
        serialized = json.dumps(op['snapshot']['generation'], ensure_ascii=False)
        return {'case_id': case['id'], 'status': op['status'], 'policy': (op['result'] or {}).get('policy'),
            'citations': (op['result'] or {}).get('citations', []), 'sources': op['snapshot']['sources'],
            'source_refs': op['snapshot']['source_refs'], 'contract_and_citations_pass': valid,
            'identity_canaries_absent': all(v not in serialized for v in ('开发样本学生', 'eval.synthetic', 'student@example.invalid', '13912345678')),
            'phase': state['view']['phase'], 'flow_completed': state['status'] == 'completed',
            'operation_id': op['operation_id'], 'request_hash': op['request_hash'], 'usage': op['usage'],
            'error_code': op['error_code'], 'elapsed_ms': op['elapsed_ms'], 'semantic_review': 'pending'}


def run(harness, folder, *, resume=False):
    raw = CORPUS.read_bytes(); corpus = json.loads(raw); digest = hashlib.sha256(raw).hexdigest()
    root = CORPUS.parents[2]
    fingerprint = {**sources(), **{p: hashlib.sha256((root/p).read_bytes()).hexdigest() for p in (
        'app/evaluation/planning.py', 'scripts/evaluate_planning.py', 'app/modules/planning/model.py',
        'app/modules/planning/service.py', 'app/modules/planning/graph.py', 'app/modules/planning/contracts.py')}}
    if resume:
        manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        if manifest['sources'] != fingerprint or manifest['corpus_sha256'] != digest or manifest['mode'] != harness.mode:
            raise ValueError('resume requires same code, corpus and mode')
    else:
        if (folder/'manifest.json').exists(): raise ValueError('run exists')
        harness.initialize()
        manifest = {'run_id': uuid4().hex, 'mode': harness.mode, 'prompt_version': PROMPT_VERSION,
            'created_at': datetime.now(BUSINESS_TIMEZONE).isoformat(timespec='seconds'), 'sources': fingerprint,
            'corpus_sha256': digest, 'corpus_version': corpus['version'], 'configuration_id': harness.configuration(),
            'planned_calls': 3, 'source': 'self_authored_development', 'professional_review': False}
        atomic_json(folder/'manifest.json', manifest)
    if any(not (folder/'rows'/(c['id']+'.json')).exists() for c in corpus['cases']) and harness.configuration() != manifest['configuration_id']:
        raise ValueError('configuration changed')
    rows = []
    for case in corpus['cases']:
        path = folder/'rows'/(case['id']+'.json')
        row = json.loads(path.read_text(encoding='utf-8')) if path.exists() else harness.evaluate(case, manifest['run_id'])
        atomic_json(path, row); rows.append(row)
        print(json.dumps({'case': case['id'], 'status': row['status']}), flush=True)
    return {**manifest, 'rows': rows, 'summary': {'attempts': len(rows), 'valid_results': sum(r['contract_and_citations_pass'] for r in rows),
        'flows_completed': sum(r['flow_completed'] for r in rows), 'identity_canaries_absent': all(r['identity_canaries_absent'] for r in rows),
        'input_tokens_known': sum((r['usage'] or {}).get('input_tokens') or 0 for r in rows),
        'output_tokens_known': sum((r['usage'] or {}).get('output_tokens') or 0 for r in rows),
        'missing_usage': sum(not r['usage'] or any(r['usage'].get(k) is None for k in ('input_tokens','output_tokens')) for r in rows)}}


def markdown_report(report):
    lines = ['# 有来源规划专项开发评测', '', '时间：'+report['created_at']+'；模式：'+report['mode']+'。', '',
        '三组自编数据，每组最多一次请求。自动引用与受限字段通过不代表规划建议适切或教学有效；审核、确认和回滚为隔离工程操作，不是实际教师复核。', '',
        '汇总：'+json.dumps(report['summary'],ensure_ascii=False), '']
    for row in report['rows']:
        lines += ['## '+row['case_id'], '', '状态：'+row['status']+'；流程完成：'+str(row['flow_completed']), '',
            '候选：'+json.dumps(row['policy'],ensure_ascii=False), '']
        for citation in row['citations']:
            lines += ['- '+citation['source_id']+'：引用“'+citation['quote']+'”；'+citation['reason']]
        lines += ['', '语义复核：待完成。', '']
    return '\n'.join(lines)
