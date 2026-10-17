from types import SimpleNamespace
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.evaluation.probability import Corpus, load_corpus, markdown_report, score, summarize
from app.evaluation.runner import EvaluationHarness, run_evaluation
from app.infrastructure.db.database import Database
from app.infrastructure.db.models import Account, ModelDailyBudget, ModelOperation
from app.infrastructure.runtime.model_tasks import ModelTaskRunner, day_key
from app.modules.learning.content_catalog import get_pack
from app.modules.learning.model_coaching import freeze_request, QUESTIONS, UNIT
from tests.test_s02 import database, env


def snapshot(case):
    pack = get_pack(UNIT)
    index = next(i for i, t in enumerate(pack.tasks) if t['id'] == case.task_id)
    view = {'task': pack.task_public(index), 'hints': pack.tasks[index]['hints'][:case.hints_before], 'content_version': pack.version}
    account = SimpleNamespace(display_name='开发样本学生', username='eval.synthetic', id='eval-account-id', learner_id='eval-learner-id')
    return freeze_request(view, case.text, account, 512)


def test_corpus_coverage_feasibility_and_no_reference_leakage():
    corpus, digest = load_corpus()
    assert len(corpus.cases) == 24 and len(corpus.repeat_ids) == 6 and len(digest) == 64
    assert {case.hints_before for case in corpus.cases} == {0, 1, 2, 3}
    assert sum(case.split == 'reserved' for case in corpus.cases) == 6
    assert len({case.category for case in corpus.cases}) >= 8
    for case in corpus.cases:
        frozen = snapshot(case)
        assert set(case.expected_focus) <= set(frozen['allowed_focus'])
        outbound = json.dumps(frozen['generation'], ensure_ascii=False)
        assert 'expected_focus' not in outbound and 'rationale' not in outbound
        assert all(value not in outbound for value in ('开发样本学生', 'eval.synthetic', 'student@example.invalid'))


@pytest.mark.parametrize('change', ['duplicate', 'invalid_reference', 'version', 'unknown_repeat'])
def test_invalid_corpus_fails_before_any_model_call(change):
    raw = load_corpus()[0].model_dump(mode='json')
    if change == 'duplicate':
        raw['cases'][1]['id'] = raw['cases'][0]['id']
    elif change == 'invalid_reference':
        raw['cases'][0]['expected_focus'] = ['ratio']
    elif change == 'version':
        raw['content_version'] = '0' * 64
    else:
        raw['repeat_ids'] = ['X00']
    with pytest.raises(ValidationError):
        Corpus.model_validate(raw)


def test_independent_scoring_does_not_accept_fabricated_quote_or_question():
    case = load_corpus()[0].cases[0]
    frozen = snapshot(case)
    op = {'status': 'succeeded', 'result': {'quote': '虚构的学生原文', 'focus': 'sample_space', 'question': QUESTIONS['sample_space']}}
    assert not score(case, op, frozen)['direction_match']
    op['result']['quote'] = case.text
    op['result']['question'] = '完整答案'
    assert not score(case, op, frozen)['policy_pass']
    op['result']['question'] = QUESTIONS['sample_space']
    assert score(case, op, frozen)['direction_match']


def test_summary_keeps_failures_and_unknown_usage_in_denominator():
    def row(identifier, passed, **changes):
        return {'case_id': identifier, 'split': 'development', 'category': 'counting', 'contract_pass': passed,
            'citation_pass': passed, 'policy_pass': passed, 'direction_match': passed, 'substantive_quote': passed,
            'selected_focus': 'sample_space' if passed else None, 'status': 'succeeded' if passed else 'unknown',
            'usage': None, 'elapsed_ms': None, **changes}
    rows = [row('D01', True), row('D02', False), row('D01', False, repeat_of='D01')]
    report = summarize(rows)
    assert report['primary_cases'] == 2 and report['direction_match'] == 1
    assert report['primary_failures'] == ['D02'] and report['missing_usage_attempts'] == 3
    assert report['repeat_pairs'] == 1 and report['repeat_complete_pairs'] == 0


def test_full_fake_flow_resume_and_budget_cannot_reset(tmp_path):
    db = Database('sqlite:///' + (tmp_path / 'evaluation.sqlite3').as_posix())
    db.migrate()
    corpus, digest = load_corpus()
    folder = tmp_path / 'run'
    try:
        report = run_evaluation(EvaluationHarness(db, 'fake'), corpus, digest, folder)
        assert report['summary']['reserved_calls'] == 30 and report['summary']['contract_pass'] == 24
        assert report['summary']['direction_match'] < 24  # Constant clarify cannot masquerade as quality success.
        assert all(row['outbound_identity_canary_absent'] for row in report['rows'])
        assert 'Fake 模式仅验证评测流水线' in markdown_report(report)
        class NoCallRunner:
            def step(self, **kwargs):
                raise AssertionError('resume must not call model again')
        resumed_harness = EvaluationHarness(db, 'fake')
        resumed_harness.model_runner = NoCallRunner()
        resumed = run_evaluation(resumed_harness, corpus, digest, folder, resume=True)
        assert resumed['summary'] == report['summary']
        exhausted = run_evaluation(EvaluationHarness(db, 'fake'), corpus, digest, tmp_path / 'new-run', max_calls=1)
        assert exhausted['rows'][0]['status'] == 'blocked'
        assert exhausted['rows'][0]['error_code'] == 'model_budget_exhausted'
        with db.transaction() as session:
            assert session.get(ModelDailyBudget, (day_key(), 'global')).calls == 30
            assert session.scalar(select(func.count()).select_from(Account)) == 2
    finally:
        db.close()


def test_selected_operation_does_not_drain_another_task(env):
    from tests.test_model_runtime import setup, ready
    client, path, _ = setup(env)
    first = ready(env, client, path)['coaching']['operation_id']
    second = ready(env, client, path, key='second')['coaching']['operation_id']
    assert ModelTaskRunner(env.db).step(operation_id=second)
    with env.db.transaction() as session:
        assert session.get(ModelOperation, first).status == 'pending'
        assert session.get(ModelOperation, second).status == 'succeeded'


def test_resume_reconstructs_saved_operation_without_reissuing(tmp_path):
    db = Database('sqlite:///' + (tmp_path / 'resume.sqlite3').as_posix())
    db.migrate()
    corpus, digest = load_corpus()
    folder = tmp_path / 'run'
    try:
        original = run_evaluation(EvaluationHarness(db, 'fake'), corpus, digest, folder, max_calls=1)
        # Simulate losing the derived report row after the model result was already committed.
        (folder / 'rows/D01.json').unlink()
        def unexpected_call(config, request):
            raise AssertionError('a committed operation must not issue a second request')
        restored = run_evaluation(EvaluationHarness(db, 'fake', unexpected_call), corpus, digest, folder, resume=True)
        assert restored['summary'] == original['summary']
        with pytest.raises(ValueError, match='identical'):
            run_evaluation(EvaluationHarness(db, 'fake'), corpus, '0' * 64, folder, resume=True)
        with db.transaction() as session:
            assert session.get(ModelDailyBudget, (day_key(), 'global')).calls == 1
            assert session.scalar(select(func.count()).select_from(ModelOperation)) == 1
    finally:
        db.close()
