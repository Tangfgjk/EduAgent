from sqlalchemy import func, select

from app.evaluation.roundtable import run, RoundtableHarness
from app.infrastructure.db.database import Database
from app.infrastructure.db.models import ModelOperation, ModelDailyBudget
from app.infrastructure.runtime.model_tasks import day_key


def test_roundtable_frozen_flows_and_recovery_without_duplicate_calls(tmp_path):
    db = Database('sqlite:///' + (tmp_path / 'eval.sqlite3').as_posix())
    db.migrate()
    try:
        folder = tmp_path / 'run'
        report = run(RoundtableHarness(db, 'fake'), folder)
        assert len(report['rows']) == 6 and report['summary']['contract_pass'] == 6
        assert report['summary']['flows_completed'] == 6 and report['summary']['identity_canaries_absent']
        assert all(len(r['voices']) == 3 and r['verification_check']['independent'] for r in report['rows'])
        (folder / 'rows/R06.json').unlink()
        def forbidden(*args):
            raise AssertionError('recovered report repeated a call')
        resumed = run(RoundtableHarness(db, 'fake', forbidden), folder, resume=True)
        assert resumed == report
        with db.transaction() as session:
            assert session.scalar(select(func.count()).select_from(ModelOperation)) == 6
            assert session.get(ModelDailyBudget, (day_key(), 'global')).calls == 6
    finally:
        db.close()
