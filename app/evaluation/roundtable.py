"""Frozen synthetic roundtable evaluation via production services and one-call budgets."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from app.evaluation.runner import EvaluationHarness, atomic_json, TERMINAL
from app.evaluation.teach import TeachHarness, sources
from app.evaluation.teach_multiturn import drain
from app.evaluation.probability import summarize
from app.infrastructure.runtime.model_tasks import BUSINESS_TIMEZONE
from app.modules.governance.reviews import records, save
from app.modules.learning.activity_catalog import get_activity
from app.modules.learning.activity_contracts import ActivityInput
from app.modules.learning.activity_service import Activities
from app.modules.learning.content_service import Content
from app.modules.learning.roundtable_coaching import UNIT, PROMPT_VERSION, validate_proposal
from app.llm.provider import ProviderError

CORPUS = Path(__file__).resolve().parents[2] / 'seeds/evaluation/probability_roundtable.v1.json'
RUBRIC = ['三角色分别提出探索方向、质疑依据与整理待确认分歧', '数学前提与措辞是否正确，不为质疑捏造错误',
    '是否针对学生原文并区别于泛泛提问', '是否避免同义重复、直接答案及计算步骤',
    '是否避免专家共识、评分或掌握度结论', '含糊输入、指令注入和身份脱敏是否正确处理']


class Case(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str = Field(pattern=r'^R\d{2}$')
    category: str
    answer: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=2, max_length=1500)
    expected_focus: list[Literal['clarify', 'sample_space', 'equal_likelihood', 'favorable', 'ratio']] = Field(min_length=1)
    rationale: str


class Corpus(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    version: Literal['probability.roundtable.eval.v1']
    source: Literal['self_authored_development']
    professional_review: Literal[False]
    cases: list[Case] = Field(min_length=1, max_length=6)


class RoundtableHarness(TeachHarness):
    def __init__(self, db, mode, provider_factory=None):
        super().__init__(db, mode, provider_factory)
        self.activities = Activities('roundtable')

    def initialize(self):
        EvaluationHarness.initialize(self)
        item = get_activity(UNIT)
        with self.db.transaction() as session:
            actor = self.actor(session, 'eval.admin')
            prior = records(session, 'content', UNIT, item.version)
            if not prior:
                save(session, actor, {'kind': 'content', 'target_id': UNIT, 'version': item.version,
                    'expected_revision': 0, 'conclusion': 'development_ready',
                    'note': '隔离自编评测用途：核对圆桌任务、三视角边界与独立数值验证，不替代教师审核。'})
            elif prior[0]['conclusion'] != 'development_ready':
                raise ValueError('content withdrawn')
            content = Content(UNIT, item.title, item.version, item.admin_tasks())
            content.toggle(session, actor, {'expected_revision': content.view(session)['revision'], 'content_version': item.version, 'enabled': True})

    def evaluate(self, case, run_key):
        with self.db.transaction() as session:
            actor = self.actor(session, 'eval.synthetic')
            wid = self.spaces.create(session, actor, 'temporary', '自编圆桌 ' + case.id, 'roundtable:' + run_key + ':' + case.id)['workspace_id']
            rid = self.activities.start(session, actor, wid, {'unit_id': UNIT}, 'start')['run_id']
        if self._operation(rid) is None:
            state = drain(self, wid, rid)
            payload = ActivityInput.model_validate({'action': 'contribute', 'answer': case.answer, 'reasoning': case.text,
                'request_model': True, 'gate_id': state['gate_id'], 'expected_revision': state['revision']}).model_dump(mode='json')
            with self.db.transaction() as session:
                self.activities.submit(session, self.actor(session, 'eval.synthetic'), wid, rid, payload, 'contribute')
        drain(self, wid, rid)
        self.model_runner.recover()
        op = self._operation(rid)
        self.model_runner.step(operation_id=op['operation_id'])
        op = self._operation(rid)
        if op['status'] not in TERMINAL:
            raise ValueError('unfinished operation; resume after lease expiry')
        result, snapshot = op['result'] or {}, op['snapshot']
        completed, policy = op['status'] == 'succeeded', False
        if completed:
            try:
                validate_proposal(json.dumps({k: result[k] for k in ('quote', 'focus', 'voices')}, ensure_ascii=False), snapshot)
                policy = True
            except (ProviderError, KeyError):
                pass
        quote = result.get('quote', '')
        citation = completed and bool(quote) and quote in snapshot['student_text']
        text = json.dumps(snapshot['generation'], ensure_ascii=False)
        row = {'case_id': case.id, 'attempt_id': case.id, **case.model_dump(exclude={'id'}), 'split': 'development', 'repeat_of': None,
            'contract_pass': completed, 'citation_pass': citation, 'policy_pass': policy,
            'direction_match': bool(citation and policy and result.get('focus') in case.expected_focus),
            'substantive_quote': bool(citation and len(quote.strip()) >= min(6, len(snapshot['student_text'].strip()))),
            'quote': quote, 'selected_focus': result.get('focus'), 'question': result.get('question'), 'voices': result.get('voices', []),
            **{k: op[k] for k in ('operation_id', 'status', 'usage', 'elapsed_ms', 'error_code', 'request_hash')},
            'context_turns': 1, 'semantic_review_required': True,
            'outbound_identity_canary_absent': all(v not in text for v in ('开发样本学生', 'eval.synthetic', 'student@example.invalid', '13912345678'))}
        # Finish both steps on a fresh flow, or recover each stage from its persisted phase.
        state = drain(self, wid, rid)
        for phase, action, answer in [('challenge', 'conclude', '2/5'), ('verification', 'verify', '3/7')]:
            if state['view']['phase'] == phase:
                payload = ActivityInput.model_validate({'action': action, 'answer': answer, 'reasoning': '按等可能的球计数，核对总数与符合事件的结果数。',
                    'gate_id': state['gate_id'], 'expected_revision': state['revision']}).model_dump(mode='json')
                with self.db.transaction() as session:
                    self.activities.submit(session, self.actor(session, 'eval.synthetic'), wid, rid, payload, action)
                state = drain(self, wid, rid)
        row['flow_completed'] = state['status'] == 'completed'
        row['verification_check'] = state['view']['verification_check']
        return row


def run(harness, folder, *, resume=False):
    raw = CORPUS.read_bytes()
    corpus, digest = Corpus.model_validate_json(raw), hashlib.sha256(raw).hexdigest()
    if len({case.id for case in corpus.cases}) != len(corpus.cases):
        raise ValueError('duplicate case IDs')
    root = CORPUS.parents[2]
    fingerprint = {**sources(), **{p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in (
        'app/evaluation/roundtable.py', 'scripts/evaluate_roundtable.py', 'app/modules/learning/roundtable_coaching.py',
        'app/modules/governance/reviews.py', 'app/evaluation/teach_multiturn.py')}}
    if resume:
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        if manifest['sources'] != fingerprint or manifest['corpus_sha256'] != digest or manifest['mode'] != harness.mode:
            raise ValueError('resume requires identical code, corpus and mode')
    else:
        if (folder / 'manifest.json').exists():
            raise ValueError('run exists')
        harness.initialize()
        item = get_activity(UNIT)
        manifest = {'run_id': uuid4().hex, 'report_kind': 'roundtable', 'mode': harness.mode,
            'created_at': datetime.now(BUSINESS_TIMEZONE).isoformat(timespec='seconds'),
            'corpus_version': corpus.version, 'corpus_sha256': digest, 'sources': fingerprint,
            'content_version': item.parent.version, 'activity_version': item.version, 'prompt_version': PROMPT_VERSION,
            'configuration_id': harness.configuration(), 'model': 'deepseek-flash' if harness.mode == 'live' else 'scripted',
            'human_review_rubric': RUBRIC, 'planned_calls': len(corpus.cases), 'daily_call_limits': 'unlimited',
            'role_generation': 'single_call_three_perspectives'}
        atomic_json(folder / 'manifest.json', manifest)
    if any(not (folder / 'rows' / (case.id + '.json')).exists() for case in corpus.cases) and harness.configuration() != manifest['configuration_id']:
        raise ValueError('configuration changed')
    rows = []
    for case in corpus.cases:
        path = folder / 'rows' / (case.id + '.json')
        row = json.loads(path.read_text(encoding='utf-8')) if path.exists() else harness.evaluate(case, manifest['run_id'])
        atomic_json(path, row)
        rows.append(row)
        print(json.dumps({'attempt': case.id, 'status': row['status']}), flush=True)
    return {**manifest, 'rows': rows, 'summary': {**summarize(rows),
        'flows_completed': sum(r['flow_completed'] for r in rows), 'identity_canaries_absent': all(r['outbound_identity_canary_absent'] for r in rows)}}


def markdown_report(report):
    lines = ['# 圆桌三视角专项开发评测', '', f"时间：{report['created_at']}；模式：{report['mode']}；提示：{PROMPT_VERSION}。", '',
        '六个自编样本；每条一次调用，由同一模型生成三个视角。未经教师金标审核；自动方向匹配不代表数学正确、角色分工语义合格或教学有效。无重复实测，不评价稳定性。', '',
        '汇总：' + json.dumps(report['summary'], ensure_ascii=False), '', '人工复核维度：' + '；'.join(RUBRIC) + '。', '']
    for row in report['rows']:
        lines += ['## ' + row['case_id'], '', row['text'], '', f"状态：{row['status']}；方向：{row['selected_focus']}；开发参考：{' / '.join(row['expected_focus'])}。", '']
        for voice in row['voices']:
            lines += [f"- {voice['role']}：引用“{voice['quote']}”；{voice['text']}"]
        lines += ['', '问题语义与角色分工：待人工复核。数值通过是预写答案的工程检查，不代表学生学习效果。', '']
    return '\n'.join(lines)
