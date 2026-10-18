from copy import deepcopy
import hashlib
import json

import pytest
from sqlalchemy import func, select

from app.infrastructure.db.models import ModelOperation, ModelPolicy
from app.modules.errors import DomainError
from app.modules.governance import workbench
from tests.test_s02 import database, env


def test_reports_comparison_review_and_advice_do_not_publish_or_call(env):
    student, _ = env.student()
    path = '/api/v1/admin/evaluation-comparison?left_id=multiturn-v2-live&right_id=multiturn-v3-live'
    assert student.get(path).status_code == 403
    comparison = env.admin.get(path)
    assert comparison.status_code == 200, comparison.text
    body = comparison.json()
    assert (body['improved'], body['regressed'], body['unchanged']) == (3, 0, 15)
    reverse = env.admin.get(path.replace('left_id=multiturn-v2-live&right_id=multiturn-v3-live',
        'left_id=multiturn-v3-live&right_id=multiturn-v2-live')).json()
    assert reverse['regressed'] == 3
    for left, right in [('teach-fake', 'teach-live'), ('multiturn-v2-live', 'holdout-v3-live')]:
        assert env.admin.get(f'/api/v1/admin/evaluation-comparison?left_id={left}&right_id={right}').status_code == 409
    assert env.admin.get('/api/v1/admin/evaluation-comparison?left_id=teach-live&right_id=teach-live').status_code == 422
    detail = env.admin.get('/api/v1/admin/evaluation-reports/teach-live').json()
    assert detail['assessment']['state'] == 'blocked' and detail['assessment']['engineering_failures'] == 2
    assert detail['assessment']['pending_reviews'] == 18 and len(detail['human_review_rubric']) == 6
    assert all(row['case']['semantic_review_required'] for row in detail['cases'])
    row = next(row for row in detail['cases'] if row['target_id'] == 'teach-live:T04')
    assert env.admin.post('/api/v1/admin/development-reviews', json={'kind': 'evaluation',
        'target_id': row['target_id'], 'version': detail['report_sha256'], 'expected_revision': 0,
        'conclusion': 'runtime_issue', 'note': '输出被约束拒绝，无有效问题可复核；保留失败，不自动重试。'}).status_code == 200
    updated = env.admin.get('/api/v1/admin/evaluation-reports/teach-live').json()
    assert updated['assessment']['review_issues'] == 1 and updated['assessment']['pending_reviews'] == 17
    assert updated['assessment']['state'] == 'blocked'
    assert env.admin.get('/api/v1/admin/evaluation-reports/multiturn-v3-live').json()['assessment']['state'] == 'needs_review'
    roundtable = env.admin.get('/api/v1/admin/evaluation-reports/roundtable-live').json()
    assert roundtable['report_kind'] == 'roundtable' and roundtable['assessment']['state'] == 'blocked'
    assert roundtable['assessment']['reference_mismatches'] == 1
    assert roundtable['assessment']['pending_reviews'] == 6
    assert all(len(row['case']['voices']) == 3 for row in roundtable['cases'])
    planning = env.admin.get('/api/v1/admin/evaluation-reports/planning-live').json()
    assert planning['report_kind'] == 'planning' and planning['assessment']['state'] == 'blocked'
    assert planning['assessment']['engineering_failures'] == 1 and planning['assessment']['pending_reviews'] == 3
    assert planning['cases'][0]['case']['policy']['daily_minutes'] == 15
    assert planning['cases'][0]['case']['citations']
    with env.db.transaction() as session:
        assert session.get(ModelPolicy, 1).revision == 0
        assert session.scalar(select(func.count()).select_from(ModelOperation)) == 0


@pytest.mark.parametrize('key', ['corpus_sha256', 'content_version', 'model', 'mode', 'activity_version', 'report_kind'])
def test_comparison_rejects_changed_conditions(monkeypatch, key):
    original, _ = workbench.load('multiturn-v3-live')
    changed = deepcopy(original)
    changed[key] = 'different'
    monkeypatch.setattr(workbench, 'load', lambda name: (original if name == 'left' else changed, 'hash'))
    with pytest.raises(DomainError) as error:
        workbench.compare('left', 'right')
    assert error.value.code == 'comparison_incompatible'


@pytest.mark.parametrize('change', ['input', 'reference', 'duplicate', 'missing'])
def test_comparison_rejects_spoofed_hash_with_different_rows(monkeypatch, change):
    original, _ = workbench.load('multiturn-v3-live')
    changed = deepcopy(original)
    if change == 'input': changed['rows'][0]['text'] += '不同输入'
    elif change == 'reference': changed['rows'][0]['expected_focus'] = ['clarify']
    elif change == 'duplicate': changed['rows'].append(deepcopy(changed['rows'][0]))
    else: changed['rows'].pop()
    monkeypatch.setattr(workbench, 'load', lambda name: (original if name == 'left' else changed, 'hash'))
    with pytest.raises(DomainError):
        workbench.compare('left', 'right')


def test_report_integrity_and_registration(tmp_path, monkeypatch):
    for identifier in workbench.REPORTS:
        report, digest = workbench.load(identifier)
        assert digest == hashlib.sha256((workbench.ROOT / (identifier + '.json')).read_bytes()).hexdigest()
        assert report['mode'] in {'fake', 'live'}
    report = (workbench.ROOT / 'teach-live.json').read_bytes()
    (tmp_path / 'teach-live.json').write_bytes(report + b' ')
    (tmp_path / 'hashes.json').write_text(json.dumps({'teach-live': hashlib.sha256(report).hexdigest()}))
    monkeypatch.setattr(workbench, 'ROOT', tmp_path)
    with pytest.raises(DomainError) as error:
        workbench.load('teach-live')
    assert error.value.code == 'report_unavailable'
    with pytest.raises(DomainError):
        workbench.load('../teach-live')


def test_assessment_never_treats_automatic_pass_as_semantic_review():
    report, _ = workbench.load('multiturn-v3-live')
    assert workbench.assessment(report, {})['state'] == 'needs_review'
    reviewed = {row['case_id']: [{'conclusion': 'acceptable'}] for row in report['rows']}
    assert workbench.assessment(report, reviewed)['state'] == 'development_candidate'
    reviewed[report['rows'][0]['case_id']] = [{'conclusion': 'reference_ambiguous'}]
    assert workbench.assessment(report, reviewed)['state'] == 'blocked'
    fake = {**report, 'mode': 'fake'}
    assert workbench.assessment(fake, reviewed)['state'] == 'simulation_only'
