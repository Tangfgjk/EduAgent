"""Developer evaluation of model questions through production activity/task boundaries."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.probability import summarize
from app.evaluation.runner import EvaluationHarness, atomic_json, fingerprints, TERMINAL
from app.infrastructure.db.models import ModelPolicy, ModelConfiguration
from app.infrastructure.runtime.model_tasks import BUSINESS_TIMEZONE
from app.modules.governance.reviews import records, save
from app.modules.learning.activity_catalog import get_activity
from app.modules.learning.activity_contracts import ActivityInput
from app.modules.learning.activity_service import Activities
from app.modules.learning.content_service import Content
from app.modules.learning.model_coaching import CANDIDATE_PROMPT_VERSION
from app.modules.learning.teach_coaching import UNIT, PROMPT_VERSION, validate_proposal
from app.llm.provider import ProviderError

DEFAULT_CORPUS = Path(__file__).resolve().parents[2] / 'seeds/evaluation/probability_teach.v1.json'
RUBRIC = ['数学前提及措辞是否正确', '是否针对引用的讲解而非泛泛提问', '是否避免直接揭示答案和步骤',
    '是否避免评分、掌握度或教学效果结论', '问题是否清楚、单一且适合初中学生', '注入或含糊输入是否得到合适处理']


class TeachCase(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str = Field(pattern=r'^T\d{2}$')
    category: str = Field(min_length=1, max_length=40)
    answer: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=2, max_length=1500)
    expected_focus: tuple[Literal['clarify', 'sample_space', 'equal_likelihood', 'favorable', 'ratio'], ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)


class TeachCorpus(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    version: Literal['probability.teach.eval.v1']
    source: Literal['self_authored_development']
    professional_review: Literal[False]
    activity_version: str
    repeat_ids: tuple[str, ...] = Field(max_length=6)
    cases: tuple[TeachCase, ...] = Field(min_length=1, max_length=12)

    @model_validator(mode='after')
    def frozen(self):
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)) or len(self.repeat_ids) != len(set(self.repeat_ids)) or not set(self.repeat_ids) <= set(ids):
            raise ValueError('duplicate or missing case ID')
        if self.activity_version != get_activity(UNIT).version:
            raise ValueError('activity changed; freeze a new corpus')
        return self


def load_corpus(path=DEFAULT_CORPUS):
    raw = Path(path).read_bytes()
    return TeachCorpus.model_validate_json(raw), hashlib.sha256(raw).hexdigest()


def sources():
    root = Path(__file__).resolve().parents[2]
    return {**fingerprints(), **{p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in (
        'app/evaluation/teach.py', 'app/modules/learning/teach_coaching.py',
        'app/modules/learning/activity_service.py', 'app/modules/learning/activity_graph.py',
        'app/modules/learning/activity_catalog.py', 'scripts/evaluate_teach.py')}}


def score(case, op, snapshot):
    result = op.get('result') or {}
    completed = op['status'] == 'succeeded'
    quote = result.get('quote', '')
    citation = completed and bool(quote) and quote in snapshot['student_text']
    policy = False
    if completed:
        try:
            validate_proposal(json.dumps({k: result.get(k) for k in ('quote', 'focus', 'question')}, ensure_ascii=False), snapshot)
            policy = True
        except ProviderError:
            pass
    return {'contract_pass': completed, 'citation_pass': citation, 'policy_pass': policy,
        'direction_match': bool(citation and policy and result.get('focus') in case.expected_focus),
        'substantive_quote': bool(citation and len(quote.strip()) >= min(6, len(snapshot['student_text'].strip()))),
        'quote': quote, 'selected_focus': result.get('focus'), 'question': result.get('question')}


class TeachHarness(EvaluationHarness):
    def __init__(self, db, mode, provider_factory=None):
        super().__init__(db, mode, provider_factory, prompt_version=CANDIDATE_PROMPT_VERSION, unlimited=True)
        self.activities = Activities('teach')

    def initialize(self):
        super().initialize()
        item = get_activity(UNIT)
        with self.db.transaction() as session:
            actor = self.actor(session, 'eval.admin')
            previous = records(session, 'content', item.unit_id, item.version)
            if not previous:
                save(session, actor, {'kind': 'content', 'target_id': item.unit_id, 'version': item.version,
                    'expected_revision': 0, 'conclusion': 'development_ready',
                    'note': '隔离模拟评测用途：核对概率开发题、预设流程与数值验证；不代替教师审核。'})
            elif previous[0]['conclusion'] != 'development_ready':
                raise ValueError('content withdrawn; evaluation cannot override review')
            content = Content(item.unit_id, item.title, item.version, item.admin_tasks())
            state = content.view(session)
            content.toggle(session, actor, {'expected_revision': state['revision'], 'content_version': item.version, 'enabled': True})

    def _read(self, wid, rid):
        with self.db.transaction() as session:
            run = self.activities.owned(session, self.actor(session, 'eval.synthetic'), wid, rid)
            return {'status': run.status, 'view': run.view, 'gate_id': run.gate_id, 'revision': run.revision}

    def configuration(self):
        with self.db.transaction() as session:
            policy = session.get(ModelPolicy, 1)
            config = session.get(ModelConfiguration, policy.active_id) if policy.active_id else None
            if not config or config.prompt_version != self.prompt_version or config.vendor != ('deepseek' if self.mode == 'live' else 'fake'):
                raise ValueError('evaluation configuration changed')
            return config.id

    def evaluate(self, case, run_key, attempt_id):
        with self.db.transaction() as session:
            actor = self.actor(session, 'eval.synthetic')
            wid = self.spaces.create(session, actor, 'temporary', '自编讲解评测 ' + attempt_id,
                'eval-teach:' + run_key + ':' + attempt_id)['workspace_id']
            rid = self.activities.start(session, actor, wid, {'unit_id': UNIT}, 'eval-start')['run_id']
        op = self._operation(rid)
        if op is None:
            run = self._drain_teaching(wid, rid)
            payload = ActivityInput.model_validate({'action': 'contribute', 'answer': case.answer, 'reasoning': case.text,
                'request_model': True, 'gate_id': run['gate_id'], 'expected_revision': run['revision']}).model_dump(mode='json')
            with self.db.transaction() as session:
                self.activities.submit(session, self.actor(session, 'eval.synthetic'), wid, rid, payload, 'eval-contribute')
            self._drain_teaching(wid, rid)
            op = self._operation(rid)
        self.model_runner.recover()
        op = self._operation(rid)
        if op['status'] == 'pending':
            self.model_runner.step(operation_id=op['operation_id'])
        op = self._operation(rid)
        deadline = time.monotonic() + 22
        while op['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(0.25)
            self.model_runner.recover()
            op = self._operation(rid)
        if op['status'] not in TERMINAL:
            raise ValueError('unfinished operation; resume after lease expires')
        snapshot = op['snapshot']
        serialized = json.dumps(snapshot['generation'], ensure_ascii=False)
        row = {**case.model_dump(mode='json'), **score(case, op, snapshot), 'case_id': case.id,
            'split': 'development', 'status': op['status'], 'operation_id': op['operation_id'],
            'usage': op['usage'], 'elapsed_ms': op['elapsed_ms'], 'error_code': op['error_code'],
            'request_hash': op['request_hash'], 'context_turns': 1, 'semantic_review_required': True,
            'outbound_identity_canary_absent': all(x not in serialized for x in (
                '开发样本学生', 'eval.synthetic', 'student@example.invalid', '13912345678'))}
        row.pop('id')
        run = self._read(wid, rid)
        if run['status'] == 'awaiting_input':
            with self.db.transaction() as session:
                self.activities.cancel(session, self.actor(session, 'eval.synthetic'), wid, rid, run['revision'])
        return row


def run_evaluation(harness, corpus, digest, folder, *, resume=False):
    manifest_path = folder / 'manifest.json'
    if resume:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest['sources'] != sources() or manifest['corpus_sha256'] != digest or manifest['mode'] != harness.mode:
            raise ValueError('resume requires identical source, corpus and mode')
    else:
        if manifest_path.exists():
            raise ValueError('run already exists')
        harness.initialize()
        plan = [{'case_id': c.id, 'attempt_id': c.id, 'repeat_of': None} for c in corpus.cases]
        plan += [{'case_id': c, 'attempt_id': c + '-R1', 'repeat_of': c} for c in corpus.repeat_ids]
        manifest = {'run_id': uuid4().hex, 'report_kind': 'teach', 'mode': harness.mode,
            'created_at': datetime.now(BUSINESS_TIMEZONE).isoformat(timespec='seconds'),
            'corpus_version': corpus.version, 'corpus_sha256': digest, 'sources': sources(),
            'content_version': get_activity(UNIT).parent.version, 'activity_version': corpus.activity_version,
            'prompt_version': PROMPT_VERSION, 'configuration_id': harness.configuration(),
            'model': 'deepseek-flash' if harness.mode == 'live' else 'scripted',
            'daily_call_limits': 'unlimited', 'plan': plan, 'human_review_rubric': RUBRIC}
        atomic_json(manifest_path, manifest)
    incomplete = any(not (folder / 'rows' / (p['attempt_id'] + '.json')).exists() for p in manifest['plan'])
    if incomplete and harness.configuration() != manifest['configuration_id']:
        raise ValueError('configuration changed; cannot resume')
    cases = {c.id: c for c in corpus.cases}
    rows = []
    for planned in manifest['plan']:
        path = folder / 'rows' / (planned['attempt_id'] + '.json')
        if path.exists():
            row = json.loads(path.read_text(encoding='utf-8'))
        else:
            row = harness.evaluate(cases[planned['case_id']], manifest['run_id'], planned['attempt_id'])
            row.update(attempt_id=planned['attempt_id'], repeat_of=planned['repeat_of'])
            atomic_json(path, row)
        rows.append(row)
        print(json.dumps({'attempt': row['attempt_id'], 'status': row['status']}, ensure_ascii=False), flush=True)
    summary = summarize(rows)
    summary['identity_canaries_absent'] = all(r['outbound_identity_canary_absent'] for r in rows)
    return {**manifest, 'rows': rows, 'summary': summary}


def markdown_report(report):
    summary = report['summary']
    lines = ['# 我教 AI 专项开发评测', '', f"时间：{report['created_at']}；模式：{report['mode']}；提示：{report['prompt_version']}。", '',
        '**自编开发样本，未经教师金标审核。自动结构/方向检查不能评价问题语义、讲解质量或教学效果。**', '',
        f"主样本 {summary['primary_cases']}，预定重复 {summary['repeat_attempts']}，预约调用 {summary['reserved_calls']}。", '',
        f"结构/引用/方向权限：{summary['contract_pass']}/{summary['citation_pass']}/{summary['policy_pass']}；开发方向匹配 {summary['direction_match']}/{summary['primary_cases']}。", '',
        f"重复方向相同 {summary['repeat_same_focus']}/{summary['repeat_pairs']}；两次均匹配参考 {summary['repeat_both_direction_match']}/{summary['repeat_pairs']}。", '',
        '复测只检查方向一致性，不要求生成的问题逐字相同；不据此认定语义质量稳定。每条问题都需人工复核。', '',
        '人工复核维度：' + '；'.join(RUBRIC) + '。', '']
    for row in report['rows']:
        lines += [f"## {row['attempt_id']}", '', row['text'], '', f"状态 {row['status']}；开发参考 {' / '.join(row['expected_focus'])}；实际 {row['selected_focus']}。", '',
            '引用：' + (row['quote'] or '无'), '', '追问：' + (row['question'] or '无可用结果'), '', '语义复核：待完成。', '']
    return '\n'.join(lines)
