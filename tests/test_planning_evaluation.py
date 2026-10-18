from sqlalchemy import func, select

from app.evaluation.planning import run, PlanningHarness
from app.infrastructure.db.database import Database
from app.infrastructure.db.models import ModelOperation, ModelDailyBudget
from app.infrastructure.runtime.model_tasks import day_key


def test_planning_recovery_keeps_calls_and_requires_quoted_sources(tmp_path):
    db = Database('sqlite:///' + (tmp_path / 'evaluation.sqlite3').as_posix())
    db.migrate()
    try:
        folder = tmp_path / 'run'
        report = run(PlanningHarness(db, 'fake'), folder)
        assert report['summary']['valid_results'] == 3 and report['summary']['flows_completed'] == 3
        assert report['summary']['identity_canaries_absent']
        assert any(c['source_id'].startswith('fact:') for c in report['rows'][1]['citations'])
        (folder / 'rows/PL02.json').unlink()
        def forbidden(*args): raise AssertionError('completed evaluation repeated a model call')
        restored = run(PlanningHarness(db, 'fake', forbidden), folder, resume=True)
        assert restored == report
        with db.transaction() as session:
            assert session.scalar(select(func.count()).select_from(ModelOperation)) == 3
            assert session.get(ModelDailyBudget, (day_key(), 'global')).calls == 3
    finally:
        db.close()
