"""Explicit packaged development reports, never arbitrary paths or student databases."""
import hashlib
import json
from pathlib import Path

from app.modules.errors import DomainError
from app.modules.governance.reviews import records

REPORTS = {'single-baseline': '单轮 DeepSeek 基线', 'multiturn-fake': '多轮 Fake 工程流程', 'holdout-fake': '独立样本 Fake 工程流程'}
ROOT = Path(__file__).resolve().parents[3] / 'seeds/evaluation/reports'


def load(identifier):
    if identifier not in REPORTS:
        raise DomainError(404, 'not_found', '未找到此冻结评测报告')
    path = ROOT / (identifier + '.json')
    try:
        raw = path.read_bytes()
        report = json.loads(raw)
    except (OSError, ValueError):
        raise DomainError(503, 'report_unavailable', '冻结报告暂不可用，请检查部署文件') from None
    return report, hashlib.sha256(raw).hexdigest()


def index(session):
    items = []
    for identifier, title in REPORTS.items():
        report, digest = load(identifier)
        rows = report['rows']
        items.append({'report_id': identifier, 'title': title, 'report_sha256': digest,
            'mode': report['mode'], 'prompt_version': report['prompt_version'], 'created_at': report['created_at'],
            'cases': len(rows), 'needs_review': sum(not r['direction_match'] for r in rows),
            'reviewed_cases': sum(bool(records(session, 'evaluation', identifier + ':' + (r.get('attempt_id') or r['case_id']), digest)) for r in rows)})
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
            'direction_match', 'contract_pass', 'citation_pass', 'policy_pass', 'elapsed_ms', 'usage', 'context_turns', 'error_code')
    rows = []
    for row in report['rows']:
        target = identifier + ':' + (row.get('attempt_id') or row['case_id'])
        reviews = records(session, 'evaluation', target, digest)
        rows.append({'target_id': target, 'revision': reviews[0]['revision'] if reviews else 0,
            'reviews': reviews, 'case': {key: row.get(key) for key in keys}})
    return {'report_id': identifier, 'title': REPORTS[identifier], 'report_sha256': digest,
        'mode': report['mode'], 'prompt_version': report['prompt_version'], 'cases': rows,
        'summary': report['summary']}
