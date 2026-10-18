"""Explicit packaged development reports, never arbitrary paths or student databases."""
import hashlib
import json
from pathlib import Path

from app.modules.errors import DomainError
from app.modules.governance.reviews import records

REPORTS = {'single-baseline': '单轮 DeepSeek 基线', 'multiturn-fake': '多轮 Fake 工程流程', 'holdout-fake': '独立样本 Fake 工程流程',
    'multiturn-v2-live': '多轮 v2 · 真实基线', 'holdout-v2-live': '原独立样本 v2 · 真实回归',
    'multiturn-v3-live': '多轮 v3 · 真实基线', 'holdout-v3-live': '原独立样本 v3 · 已见回归',
    'regression-v3-live': '新增样本 v3 · 真实回归', 'regression-v2-live': '新增样本 v2 · 真实对照',
    'teach-fake': '我教 AI · Fake 工程评测', 'teach-live': '我教 AI · DeepSeek 专项评测',
    'teach-multiturn-fake': '我教 AI 多轮 · Fake 完整流程', 'teach-multiturn-live': '我教 AI 多轮 · DeepSeek 完整流程',
    'roundtable-fake': '圆桌三视角 · Fake 完整流程', 'roundtable-live': '圆桌三视角 · DeepSeek 专项评测',
    'planning-fake': '有来源规划 · Fake 工程评测', 'planning-live': '有来源规划 · DeepSeek 专项评测'}
ROOT = Path(__file__).resolve().parents[3] / 'seeds/evaluation/reports'


def load(identifier):
    if identifier not in REPORTS:
        raise DomainError(404, 'not_found', '未找到此冻结评测报告')
    path = ROOT / (identifier + '.json')
    try:
        raw = path.read_bytes()
        hashes = json.loads((ROOT / 'hashes.json').read_text(encoding='utf-8'))
        if hashlib.sha256(raw).hexdigest() != hashes.get(identifier):
            raise ValueError('report integrity mismatch')
        report = json.loads(raw)
    except (OSError, ValueError):
        raise DomainError(503, 'report_unavailable', '冻结报告暂不可用，请检查部署文件') from None
    if identifier.startswith('planning-'):
        report = planning_projection(report)
    return report, hashlib.sha256(raw).hexdigest()


def planning_projection(report):
    """Read adapter only; digest/reviews continue to bind the unchanged original report bytes."""
    from app.evaluation.probability import summarize
    rows = []
    for row in report['rows']:
        passed = row['contract_and_citations_pass']
        rows.append({**row, 'attempt_id': row['case_id'], 'text': row['sources']['experience'],
            'source_excerpts': row['sources'], 'category': 'planning', 'split': 'development', 'repeat_of': None,
            'expected_focus': ['source_contract'], 'selected_focus': 'source_contract' if passed else None,
            'rationale': '仅核对受限参数与来源引用规则，不评判时长选择、建议适切性或教学效果。',
            'contract_pass': row['status'] == 'succeeded', 'citation_pass': passed, 'policy_pass': passed,
            'direction_match': passed, 'substantive_quote': passed,
            'quote': row['citations'][0]['quote'] if row['citations'] else '', 'question': None,
            'context_turns': 1, 'semantic_review_required': True, 'outbound_identity_canary_absent': row['identity_canaries_absent']})
    return {**report, 'report_kind': 'planning', 'model': 'deepseek-flash' if report['mode'] == 'live' else 'scripted',
        'content_version': 'planning.sources.v1', 'rows': rows, 'summary': summarize(rows),
        'human_review_rubric': ['建议是否有来源且适合当前学习体验', '是否区分自述、机器事实与课程资料声明',
            '是否把局部片段或全局计数夸大成能力诊断', '是否正确处理资料指令注入且不代替审核',
            '时长及反思重点的理由是否清楚、可质疑', '是否保留学生自主确认和无教学效果结论的边界']}


def assessment(report, reviewed):
    rows = report['rows']
    engineering = sum(not (r['contract_pass'] and r['citation_pass'] and r['policy_pass'])
        or r.get('outbound_identity_canary_absent') is False for r in rows)
    mismatch = sum(not r['direction_match'] for r in rows)
    pending = sum(not reviewed.get(r.get('attempt_id') or r['case_id']) for r in rows)
    issues = sum(bool(history and history[0]['conclusion'] != 'acceptable') for history in reviewed.values())
    state = ('simulation_only' if report['mode'] == 'fake' else 'blocked' if engineering or mismatch or issues
        else 'needs_review' if pending else 'development_candidate')
    return {'state': state, 'engineering_failures': engineering, 'reference_mismatches': mismatch,
        'pending_reviews': pending, 'review_issues': issues, 'automatic_publish': False,
        'notice': '报告准入建议，不自动发布或修改当前模型开关；开发复核不替代教师审核或教学效果验证。'}


def review_map(session, identifier, report, digest):
    return {(r.get('attempt_id') or r['case_id']): records(session, 'evaluation',
        identifier + ':' + (r.get('attempt_id') or r['case_id']), digest) for r in report['rows']}


def index(session):
    items = []
    for identifier, title in REPORTS.items():
        report, digest = load(identifier)
        rows = report['rows']
        reviewed = review_map(session, identifier, report, digest)
        items.append({'report_id': identifier, 'title': title, 'report_sha256': digest,
            'mode': report['mode'], 'prompt_version': report['prompt_version'], 'created_at': report['created_at'],
            'cases': len(rows), 'needs_review': sum(not r['direction_match'] for r in rows),
            'reviewed_cases': sum(bool(history) for history in reviewed.values()),
            'report_kind': report.get('report_kind', 'tutoring'), 'corpus_sha256': report['corpus_sha256'],
            'model': report['model'], 'assessment': assessment(report, reviewed)})
    return {'items': items, 'notice': '开发参考未经教师金标审核；Fake 只验证工程流程，不能证明真实模型质量。复核不自动修改提示或发布配置。'}


def case_target(target):
    identifier, _, case_id = target.partition(':')
    report, digest = load(identifier)
    row = next((r for r in report['rows'] if (r.get('attempt_id') or r['case_id']) == case_id), None)
    if row is None:
        raise DomainError(404, 'not_found', '未找到此评测样本')
    return {'report_sha256': digest, 'case': row}


def detail(session, identifier):
    report, digest = load(identifier)
    keys = ('text', 'rationale', 'expected_focus', 'selected_focus', 'status', 'quote', 'question',
            'direction_match', 'contract_pass', 'citation_pass', 'policy_pass', 'elapsed_ms', 'usage', 'context_turns', 'error_code',
            'category', 'answer', 'repeat_of', 'semantic_review_required', 'outbound_identity_canary_absent')
    rows = []
    for row in report['rows']:
        target = identifier + ':' + (row.get('attempt_id') or row['case_id'])
        reviews = records(session, 'evaluation', target, digest)
        case = {key: row.get(key) for key in keys}
        case['semantic_review_required'] = row.get('semantic_review_required', False)
        case['voices'] = row.get('voices', [])
        case['policy'] = row.get('policy')
        case['citations'] = row.get('citations', [])
        case['source_excerpts'] = row.get('source_excerpts', {})
        rows.append({'target_id': target, 'revision': reviews[0]['revision'] if reviews else 0, 'reviews': reviews, 'case': case})
    return {'report_id': identifier, 'title': REPORTS[identifier], 'report_sha256': digest,
        'mode': report['mode'], 'prompt_version': report['prompt_version'], 'cases': rows,
        'summary': report['summary'], 'report_kind': report.get('report_kind', 'tutoring'),
        'human_review_rubric': report.get('human_review_rubric', []),
        'assessment': assessment(report, review_map(session, identifier, report, digest))}


def compare(left_id, right_id):
    if left_id == right_id:
        raise DomainError(422, 'comparison_same_report', '请选择两份不同报告')
    left, left_hash = load(left_id)
    right, right_hash = load(right_id)
    checks = ('corpus_sha256', 'content_version', 'mode', 'model', 'activity_version')
    if (any(left.get(k) != right.get(k) for k in checks)
            or left.get('report_kind', 'tutoring') != right.get('report_kind', 'tutoring')):
        raise DomainError(409, 'comparison_incompatible', '仅可比较同语料、内容版本、型号、模式与路线的报告')

    def indexed(report):
        rows = {r.get('attempt_id') or r['case_id']: r for r in report['rows']}
        if len(rows) != len(report['rows']):
            raise DomainError(409, 'comparison_incompatible', '报告样本标识重复')
        return rows

    a, b = indexed(left), indexed(right)
    if set(a) != set(b):
        raise DomainError(409, 'comparison_incompatible', '样本集合不一致')
    rows = []
    for identifier, first in a.items():
        second = b[identifier]
        if any(first.get(k) != second.get(k) for k in ('text', 'answer', 'task_id', 'hints_before', 'expected_focus', 'rationale', 'repeat_of')):
            raise DomainError(409, 'comparison_incompatible', '输入或开发参考不一致，不能直接比较')
        rows.append({'case_id': identifier, 'left_match': first['direction_match'], 'right_match': second['direction_match'],
            'left_focus': first['selected_focus'], 'right_focus': second['selected_focus'],
            'left_status': first['status'], 'right_status': second['status'],
            'left_question': first.get('question'), 'right_question': second.get('question')})
    return {'left_id': left_id, 'right_id': right_id, 'left_sha256': left_hash, 'right_sha256': right_hash,
        'left_prompt': left['prompt_version'], 'right_prompt': right['prompt_version'],
        'cases': rows, 'improved': sum(not r['left_match'] and r['right_match'] for r in rows),
        'regressed': sum(r['left_match'] and not r['right_match'] for r in rows),
        'unchanged': sum(r['left_match'] == r['right_match'] for r in rows),
        'left_summary': left['summary'], 'right_summary': right['summary'],
        'notice': '相同开发样本的单次运行对比，不证明因果、泛化或教学效果；重复条目按独立尝试保留。'}
