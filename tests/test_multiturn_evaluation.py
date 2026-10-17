import json

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.evaluation.multiturn import HOLDOUT_CORPUS, REGRESSION_CORPUS, MultiCorpus, MultiHarness, load_corpus, markdown_report, run_multiturn
from app.infrastructure.db.database import Database
from app.infrastructure.db.models import Account, ModelDailyBudget, ModelOperation
from app.infrastructure.runtime.model_tasks import day_key


def test_multiturn_corpus_validates_hint_cap_and_context_control():
    corpus, digest = load_corpus()
    assert len(corpus.dialogues) == 6 and sum(len(d.turns) for d in corpus.dialogues) == 18
    assert len(digest) == 64
    first, second = corpus.dialogues[2:4]
    assert first.task_id == second.task_id and first.initial_hints == second.initial_hints
    assert first.turns[-1].text == second.turns[-1].text
    assert set(first.turns[-1].expected_focus).isdisjoint(second.turns[-1].expected_focus)
    raw = corpus.model_dump(mode='json')
    raw['dialogues'][0]['initial_hints'] = 1
    with pytest.raises(ValidationError, match='hint cap'):
        MultiCorpus.model_validate(raw)


def test_candidate_fake_report_and_resume_rejects_different_prompt(tmp_path):
    from app.modules.learning.model_coaching import CANDIDATE_PROMPT_VERSION
    db = Database('sqlite:///' + (tmp_path / 'candidate.sqlite3').as_posix())
    db.migrate()
    try:
        corpus, digest = load_corpus(REGRESSION_CORPUS)
        assert sum(len(d.turns) for d in corpus.dialogues) == 12
        old, _ = load_corpus()
        holdout, _ = load_corpus(HOLDOUT_CORPUS)
        existing = {t.text for c in (old, holdout) for d in c.dialogues for t in d.turns}
        assert not existing.intersection(t.text for d in corpus.dialogues for t in d.turns)
        harness = MultiHarness(db, 'fake', unlimited=True, prompt_version=CANDIDATE_PROMPT_VERSION)
        report = run_multiturn(harness, corpus, digest, tmp_path / 'run')
        assert report['prompt_version'] == CANDIDATE_PROMPT_VERSION
        assert report['summary']['contract_pass'] == 12
        assert [r['context_turns'] for r in report['rows']] == [1, 2] * 6
        assert report['summary']['direction_match'] < 12  # Fake is still constant, not semantic evidence.
        with pytest.raises(ValueError, match='identical source'):
            run_multiturn(MultiHarness(db, 'fake', unlimited=True), corpus, digest, tmp_path / 'run', resume=True)
    finally:
        db.close()


def test_unlimited_evaluation_preserves_existing_ledger(tmp_path):
    db = Database('sqlite:///' + (tmp_path / 'unlimited.sqlite3').as_posix())
    db.migrate()
    try:
        harness = MultiHarness(db, 'fake', unlimited=True)
        harness.initialize()
        with db.transaction() as session:
            account = session.scalar(select(Account).where(Account.username == 'eval.synthetic'))
            session.add(ModelDailyBudget(day=day_key(), scope='global', calls=100))
            session.add(ModelDailyBudget(day=day_key(), scope='account:' + account.id, calls=30))
        corpus, digest = load_corpus()
        report = run_multiturn(harness, corpus, digest, tmp_path / 'run')
        assert report['daily_call_limits'] == 'unlimited'
        assert report['summary']['reserved_calls'] == report['summary']['contract_pass'] == 18
        assert harness.remaining() is None
        with db.transaction() as session:
            assert session.get(ModelDailyBudget, (day_key(), 'global')).calls == 118
            assert session.get(ModelDailyBudget, (day_key(), 'account:' + account.id)).calls == 48
    finally:
        db.close()


def test_multiturn_fake_resume_and_same_daily_budget(tmp_path):
    db = Database('sqlite:///' + (tmp_path / 'evaluation.sqlite3').as_posix())
    db.migrate()
    corpus, digest = load_corpus()
    folder = tmp_path / 'multi'
    try:
        report = run_multiturn(MultiHarness(db, 'fake'), corpus, digest, folder)
        summary = report['summary']
        assert summary['reserved_calls'] == summary['contract_pass'] == 18
        assert summary['identity_canaries_absent']
        assert not summary['paired_context_discrimination']
        assert summary['direction_match'] < 18  # Constant Fake must not masquerade as model quality.
        assert [r['context_turns'] for r in report['rows'][:4]] == [1, 2, 3, 4]
        assert [r['context_turns'] for r in report['rows'][4:8]] == [1, 2, 3, 4]
        assert '不能证明真实模型理解' in markdown_report(report)
        for row in report['rows']:
            with db.transaction() as session:
                op = session.get(ModelOperation, row['operation_id'])
                outbound = json.dumps(op.request['generation'], ensure_ascii=False)
                assert 'expected_focus' not in outbound and 'rationale' not in outbound
        # Derived row loss must be reconstructed from the exact ordinal operation, without another call.
        (folder / 'rows/M01-2.json').unlink()
        def no_call(config, snapshot):
            raise AssertionError('resume cannot issue another model call')
        restored = run_multiturn(MultiHarness(db, 'fake', no_call), corpus, digest, folder, resume=True)
        assert restored == report
        with db.transaction() as session:
            assert session.get(ModelDailyBudget, (day_key(), 'global')).calls == 18
            assert session.scalar(select(func.count()).select_from(Account)) == 2
            assert session.scalar(select(func.count()).select_from(ModelOperation)) == 18
        with pytest.raises(ValueError, match='daily budget'):
            run_multiturn(MultiHarness(db, 'fake'), corpus, digest, tmp_path / 'another')
        with pytest.raises(ValueError, match='identical source'):
            run_multiturn(MultiHarness(db, 'fake'), corpus, '0' * 64, folder, resume=True)
    finally:
        db.close()


def test_holdout_control_and_distinct_texts_are_frozen():
    baseline, _ = load_corpus()
    holdout, digest = load_corpus(HOLDOUT_CORPUS)
    assert len(holdout.dialogues) == 4 and sum(len(d.turns) for d in holdout.dialogues) == 10
    assert len(digest) == 64
    old = {t.text for d in baseline.dialogues for t in d.turns}
    assert not old.intersection(t.text for d in holdout.dialogues for t in d.turns)
    raw = holdout.model_dump(mode='json')
    raw['dialogues'][-1]['turns'][-1]['text'] = '不相同的本轮文字'
    with pytest.raises(ValidationError, match='isolate history'):
        MultiCorpus.model_validate(raw)


def test_holdout_fake_flow_retains_failures_and_context_controls(tmp_path):
    db = Database('sqlite:///' + (tmp_path / 'holdout.sqlite3').as_posix())
    db.migrate()
    try:
        corpus, digest = load_corpus(HOLDOUT_CORPUS)
        report = run_multiturn(MultiHarness(db, 'fake'), corpus, digest, tmp_path / 'report')
        assert report['summary']['contract_pass'] == 10
        assert report['summary']['context_controls'] == [{'first':'M09-2', 'second':'M10-2', 'pass':False}]
        assert '### M07-1' in markdown_report(report)
        assert [r['context_turns'] for r in report['rows'][:3]] == [1, 2, 3]
    finally:
        db.close()


def test_cli_check_only_does_not_publish_or_call(tmp_path, monkeypatch, capsys):
    import importlib.util
    from pathlib import Path
    from app.infrastructure.db.models import ModelPolicy
    spec = importlib.util.spec_from_file_location('check_multiturn_cli', Path('scripts/evaluate_probability_multiturn.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr('sys.argv', ['evaluate_probability_multiturn.py', '--check-only', '--corpus', 'holdout'])
    def forbidden(*args, **kwargs):
        raise AssertionError('preflight cannot execute or publish')
    monkeypatch.setattr(module, 'run_multiturn', forbidden)
    assert module.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result['calls_made'] == 0 and result['remaining'] is None and result['required'] == 10
    db = Database('sqlite:///' + (tmp_path / '.local/evaluation/probability-fake.sqlite3').as_posix())
    try:
        with db.transaction() as session:
            assert session.get(ModelPolicy, 1).revision == 0
            assert session.scalar(select(func.count()).select_from(ModelOperation)) == 0
            assert session.scalar(select(func.count()).select_from(Account)) == 0
    finally:
        db.close()
