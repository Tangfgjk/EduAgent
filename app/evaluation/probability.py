"""Versioned developer reference labels, deterministic scoring and evidence reports."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.learning.content_catalog import get_pack
from app.modules.learning.model_coaching import QUESTIONS, UNIT

Focus = Literal['clarify', 'sample_space', 'equal_likelihood', 'favorable', 'ratio']
DEFAULT_CORPUS = Path(__file__).resolve().parents[2] / 'seeds/evaluation/probability_followup.v1.json'


class Case(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str = Field(pattern=r'^[DB]\d{2}$')
    task_id: Literal['P-PRB-01', 'P-PRB-02']
    hints_before: int = Field(ge=0, le=3, strict=True)
    split: Literal['development', 'reserved']
    category: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=2, max_length=1500)
    expected_focus: tuple[Focus, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode='after')
    def feasible_reference(self):
        allowed = {'clarify', 'sample_space', 'equal_likelihood'}
        if self.hints_before >= 1:
            allowed.add('favorable')
        if self.hints_before >= 2:
            allowed.add('ratio')
        if not set(self.expected_focus) <= allowed:
            raise ValueError('reference direction exceeds hint permission')
        return self


class Corpus(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    version: Literal['probability.followup.eval.v1']
    source: Literal['self_authored_development']
    professional_review: Literal[False]
    unit_id: Literal['math.g9.probability.dev1']
    content_version: str = Field(pattern=r'^[a-f0-9]{64}$')
    repeat_ids: tuple[str, ...] = Field(max_length=6)
    cases: tuple[Case, ...] = Field(min_length=1, max_length=24)

    @model_validator(mode='after')
    def unique_cases_and_version(self):
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)) or len(self.repeat_ids) != len(set(self.repeat_ids)) or not set(self.repeat_ids) <= set(ids):
            raise ValueError('duplicate or missing case ID')
        if self.content_version != get_pack(UNIT).version:
            raise ValueError('content version changed; create a new corpus version')
        return self


def load_corpus(path=DEFAULT_CORPUS):
    raw = Path(path).read_bytes()
    return Corpus.model_validate_json(raw), hashlib.sha256(raw).hexdigest()


def score(case: Case, operation: dict, snapshot: dict):
    result = operation.get('result') or {}
    completed = operation['status'] == 'succeeded'
    quote, focus = result.get('quote', ''), result.get('focus')
    citation = completed and bool(quote) and quote in snapshot['student_text']
    policy = completed and focus in snapshot['allowed_focus'] and result.get('question') == QUESTIONS.get(focus)
    # A developer label match is not a teacher judgment or evidence of teaching effectiveness.
    direction = completed and citation and policy and focus in case.expected_focus
    substantive_quote = citation and len(quote.strip()) >= min(6, len(snapshot['student_text'].strip()))
    return {'contract_pass': completed, 'citation_pass': citation, 'policy_pass': policy,
            'direction_match': direction, 'substantive_quote': substantive_quote,
            'selected_focus': focus, 'quote': quote, 'question': result.get('question')}


def summarize(rows):
    primary = [row for row in rows if not row.get('repeat_of')]
    originals = {row['case_id']: row for row in primary}
    repeats = [row for row in rows if row.get('repeat_of')]
    pairs = [(originals[row['repeat_of']], row) for row in repeats if row['repeat_of'] in originals]
    complete_pairs = [(first, second) for first, second in pairs if first['contract_pass'] and second['contract_pass']]
    groups = {}
    for row in primary:
        group = groups.setdefault(row['category'], {'cases': 0, 'direction_matches': 0})
        group['cases'] += 1
        group['direction_matches'] += bool(row['direction_match'])
    durations = [row['elapsed_ms'] for row in rows if row.get('elapsed_ms') is not None]
    return {
        'primary_cases': len(primary), 'total_attempts': len(rows), 'repeat_attempts': len(repeats),
        'reserved_calls': sum(bool(row.get('operation_id')) for row in rows),
        **{name: sum(bool(row[name]) for row in primary) for name in
           ('contract_pass', 'citation_pass', 'policy_pass', 'direction_match', 'substantive_quote')},
        'primary_failures': [row['case_id'] for row in primary if not row['direction_match']],
        'status_counts_all_attempts': dict(Counter(row['status'] for row in rows)),
        'repeat_pairs': len(pairs), 'repeat_complete_pairs': len(complete_pairs),
        'repeat_same_focus': sum(a['selected_focus'] == b['selected_focus'] for a, b in complete_pairs),
        'repeat_both_direction_match': sum(a['direction_match'] and b['direction_match'] for a, b in pairs),
        'input_tokens_known': sum((row.get('usage') or {}).get('input_tokens') or 0 for row in rows),
        'output_tokens_known': sum((row.get('usage') or {}).get('output_tokens') or 0 for row in rows),
        'missing_usage_attempts': sum(not row.get('usage') or any(row['usage'].get(k) is None for k in ('input_tokens', 'output_tokens')) for row in rows),
        'latency_median_ms': statistics.median(durations) if durations else None,
        'latency_max_ms': max(durations) if durations else None,
        'categories': groups,
        'splits': {split: {'cases': sum(row['split'] == split for row in primary),
                           'direction_matches': sum(row['split'] == split and row['direction_match'] for row in primary)}
                   for split in ('development', 'reserved')},
    }


def markdown_report(report):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ').replace('<', '&lt;').replace('>', '&gt;').replace('`', '\\`')
    summary = report['summary']
    count = summary['primary_cases']
    lines = ['# 概率追问开发基线评测', '',
        f"执行时间：{report['created_at']}。模式：{report['mode']}；数据：自编开发样本；未经专业教师审核。", '',
        '**本报告测量当前受限追问流程，不是学习掌握、素养或教学效果评价。**', '',
        ('Fake 模式仅验证评测流水线，不能将方向匹配数据当作 DeepSeek 的表现。' if report['mode'] == 'fake' else '本轮使用真实 DeepSeek；参考标签为开发者自编，不是教师金标。'), '',
        '## 固定条件', '',
        f"- 样本版本：`{report['corpus_version']}`；SHA-256：`{report['corpus_sha256']}`。",
        f"- 提示版本：`{report['prompt_version']}`；题库版本：`{report['content_version']}`。",
        f"- 模型：`{report['model']}`；温度 0.2、关闭思考模式、输出上限 512 tokens、无自动重试。",
        '- 使用隔离数据库与同一个模拟来源账号，复用教学图、提示门禁、模型任务、额度和结果校验。未读取现有学生记录。',
        '- 每条新建独立临时空间；前置步骤使用题库参考答案以到达指定练习，不能当作学习表现。',
        '- 参考方向和解释未发送给模型；首轮评测后未据此修改生产提示。reserved 为本轮预留组，不是教师盲测集。', '',
        '## 汇总', '', '| 指标 | 结果 |', '| --- | --- |',
        f"| 主样本 / 执行条目 / 调用预约 | {count} / {summary['total_attempts']} / {summary['reserved_calls']} |",
        *[f"| {label} | {summary[key]} / {count} |" for key, label in [
            ('contract_pass', '运行成功并通过结构校验'), ('citation_pass', '原文连续引用检查'),
            ('policy_pass', '允许方向与固定追问一致'), ('direction_match', '符合开发参考方向'),
            ('substantive_quote', '引用长度检查（仅辅助指标）')]],
        f"| 复测可比较配对 / 全部配对 | {summary['repeat_complete_pairs']} / {summary['repeat_pairs']} |",
        f"| 复测方向完全一致 | {summary['repeat_same_focus']} / {summary['repeat_pairs']} |",
        f"| 配对两次都符合开发参考 | {summary['repeat_both_direction_match']} / {summary['repeat_pairs']} |",
        f"| 已知输入 / 输出 token | {summary['input_tokens_known']} / {summary['output_tokens_known']} |",
        f"| 用量不完整的预约 | {summary['missing_usage_attempts']} |",
        f"| 请求耗时中位数 / 最大值 | {summary['latency_median_ms']} / {summary['latency_max_ms']} ms |", '',
        '失败、超时和未完成的样本保留在分母中。相同方向不是唯一合适方向，复测不同也不自动等于教学错误。长度检查不能证明引用与结论之间存在充分语义依据。', '',
        '## 分类结果', '', '| 类别 | 主样本 | 开发参考方向匹配 |', '| --- | --- | --- |',
        *[f"| {category} | {values['cases']} | {values['direction_matches']} |" for category, values in summary['categories'].items()], '',
        '## 逐条记录', '', '| 样本 | 分组 | 提示已用 | 状态 | 参考方向 | 返回方向 | 开发匹配 |', '| --- | --- | --- | --- | --- | --- | --- |',
        *[f"| {cell(row['attempt_id'])} | {row['split']} | {row['hints_before']} | {cell(row['status'])} | {', '.join(row['expected_focus'])} | {cell(row['selected_focus'])} | {'是' if row['direction_match'] else '否'} |" for row in report['rows']], '',
        '## 失败与待复核样本', '']
    failures = [row for row in report['rows'] if not row['direction_match']]
    if not failures:
        lines.append('本轮没有发现不符合开发参考的返回；样本较少且未经教师审核，不能据此宣称没有风险。')
    for row in failures:
        lines.extend([f"### {cell(row['attempt_id'])}", '', f"- 自编输入：{cell(row['text'])}",
            f"- 开发参考：{', '.join(row['expected_focus'])}。{cell(row['rationale'])}",
            f"- 实际返回：{cell(row['selected_focus'])}；错误码：{cell(row['error_code'])}。",
            f"- 已通过校验的引用：{cell(row['quote'])}",
            f"- 实际追问：{cell(row['question'])}", ''])
    lines.extend(['', '## 使用限制与下一步', '',
        f"- 本轮仅有两道自编练习、{count} 个主样本和少量复测；未覆盖真实学生分布、多轮长期学习、所有数学知识点或服务商负载变化。",
        '- 当前语句仍来自受限支架；格式、引用和白名单通过不代表模型已准确理解全文，也不证明追问能促进学习。',
        '- 先由数学教学审核人员复核样本、开发参考及失败案例，再扩充改写样本和对照验证；保留基线，不用同一批结果反复调到满分后宣称泛化。',
        '- 不自动更新 Skill.md、模型配置、提示版本或开放更多学生数据。放宽自由生成与多轮能力前另做规则和教学质量验证。', ''])
    return '\n'.join(lines)
