"""同一运行协议在 SQLite 与真实 PostgreSQL 上验收。"""
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.infrastructure.db.maintenance import backup, restore
from app.infrastructure.db.models import AgentCheckpoint, AgentEvent, AgentRun, LearningInput, OutboxJob
from app.modules.learning.graph import VERSION
from app.worker import Worker
from tests.test_s02 import database, env, recovery_database


def start(env, student, wid, key=None):
    response = student.post(f"/api/v1/student/workspaces/{wid}/runs", headers={"Idempotency-Key": key or str(uuid4())},
                            json={"goal": "理解一元一次方程的等式性质", "daily_minutes": 15})
    assert response.status_code == 202, response.text
    return response.json()


def setup(env):
    student, _ = env.student()
    wid = env.workspace(student)["workspace_id"]
    run = start(env, student, wid)
    path = f"/api/v1/student/workspaces/{wid}/runs/{run['run_id']}"
    return student, wid, path


def decision(run, action="confirm", minutes=None):
    return {"action": action, "gate_id": run["gate_id"], "expected_revision": run["revision"],
            "daily_minutes": minutes or run["draft"]["daily_minutes"]}


def test_durable_interrupt_revision_confirm_and_sse(env):
    student, wid, path = setup(env)
    assert Worker(env.db).step()
    run = student.get(path).json()
    assert run["status"] == "awaiting_input" and run["draft"]["source"] == "development_template"
    old = decision(run)
    response = student.post(path + "/resume", headers={"Idempotency-Key": "revision-one"}, json=decision(run, "revise", 25))
    assert response.status_code == 202
    # 重建 worker，恢复持久检查点。旧关口不会被新决定覆盖。
    assert Worker(env.db).step()
    revised = student.get(path).json()
    assert revised["draft"]["daily_minutes"] == 25 and revised["draft"]["generation"] == 2
    assert revised["gate_id"] != run["gate_id"]
    assert student.post(path + "/resume", headers={"Idempotency-Key": "old-gate"}, json=old).status_code == 409
    body = decision(revised)
    for _ in range(2):
        assert student.post(path + "/resume", headers={"Idempotency-Key": "confirm-once"}, json=body).status_code == 202
    assert Worker(env.db).step()
    assert not Worker(env.db).step()
    assert student.get(path).json()["status"] == "completed"
    events = student.get(path + "/event-history").json()
    assert [row["seq"] for row in events] == list(range(1, 7))
    assert events[-1]["kind"] == "plan.confirmed"
    batch = student.get(path + "/events", headers={"Last-Event-ID": "4"})
    assert batch.headers["content-type"].startswith("text/event-stream")
    assert "id: 5\n" in batch.text and "id: 4\n" not in batch.text
    assert "learner_id" not in batch.text and "checkpoint" not in batch.text
    assert student.get(path + "/events", headers={"Last-Event-ID": "999"}).status_code == 409
    assert student.get(path + "/events", headers={"Last-Event-ID": "-1"}).status_code == 422


def test_idempotency_scope_and_temporary_guard(env):
    student, _ = env.student()
    wid = env.workspace(student)["workspace_id"]
    first = start(env, student, wid, "same-start")
    assert start(env, student, wid, "same-start")["run_id"] == first["run_id"]
    base = f"/api/v1/student/workspaces/{wid}/runs"
    assert student.post(base, headers={"Idempotency-Key": "same-start"}, json={"goal": "不同目标"}).status_code == 409
    assert student.post(base, headers={"Idempotency-Key": "another-start"}, json={"goal": "新目标"}).status_code == 409
    two, _ = env.student("student.two")
    assert two.get(base).status_code == 404
    assert two.get(base + "/" + first["run_id"]).status_code == 404
    other = env.workspace(student)["workspace_id"]
    assert student.get(f"/api/v1/student/workspaces/{other}/runs/{first['run_id']}").status_code == 404
    temp = env.workspace(student, "temporary")["workspace_id"]
    assert student.post(f"/api/v1/student/workspaces/{temp}/runs", headers={"Idempotency-Key": "temp"}, json={"goal": "方程"}).status_code == 409
    assert student.post(base, headers={"Idempotency-Key": "invalid"}, json={"goal": " "}).status_code == 422
    assert student.post(f"/api/v1/student/workspaces/{other}/runs", headers={"Idempotency-Key": "forbidden"}, json={"goal": "替我做作业"}).status_code == 422
    assert student.post(base, headers={"Idempotency-Key": "client-thread"}, json={"goal": "方程", "thread_id": "bad"}).status_code == 422
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(AgentRun)) == 1
        assert session.scalar(select(func.count()).select_from(LearningInput)) == 1


@pytest.mark.parametrize("phase", ["queued", "awaiting_input"])
def test_cancel_never_advances_after_commit(env, phase):
    student, _, path = setup(env)
    if phase == "awaiting_input":
        Worker(env.db).step()
    run = student.get(path).json()
    body = {"expected_revision": run["revision"]}
    assert student.post(path + "/cancel", json=body).json()["status"] == "cancelled"
    assert student.post(path + "/cancel", json=body).status_code == 200
    Worker(env.db).step()
    assert student.get(path).json()["status"] == "cancelled"
    assert student.get(path + "/event-history").json()[-1]["kind"] == "run.cancelled"


@pytest.mark.parametrize("reason", ["archive", "disable", "version"])
def test_pending_run_rechecks_current_scope_and_version(env, reason):
    student, wid, path = setup(env)
    if reason == "archive":
        assert student.post(f"/api/v1/student/workspaces/{wid}/archive", json={"expected_revision": 1}).status_code == 200
    elif reason == "disable":
        account = student.get("/api/v1/auth/me").json()["account"]
        assert env.admin.post(f"/api/v1/admin/students/{account['id']}/disable").status_code == 200
    else:
        with env.db.transaction() as session:
            session.get(AgentRun, path.split("/")[-1]).version = "old-version"
    Worker(env.db).step()
    with env.db.transaction() as session:
        run = session.get(AgentRun, path.split("/")[-1])
        assert run.status == "failed" and run.error_code
        assert session.get(AgentCheckpoint, run.id) is None


def test_checkpoint_and_result_rollback_then_retry(env, monkeypatch):
    import app.worker as module
    student, _, path = setup(env)
    original = module.advance
    def crash(*args):
        original(*args)
        raise RuntimeError("模拟检查点计算后、事务提交前崩溃")
    monkeypatch.setattr(module, "advance", crash)
    Worker(env.db).step()
    assert student.get(path).json()["status"] == "queued"
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(AgentCheckpoint)) == 0
        job = session.scalar(select(OutboxJob))
        assert job.status == "pending" and job.error_code == "runtime_error"
        job.available_at = 0
    monkeypatch.setattr(module, "advance", original)
    Worker(env.db).step()
    assert student.get(path).json()["draft"]["generation"] == 1
    assert len(student.get(path + "/event-history").json()) == 2


def test_backup_restores_pending_gate_and_resumes(env, tmp_path):
    from app.modules.identity.service import Actor
    from app.modules.learning.service import Learning
    from app.infrastructure.db.models import Account, Workspace
    student, wid, path = setup(env)
    Worker(env.db).step()
    waiting = student.get(path).json()
    destination = tmp_path / "pending.json"
    backup(env.db, destination)
    with recovery_database(env.db, tmp_path) as recovered:
        restore(recovered, destination)
        with recovered.transaction() as session:
            owner = session.get(Workspace, wid).owner_id
            actor = Actor(session.get(Account, owner), "local-test")
            Learning().resume(session, actor, wid, waiting["run_id"], decision(waiting), "restore-confirm")
        Worker(recovered).step()
        with recovered.transaction() as session:
            assert session.get(AgentRun, waiting["run_id"]).status == "completed"
            assert session.scalar(select(func.count()).select_from(AgentEvent)) == 4


def test_two_workers_do_not_duplicate_results(env):
    student, _, path = setup(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: Worker(env.db).step(), range(2)))
    assert results.count(True) == 1
    assert student.get(path).json()["status"] == "awaiting_input"
    assert len(student.get(path + "/event-history").json()) == 2


def test_exhausted_retry_exposes_terminal_failure(env, monkeypatch):
    import app.worker as module
    student, _, path = setup(env)
    def crash(*args):
        raise RuntimeError("测试故障，不应记录原始异常")
    monkeypatch.setattr(module, "advance", crash)
    with env.db.transaction() as session:
        session.scalar(select(OutboxJob)).attempts = 4
    Worker(env.db).step()
    result = student.get(path).json()
    assert result["status"] == "failed" and result["error_code"] == "retries_exhausted"
    assert student.get(path + "/event-history").json()[-1]["kind"] == "run.failed"


def test_expired_lease_rolls_back_checkpoint_result(env, monkeypatch):
    import app.worker as module
    from app.infrastructure.db.models import now
    student, _, path = setup(env)
    original = module.advance
    def expire(*args):
        result = original(*args)
        monkeypatch.setattr(module, "now", lambda: now() + 120)
        return result
    monkeypatch.setattr(module, "advance", expire)
    Worker(env.db).step()
    assert student.get(path).json()["status"] == "queued"
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(AgentCheckpoint)) == 0
        assert session.scalar(select(OutboxJob)).status == "pending"
