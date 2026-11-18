"""Real PostgreSQL acceptance in disposable schemas; no student data is used."""

import asyncio, uuid
from pathlib import Path
from unittest.mock import patch
import pytest
from psycopg import sql
from deeptutor.learning.models import LearningProgress, LearningModule, KnowledgePoint
from deeptutor.learning.storage import LearningStore
from deeptutor.learning.diagnosis import _digest
from deeptutor.learning import personal_training as models
from deeptutor.learning import training_jobs as jobs


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    original = models.connection
    schema = "acceptance_" + uuid.uuid4().hex
    try:
        with original() as c:
            c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    except Exception as e:
        pytest.skip("PostgreSQL unavailable: " + type(e).__name__)

    def connect():
        c = original()
        c.execute(sql.SQL("SET search_path TO {},public").format(sql.Identifier(schema)))
        c.commit()
        return c

    monkeypatch.setattr(models, "connection", connect)
    monkeypatch.setattr(jobs, "connection", connect)
    store = LearningStore(tmp_path / "learning")
    points = [
        KnowledgePoint(id="k" + str(i), name="Skill " + str(i), type="memory", module_id="m")
        for i in range(2)
    ]
    store.save(
        LearningProgress(
            book_id="p",
            modules=[LearningModule(id="m", name="Module", order=0, knowledge_points=points)],
        )
    )
    rows = [
        dict(sequence_id=str(s), timestamp=i, observation_id=f"{s}-{i}", correct=int(i > 8))
        for s in range(2)
        for i in range(30)
    ]
    snapshot = {
        "id": "snapshot-test",
        "digest": "test-only",
        "rows": rows,
        "provenance": [
            {
                "objective_id": p.id,
                "eligibility": "eligible",
                "context": {"objective_scope": _digest(p.model_dump(mode="json"))},
            }
            for p in points
        ],
    }
    monkeypatch.setattr(models, "create_snapshot", lambda *args: snapshot)
    yield store, snapshot, connect
    with original() as c:
        c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_personal_model_activation_scope_and_rollback(isolated):
    store, snapshot, _ = isolated
    run = models.train(store, "p", store.load("p").version)
    assert run["status"] == "validated_candidate"
    assert run["activated"]
    point = store.load("p").modules[0].knowledge_points[0]
    result = models.active_parameters(store, "p", point.id, _digest(point.model_dump(mode="json")))
    assert result[1] == run["id"]
    assert (
        models.active_parameters(
            store, "other-project", point.id, _digest(point.model_dump(mode="json"))
        )
        is None
    )
    assert models.active_parameters(store, "p", point.id, "changed-scope") is None
    with pytest.raises(RuntimeError):
        models.rollback_parameters(store, "p", "stale-id")
    assert models.rollback_parameters(store, "p", run["id"])["restored_defaults"]
    assert (
        models.active_parameters(store, "p", point.id, _digest(point.model_dump(mode="json")))
        is None
    )
    assert models.runs(store, "p")[0]["activated"] is False


def test_durable_worker_recovers_expired_job(isolated, monkeypatch):
    _, _, connect = isolated
    monkeypatch.setattr(
        jobs,
        "execute_job",
        lambda body, path: {
            "run_id": "test-run",
            "planning": {"action": "keep", "reason": "Test fixture"},
        },
    )
    with connect() as c:
        jobs.initialize(c)
        from psycopg.types.json import Jsonb

        c.execute(
            "INSERT INTO personal_training_jobs(id,scope,project,revision,status,lease_until,body) VALUES ('j','s','p',1,'running',0,%s)",
            (Jsonb({"id": "j", "created_at": 1}),),
        )

    async def check():
        stop = asyncio.Event()
        worker = asyncio.create_task(jobs.worker(stop))
        try:
            for _ in range(60):
                await asyncio.sleep(0.1)
                with connect() as c:
                    status = c.execute(
                        "SELECT status FROM personal_training_jobs WHERE id='j'"
                    ).fetchone()[0]
                if status == "completed":
                    break
            assert status == "completed"
        finally:
            stop.set()
            await worker

    asyncio.run(check())


def test_agent_plan_transaction_and_rollback(isolated):
    store, _, _ = isolated
    before = store.load("p")
    decision = jobs.PlanDecision(
        action="reorder",
        reason="Review recent error first",
        module_order=["m"],
        point_order={"m": ["k1", "k0"]},
    )
    result = jobs.apply_decision(store, "p", decision, before.version, "test-job")
    assert store.load("p").modules[0].knowledge_points[0].id == "k1"
    from deeptutor.learning.plan_versions import history, rollback

    version = history(store, "p")[0]
    rollback(store, "p", version["id"], result["revision"])
    assert store.load("p").modules[0].knowledge_points[0].id == "k0"


def test_native_model_change_audit_is_committed_with_project_revision(isolated, monkeypatch):
    from deeptutor.services.storage import postgres_adapter as pg
    from deeptutor.services.storage.postgres_migration import migrate

    store, _, connect = isolated
    migrate(store.db_path)
    original = pg.enabled
    monkeypatch.setattr(
        pg,
        "enabled",
        lambda path: Path(path).resolve() == store.db_path.resolve() or original(path),
    )
    try:
        before = store.load("p").version
        run = models.train(store, "p", before)
        assert run["activated"]
        assert store.load("p").version == before + 1
        assert any(e.event_type == "diagnostic.model_activated" for e in store.list_events("p"))
        models.rollback_parameters(store, "p", run["id"])
        assert store.load("p").version == before + 2
        assert any(e.event_type == "diagnostic.model_rolled_back" for e in store.list_events("p"))
    finally:
        with connect() as c:
            c.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(
                    sql.Identifier(pg.schema_name(store.db_path))
                )
            )
