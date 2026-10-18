from sqlalchemy import func, select

from app.evaluation.teach import TeachHarness
from app.evaluation.teach_multiturn import run
from app.infrastructure.db.database import Database
from app.infrastructure.db.models import ModelDailyBudget, ModelOperation
from app.infrastructure.runtime.model_tasks import day_key


def test_complete_flows_resume_without_calls_or_new_reservations(tmp_path):
    db = Database('sqlite:///' + (tmp_path / 'eval.sqlite3').as_posix())
    db.migrate()
    try:
        folder = tmp_path / 'run'
        report = run(TeachHarness(db, 'fake'), folder)
        assert report['summary']['total_attempts'] == 6
        assert report['summary']['contract_pass'] == 6
        assert [r['context_turns'] for r in report['rows']] == [1, 2, 3, 1, 2, 3]
        assert all(f['status'] == 'completed' and f['verification_check']['independent'] for f in report['flows'])
        assert all(r['outbound_identity_canary_absent'] for r in report['rows'])
        (folder / 'rows/TM01-2.json').unlink()
        def forbidden(*args):
            raise AssertionError('completed flow was sent again')
        resumed = run(TeachHarness(db, 'fake', forbidden), folder, resume=True)
        assert resumed == report
        with db.transaction() as session:
            assert session.scalar(select(func.count()).select_from(ModelOperation)) == 6
            assert session.get(ModelDailyBudget, (day_key(), 'global')).calls == 6
    finally:
        db.close()
