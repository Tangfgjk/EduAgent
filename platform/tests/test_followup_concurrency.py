"""Controlled multi-connection regressions for correction and artifact races."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, Lock

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.core.schema import GoalContract, GoalStatement, utcnow
from app.gateway.routes import create_app
import app.gateway.qualitative_routes as qualitative_routes
from app.learning.recovery import RecoveryJobs
from app.learning.service import LearningService
from app.storage.db import Store
from tests.test_learning_storage import evidence, service

GRAPH = {"MATH.G7.EQ.SOLVE": []}


class RollbackWindow:
    """Pause only after rollback releases the SQLite writer lock."""

    def __init__(self, connection, released, completed):
        self.connection, self.released, self.completed = connection, released, completed
        self.opened = False

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def rollback(self):
        self.connection.rollback()
        if not self.opened:
            self.opened = True
            self.released.set()
            assert self.completed.wait(10), "Competing worker did not finish inside rollback window"


def test_failed_worker_cannot_overwrite_competing_completed_job(tmp_path):
    path = str(tmp_path / "recovery.sqlite3")
    seed, failed_store, successful_store = Store(path), Store(path), Store(path)
    try:
        learning = service(seed)
        seed.save_contract(GoalContract(learner_id="s1", goal_statement=GoalStatement(text="Independent reasoning")))
        learning.consume(evidence("original"))
        learning.consume(evidence("corrected", attempt_id="original", supersedes="original",
                                  verdict_status="failed", score=0))
        job = RecoveryJobs(seed).pending("s1")[0]
        released, completed = Event(), Event()
        failed_store.conn = RollbackWindow(failed_store.conn, released, completed)

        def failing_worker():
            def fault(stage):
                if stage == "proposal":
                    raise RuntimeError("injected proposal failure")
            with pytest.raises(RuntimeError, match="injected proposal failure"):
                RecoveryJobs(failed_store).run("s1", job["job_id"], GRAPH, fault)

        def successful_worker():
            assert released.wait(10), "Failing worker did not release its transaction"
            try:
                return RecoveryJobs(successful_store).run("s1", job["job_id"], GRAPH)
            finally:
                completed.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            failed = executor.submit(failing_worker)
            successful = executor.submit(successful_worker)
            result = successful.result(timeout=15)
            failed.result(timeout=15)

        jobs = RecoveryJobs(seed)
        assert jobs.get("s1", job["job_id"]) == result
        assert result["status"] == "completed" and result["attempts"] == 1
        assert jobs.run("s1", job["job_id"], GRAPH) == result
        assert seed.conn.execute("SELECT COUNT(*) FROM plan_versions").fetchone()[0] == 1
    finally:
        for store in (seed, failed_store, successful_store):
            store.close()


@pytest.mark.parametrize("changed_content", [False, True])
def test_artifact_same_attempt_concurrent_distinct_clocks_are_idempotent_or_conflicting(
        tmp_path, monkeypatch, changed_content):
    path = str(tmp_path / "artifacts.sqlite3")
    seed, first, second = Store(path), Store(path), Store(path)
    try:
        service(seed)
        apps = [create_app(Settings(learner_id="s1"), store=store) for store in (first, second)]
        barrier, time_lock = Barrier(2), Lock()
        base, tick = utcnow(), [0]
        timestamps = []
        consume = LearningService.consume

        def distinct_clock():
            with time_lock:
                tick[0] += 1
                return base + timedelta(microseconds=tick[0])

        def concurrent_consume(self, value, **kwargs):
            timestamps.append(value.occurred_at)
            # Both requests have read an absent receipt and built their evidence.
            barrier.wait(timeout=10)
            return consume(self, value, **kwargs)

        monkeypatch.setattr(qualitative_routes, "utcnow", distinct_clock)
        monkeypatch.setattr(LearningService, "consume", concurrent_consume)

        def submit(index):
            content = "Independent explanation"
            if changed_content and index:
                content = "A conflicting explanation"
            with TestClient(apps[index], raise_server_exceptions=False) as client:
                return client.post("/api/learning/qualitative/s1/artifacts", json={
                    "assessment_id": "Q-EXPLAIN-SOLVE", "attempt_id": "concurrent-attempt",
                    "content": content})

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, range(2)))

        assert len(set(timestamps)) == 2
        assert sorted(response.status_code for response in results) == ([200, 409] if changed_content else [200, 200])
        if not changed_content:
            assert results[0].json() == results[1].json()
        for table in ("learning_evidence", "learning_api_receipts", "qualitative_artifacts"):
            assert seed.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        assert seed.conn.execute("SELECT COUNT(*) FROM learning_transitions").fetchone()[0] == 2
    finally:
        for store in (seed, first, second):
            store.close()
