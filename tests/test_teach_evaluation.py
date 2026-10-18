import json

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.evaluation.teach import TeachHarness, TeachCorpus, load_corpus, run_evaluation, score
from app.infrastructure.db.database import Database
from app.infrastructure.db.models import ModelOperation, ModelDailyBudget
from app.infrastructure.runtime.model_tasks import day_key
from app.llm.provider import FakeProvider, ProviderError


def test_corpus_frozen_and_references_never_sent_to_model(tmp_path):
    corpus, digest = load_corpus()
    assert len(corpus.cases) == 12 and len(corpus.repeat_ids) == 6 and len(digest) == 64
    for update in ({'activity_version': '0' * 64}, {'repeat_ids': ['T99']}, {'cases': [corpus.cases[0]] * 2}):
        with pytest.raises(ValidationError):
            TeachCorpus.model_validate({**corpus.model_dump(), **update})
    db = Database('sqlite:///' + (tmp_path / 'evaluation.sqlite3').as_posix())
    db.migrate()
    try:
        def provider(config, snapshot):
            payload = json.loads(snapshot['generation']['messages'][1]['content'])
            assert set(payload) == {'task', 'student_text', 'candidate_answer'}
            assert 'expected_focus' not in payload and 'rationale' not in payload
            serialized = json.dumps(payload, ensure_ascii=False)
            assert all(x not in serialized for x in ('开发样本学生', 'eval.synthetic', '13912345678', 'student@example.invalid'))
            return FakeProvider((json.dumps({'quote': snapshot['student_text'][:80], 'focus': 'clarify',
                'question': '你能具体说明这一步的依据是什么吗？'}, ensure_ascii=False),))
        harness = TeachHarness(db, 'fake', provider)
        folder = tmp_path / 'run'
        report = run_evaluation(harness, corpus, digest, folder)
        assert report['summary']['contract_pass'] == 12 and report['summary']['total_attempts'] == 18
        assert report['summary']['direction_match'] < 12
        assert report['summary']['repeat_pairs'] == 6 and report['summary']['repeat_same_focus'] == 6
        assert report['summary']['identity_canaries_absent']
        assert all(row['semantic_review_required'] for row in report['rows'])
        (folder / 'rows/T10-R1.json').unlink()  # Recover derived output without another reservation.
        def forbidden(*args): raise AssertionError('resume issued another model call')
        resumed = run_evaluation(TeachHarness(db, 'fake', forbidden), corpus, digest, folder, resume=True)
        assert report == resumed
        with db.transaction() as session:
            assert session.scalar(select(func.count()).select_from(ModelOperation)) == 18
            assert session.get(ModelDailyBudget, (day_key(), 'global')).calls == 18
        with pytest.raises(ValueError, match='identical source'):
            run_evaluation(TeachHarness(db, 'fake'), corpus, '0' * 64, folder, resume=True)
    finally:
        db.close()


def test_failed_call_is_preserved_not_retried(tmp_path):
    corpus, digest = load_corpus()
    small = TeachCorpus.model_validate({**corpus.model_dump(), 'cases': corpus.cases[:1], 'repeat_ids': []})
    db = Database('sqlite:///' + (tmp_path / 'fail.sqlite3').as_posix())
    db.migrate()
    try:
        harness = TeachHarness(db, 'fake', lambda c, s: FakeProvider((ProviderError('timeout'),)))
        report = run_evaluation(harness, small, digest, tmp_path / 'run')
        assert report['summary']['total_attempts'] == report['summary']['reserved_calls'] == 1
        assert report['rows'][0]['status'] == 'unknown' and not report['rows'][0]['direction_match']
        assert report['summary']['missing_usage_attempts'] == 1
    finally:
        db.close()


def test_scoring_cannot_accept_a_fabricated_quote():
    case = load_corpus()[0].cases[0]
    op = {'status': 'succeeded', 'result': {'quote': '伪造原话', 'focus': 'ratio', 'question': '为什么这样安排分子分母？'}}
    result = score(case, op, {'student_text': case.text, 'allowed_focus': ['ratio']})
    assert not result['citation_pass'] and not result['policy_pass'] and not result['direction_match']
