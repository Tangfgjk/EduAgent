"""Bounded dialogue evaluation, sharing the existing fixed account and daily budget."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.evaluation.probability import Case, score, summarize
from app.evaluation.runner import EvaluationHarness, atomic_json, fingerprints
from app.infrastructure.db.models import Account, ModelConfiguration, ModelDailyBudget, ModelOperation, ModelPolicy
from app.infrastructure.runtime.model_tasks import BUSINESS_TIMEZONE, day_key
from app.modules.learning.content_catalog import get_pack
from app.modules.learning.model_coaching import MULTITURN_PROMPT_VERSION, MULTITURN_PROMPTS, UNIT

DEFAULT_CORPUS = Path(__file__).resolve().parents[2] / 'seeds/evaluation/probability_multiturn.v1.json'
HOLDOUT_CORPUS = DEFAULT_CORPUS.with_name('probability_multiturn_holdout.v1.json')
REGRESSION_CORPUS = DEFAULT_CORPUS.with_name('probability_multiturn_regression.v1.json')


class Turn(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    text: str = Field(min_length=2, max_length=1500)
    expected_focus: tuple[Literal['clarify', 'sample_space', 'equal_likelihood', 'favorable', 'ratio'], ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)


class Dialogue(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str = Field(pattern=r'^M\d{2}$')
    task_id: Literal['P-PRB-01', 'P-PRB-02']
    initial_hints: int = Field(ge=0, le=3, strict=True)
    category: str
    turns: tuple[Turn, ...] = Field(min_length=2, max_length=4)

    @model_validator(mode='after')
    def feasible(self):
        if self.initial_hints + len(self.turns) > 4:
            raise ValueError('dialogue exceeds hint cap')
        for index, turn in enumerate(self.turns):
            self.case(index)  # Reuse reference direction permission validation.
        return self

    def case(self, index):
        return Case(id='D' + self.id[1:], task_id=self.task_id, hints_before=self.initial_hints + index,
            split='development', category=self.category, **self.turns[index].model_dump())


class MultiCorpus(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    version: Literal['probability.multiturn.eval.v1', 'probability.multiturn.holdout.v1', 'probability.multiturn.regression.v1']
    source: Literal['self_authored_development']
    professional_review: Literal[False]
    content_version: str
    dialogues: tuple[Dialogue, ...] = Field(min_length=1, max_length=10)
    context_pairs: tuple[tuple[str, str], ...] = ()

    @model_validator(mode='after')
    def unique_and_current(self):
        if len({d.id for d in self.dialogues}) != len(self.dialogues):
            raise ValueError('duplicate dialogue ID')
        if self.content_version != get_pack(UNIT).version:
            raise ValueError('content changed; freeze a new corpus')
        if sum(len(d.turns) for d in self.dialogues) > 30:
            raise ValueError('more than daily account limit')
        turns = {f'{d.id}-{index+1}': (d, index, turn) for d in self.dialogues for index, turn in enumerate(d.turns)}
        for first, second in self.pairs():
            if first not in turns or second not in turns or first == second:
                raise ValueError('missing context control turn')
            a, ai, at = turns[first]
            b, bi, bt = turns[second]
            if (ai < 1 or bi < 1 or a.task_id != b.task_id or a.initial_hints + ai != b.initial_hints + bi
                    or at.text != bt.text or not set(at.expected_focus).isdisjoint(bt.expected_focus)):
                raise ValueError('context pair must isolate history with disjoint references')
        return self

    def pairs(self):
        return self.context_pairs or ((('M03-2', 'M04-2'),) if self.version == 'probability.multiturn.eval.v1' else ())


def load_corpus(path=DEFAULT_CORPUS):
    raw = Path(path).read_bytes()
    return MultiCorpus.model_validate_json(raw), hashlib.sha256(raw).hexdigest()


def sources():
    root = Path(__file__).resolve().parents[2]
    return {**fingerprints(), **{name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ('app/evaluation/multiturn.py', 'scripts/evaluate_probability_multiturn.py')}}


class MultiHarness(EvaluationHarness):
    def __init__(self, db, mode, provider_factory=None, *, unlimited=False, prompt_version=MULTITURN_PROMPT_VERSION):
        if prompt_version not in MULTITURN_PROMPTS:
            raise ValueError('multi-turn evaluation requires a multi-turn prompt')
        super().__init__(db, mode, provider_factory, prompt_version=prompt_version, unlimited=unlimited)

    def remaining(self):
        if self.unlimited:
            return None
        with self.db.transaction() as session:
            account = session.scalar(select(Account).where(Account.username == 'eval.synthetic'))
            global_budget = session.get(ModelDailyBudget, (day_key(), 'global'))
            budget = session.get(ModelDailyBudget, (day_key(), 'account:' + account.id)) if account else None
            return min(30 - (budget.calls if budget else 0), 100 - (global_budget.calls if global_budget else 0))

    def configuration(self):
        with self.db.transaction() as session:
            policy = session.get(ModelPolicy, 1)
            config = session.get(ModelConfiguration, policy.active_id) if policy.active_id else None
            if not config or config.prompt_version != self.prompt_version or config.vendor != ('deepseek' if self.mode == 'live' else 'fake'):
                raise ValueError('evaluation configuration changed')
            return config.id

    def dialogue(self, dialogue, run_key):
        with self.db.transaction() as session:
            actor = self.actor(session, 'eval.synthetic')
            space = self.spaces.create(session, actor, 'temporary', '自编多轮评测 ' + dialogue.id,
                'eval-multi:' + run_key + ':' + dialogue.id)
            wid = space['workspace_id']
            rid = self.tutoring.start(session, actor, wid, {'unit_id': UNIT}, 'eval-start')['run_id']
        return wid, rid

    def evaluate_turn(self, dialogue, index, run_key):
        wid, rid = self.dialogue(dialogue, run_key)
        with self.db.transaction() as session:
            operations = list(session.scalars(select(ModelOperation).where(ModelOperation.run_id == rid)
                .order_by(ModelOperation.run_revision)))
            existing_id = operations[index].id if len(operations) > index else None
        case = dialogue.case(index)
        if existing_id is None:
            if len(operations) != index:
                raise ValueError('dialogue turn order mismatch')
            run = self._drain_teaching(wid, rid)
            pack = get_pack(UNIT)
            for _ in range(12):
                view = run['view']
                if view['task']['task_id'] == dialogue.task_id:
                    break
                if view['can_answer']:
                    task = next(t for t in pack.tasks if t['id'] == view['task']['task_id'])
                    run = self._send(wid, rid, 'answer', answer=task['answer'])
                elif view['can_reflect']:
                    run = self._send(wid, rid, 'reflect', reflection='自编多轮评测前置步骤')
                elif view['can_continue']:
                    run = self._send(wid, rid, 'next')
                else:
                    raise ValueError('evaluation target unreachable')
            else:
                raise ValueError('evaluation preparation exceeded bound')
            while len(run['view']['hints']) < case.hints_before:
                run = self._send(wid, rid, 'hint')
            if len(run['view']['hints']) != case.hints_before:
                raise ValueError('unexpected hint state')
            self._send(wid, rid, 'hint', coaching_text=case.text)
        else:
            self._drain_teaching(wid, rid)
        # Read the exact ordinal operation, including when derived rows were lost.
        def operation():
            with self.db.transaction() as session:
                op = session.scalar(select(ModelOperation).where(ModelOperation.run_id == rid)
                    .order_by(ModelOperation.run_revision).offset(index).limit(1))
                return {'operation_id': op.id, 'status': op.status, 'result': op.result, 'usage': op.usage,
                    'error_code': op.error_code, 'elapsed_ms': op.elapsed_ms, 'snapshot': op.request}
        op = operation()
        self.model_runner.recover()
        op = operation()
        if op['status'] == 'pending':
            self.model_runner.step(operation_id=op['operation_id'])
            op = operation()
        deadline = time.monotonic() + 22
        while op['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(0.25)
            self.model_runner.recover()
            op = operation()
        if op['status'] in {'waiting', 'pending', 'running'}:
            raise ValueError('unfinished operation; resume after its lease expires')
        snapshot = op.pop('snapshot')
        text = json.dumps(snapshot['generation'], ensure_ascii=False)
        row = {**case.model_dump(mode='json'), **op, **score(case, op, snapshot),
            'case_id': dialogue.id + '-' + str(index + 1), 'dialogue_id': dialogue.id,
            'turn': index + 1, 'context_turns': snapshot['context_turns'],
            'context_sources': snapshot['context_sources'],
            'request_hash': hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest(),
            'outbound_identity_canary_absent': all(v not in text for v in ('开发样本学生', 'eval.synthetic', 'student@example.invalid'))}
        row.pop('id', None)
        row.pop('result', None)
        return row


def run_multiturn(harness, corpus, digest, folder, *, resume=False):
    manifest_path = folder / 'manifest.json'
    plan = [(dialogue, index) for dialogue in corpus.dialogues for index in range(len(dialogue.turns))]
    if resume:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest['sources'] != sources() or manifest['corpus_sha256'] != digest or manifest['mode'] != harness.mode or manifest['prompt_version'] != harness.prompt_version:
            raise ValueError('resume requires identical source, corpus and mode')
    else:
        if manifest_path.exists():
            raise ValueError('run already exists')
        if harness.remaining() is not None and harness.remaining() < len(plan):
            raise ValueError('insufficient existing daily budget; wait for reset, do not recreate accounts')
        harness.initialize()
        manifest = {'run_id': uuid4().hex, 'mode': harness.mode, 'created_at': datetime.now(BUSINESS_TIMEZONE).isoformat(timespec='seconds'),
            'daily_call_limits': 'unlimited' if harness.unlimited else 'account30_site100',
            'corpus_version': corpus.version, 'corpus_sha256': digest, 'sources': sources(),
            'prompt_version': harness.prompt_version, 'configuration_id': harness.configuration(),
            'model': 'deepseek-flash' if harness.mode == 'live' else 'scripted', 'content_version': corpus.content_version}
        atomic_json(manifest_path, manifest)
    incomplete = any(not (folder / 'rows' / f'{dialogue.id}-{index+1}.json').exists() for dialogue, index in plan)
    if incomplete and harness.configuration() != manifest['configuration_id']:
        raise ValueError('configuration changed; cannot continue with different dialogue history')
    rows = []
    for dialogue, index in plan:
        path = folder / 'rows' / f'{dialogue.id}-{index+1}.json'
        if path.exists():
            row = json.loads(path.read_text(encoding='utf-8'))
        else:
            row = harness.evaluate_turn(dialogue, index, manifest['run_id'])
            atomic_json(path, row)
        rows.append(row)
        print(json.dumps({'case': row['case_id'], 'status': row['status'], 'context_turns': row['context_turns']}, ensure_ascii=False), flush=True)
    summary = summarize(rows)
    indexed = {row['case_id']: row for row in rows}
    controls = []
    for first, second in corpus.pairs():
        a, b = indexed[first], indexed[second]
        controls.append({'first': first, 'second': second,
            'pass': a['direction_match'] and b['direction_match'] and a['selected_focus'] != b['selected_focus']})
    summary['paired_context_discrimination'] = bool(controls) and all(c['pass'] for c in controls)
    summary['context_controls'] = controls
    summary['identity_canaries_absent'] = all(row['outbound_identity_canary_absent'] for row in rows)
    return {**manifest, 'rows': rows, 'summary': summary}


def markdown_report(report):
    s = report['summary']
    lines = ['# 概率多轮追问开发评测', '', f"时间：{report['created_at']}；模式：{report['mode']}；提示：{report['prompt_version']}。", '',
        '**自编开发样本，无教师金标；Fake 仅验证工程流程，不能证明真实模型理解、追问质量或教学效果。**', '',
        f"- {len({r['dialogue_id'] for r in report['rows']})} 组对话、{len(report['rows'])} 轮；两道开发练习。语料：{report['corpus_version']}。",
        '- 参考标签不发送模型；每组最多 4 次提示，历史只取已完成交流。失败仍占分母和额度。',
        '- 与单轮脚本共用固定隔离评测账号、数据库及调用账本；每日限额模式见运行清单，恢复不重复已执行调用。', '',
        '| 指标 | 结果 |', '| --- | --- |',
        f"| 预约 / 执行成功 | {s['reserved_calls']} / {s['contract_pass']} |",
        f"| 引用 / 方向权限检查通过 | {s['citation_pass']} / {s['policy_pass']} |",
        f"| 开发参考方向匹配 | {s['direction_match']} / {s['primary_cases']} |",
        f"| 相同本轮不同历史对照 | {'通过' if s['paired_context_discrimination'] else '未通过'} |",
        f"| 出站虚构身份标记全部移除 | {s['identity_canaries_absent']} |", '',
        '| 样本 | 上下文轮数 | 状态 | 参考方向 | 返回方向 |', '| --- | --- | --- | --- | --- |']
    lines.extend(f"| {r['case_id']} | {r['context_turns']} | {r['status']} | {', '.join(r['expected_focus'])} | {r['selected_focus']} |" for r in report['rows'])
    lines.extend(['', '## 待复核样本', ''])
    for row in report['rows']:
        if not row['direction_match']:
            safe = lambda value: str(value).replace('<', '&lt;').replace('>', '&gt;').replace('\n', ' ')
            lines.extend([f"### {row['case_id']}", '', f"- 自编输入：{safe(row['text'])}",
                f"- 开发依据：{safe(row['rationale'])}", f"- 返回引用：{safe(row['quote'])}",
                f"- 返回追问：{safe(row['question'])}；错误码：{safe(row['error_code'])}。", ''])
    lines.extend(['', 'Fake 固定选择 clarify，成对上下文辨别预期不通过；不能据 Fake 结果宣称多轮语义验收通过。真实模型须另行运行此冻结语料，保留结果和失败样本。', ''])
    return '\n'.join(lines)
