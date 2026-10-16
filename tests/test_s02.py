"""S02：同一组行为在 SQLite 与隔离 PostgreSQL schema 执行。"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.config import Settings
from app.core.schema import MentalStateSnapshot
from app.gateway.v1 import SESSION_COOKIE, create_app
from app.infrastructure.db.database import Database
from app.infrastructure.db.maintenance import backup, import_legacy, restore
from app.infrastructure.db.models import Account, AuditEvent, AuthSession, LearningInput, LegacyRecord, OutboxJob, Workspace, now
from app.infrastructure.jobs.outbox import Outbox
from app.modules.errors import DomainError
from app.modules.identity.service import Identity
from app.modules.workspaces.service import Workspaces
from app.storage.db import Store

PASSWORD = "Test-only-password-123"


@contextmanager
def recovery_database(source, tmp_path):
    if source.engine.dialect.name == "sqlite":
        recovered = Database(f"sqlite:///{(tmp_path / 'restore.sqlite3').as_posix()}")
        recovered.migrate()
        try:
            yield recovered
        finally:
            recovered.close()
    else:
        schema = "s02_restore_" + uuid4().hex
        with source.engine.begin() as conn:
            conn.execute(CreateSchema(schema))
        url = source.engine.url.update_query_dict({"options": "-csearch_path=" + schema})
        recovered = Database(url.render_as_string(hide_password=False))
        try:
            recovered.migrate()
            yield recovered
        finally:
            recovered.close()
            assert schema.startswith("s02_restore_")
            with source.engine.begin() as conn:
                conn.execute(DropSchema(schema, cascade=True))


@pytest.fixture(params=["sqlite", "postgresql"])
def database(request, tmp_path):
    if request.param == "sqlite":
        db = Database(f"sqlite:///{(tmp_path / 's02.sqlite3').as_posix()}")
        db.migrate()
        yield db
        db.close()
        return
    if os.getenv("RSI_TEST_POSTGRES") != "1":
        pytest.skip("设置 RSI_TEST_POSTGRES=1 执行本机隔离 PostgreSQL 验收")
    # 不输出 URL；每个测试只创建和删除自己随机生成的 schema。
    config = Path(".local/database.env").read_text(encoding="utf-8")
    url = next(line.split("=", 1)[1] for line in config.splitlines() if line.startswith("RSI_DATABASE_URL="))
    schema = "s02_test_" + uuid4().hex
    control = create_engine(url, connect_args={"connect_timeout": 5})
    with control.begin() as conn:
        conn.execute(CreateSchema(schema))
    scoped_url = make_url(url).update_query_dict({"options": "-csearch_path=" + schema})
    db = Database(scoped_url.render_as_string(hide_password=False))
    try:
        db.migrate()
        yield db
    finally:
        db.close()
        assert schema.startswith("s02_test_") and len(schema) == 41
        with control.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
        control.dispose()


class Harness:
    def __init__(self, db, stack):
        self.db, self.stack = db, stack
        self.identity, self.spaces = Identity(db), Workspaces()
        self.identity.bootstrap("admin.s02", PASSWORD, "测试管理员")
        self.app = create_app(Settings(), db)
        self.admin = self.client("admin.s02")

    def client(self, name):
        client = self.stack.enter_context(TestClient(self.app))
        response = client.post("/api/v1/auth/login", json={"username": name, "password": PASSWORD})
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return client

    def student(self, name="student.one"):
        response = self.admin.post("/api/v1/admin/students", json={"username": name, "password": PASSWORD, "display_name": "测试学生"})
        assert response.status_code == 201, response.text
        return self.client(name), response.json()

    def workspace(self, client, kind="project", title="方程学习"):
        response = client.post("/api/v1/student/workspaces", headers={"Idempotency-Key": str(uuid4())}, json={"kind": kind, "title": title})
        assert response.status_code == 201, response.text
        return response.json()

    def actor(self, session, client):
        return self.identity.authorize(session, client.cookies.get(SESSION_COOKIE), client.headers["X-CSRF-Token"], "student", True)


@pytest.fixture
def env(database):
    with ExitStack() as stack:
        yield Harness(database, stack)


def test_roles_csrf_and_no_legacy_bypass(env):
    student, _ = env.student()
    assert student.get("/api/v1/admin/students").status_code == 403
    assert env.admin.get("/api/v1/student/workspaces").status_code == 403
    assert student.post("/api/contracts", json={"goal_text": "x"}).status_code == 404
    assert student.post("/api/v1/admin/students", json={}).status_code == 403
    csrf = student.headers.pop("X-CSRF-Token")
    assert student.post("/api/v1/student/workspaces", json={"kind": "project", "title": "x"}).status_code == 403
    student.headers["X-CSRF-Token"] = csrf
    assert student.post("/api/v1/student/workspaces", headers={"Origin": "https://untrusted.example"},
                        json={"kind": "project", "title": "x"}).status_code == 403
    data = env.admin.get("/api/v1/admin/students").json()
    assert "password_hash" not in json.dumps(data) and PASSWORD not in json.dumps(data)
    with env.db.transaction() as session:
        account = session.scalar(select(Account).where(Account.role == "student"))
        assert account.password_hash.startswith("$argon2id$")


def test_project_and_temporary_isolation_archive_and_revisions(env):
    one, _ = env.student()
    two, _ = env.student("student.two")
    project = env.workspace(one)
    temporary = env.workspace(one, "temporary", "随便问一题")
    assert project["project_id"] and temporary["project_id"] is None
    wid = project["workspace_id"]
    path = f"/api/v1/student/workspaces/{wid}"
    assert two.get(path).status_code == 404
    assert two.patch(path, json={"title": "越权", "expected_revision": 1}).status_code == 404
    assert two.post(path + "/archive", json={"expected_revision": 1}).status_code == 404
    assert two.get("/api/v1/student/workspaces").json() == {"items": []}
    result = one.post(path + "/archive", json={"expected_revision": 1})
    assert result.status_code == 200 and result.json()["revision"] == 2
    assert one.post(path + "/archive", json={"expected_revision": 1}).status_code == 409
    assert one.post(path + "/archive", json={"expected_revision": 2}).json()["revision"] == 2
    assert one.patch(path, json={"title": "归档后改名", "expected_revision": 2}).status_code == 409
    restored = one.post(path + "/restore", json={"expected_revision": 2}).json()
    assert restored["status"] == "active" and restored["revision"] == 3
    assert len(one.get("/api/v1/student/workspaces?status=active").json()["items"]) == 2
    assert one.post("/api/v1/student/workspaces", json={"kind": "project", "title": "x", "owner_id": "other"}).status_code == 422


def test_reset_disable_logout_and_expiry(env, monkeypatch):
    student, account = env.student()
    wid = env.workspace(student)["workspace_id"]
    response = env.admin.post(f"/api/v1/admin/students/{account['id']}/reset-password", json={"password": PASSWORD})
    assert response.status_code == 200
    assert student.get("/api/v1/auth/me").status_code == 401
    student = env.client("student.one")
    assert student.get(f"/api/v1/student/workspaces/{wid}").status_code == 200
    assert student.post("/api/v1/auth/logout").status_code == 204
    assert student.get("/api/v1/auth/me").status_code == 401
    student = env.client("student.one")
    with env.db.transaction() as session:
        login = session.scalar(select(AuthSession).join(Account).where(Account.id == account["id"]))
        login.expires_at = now() - 1
    assert student.get("/api/v1/auth/me").status_code == 401
    student = env.client("student.one")
    assert env.admin.post(f"/api/v1/admin/students/{account['id']}/disable").status_code == 200
    assert student.get("/api/v1/auth/me").status_code == 401
    assert student.post("/api/v1/auth/login", json={"username": "student.one", "password": PASSWORD}).status_code == 401
    with env.db.transaction() as session:
        assert session.get(Workspace, wid) is not None


def test_bootstrap_once_and_persistent_login_throttle(env):
    with pytest.raises(DomainError, match="已初始化"):
        env.identity.bootstrap("another.admin", PASSWORD, "x")
    client = env.stack.enter_context(TestClient(env.app))
    for _ in range(20):
        result = client.post("/api/v1/auth/login", json={"username": "unknown", "password": "wrong"})
        assert result.status_code == 401
    new_app = create_app(Settings(), env.db)
    with TestClient(new_app) as restarted:
        assert restarted.post("/api/v1/auth/login", json={"username": "unknown", "password": "wrong"}).status_code == 429
    invalid = client.post("/api/v1/auth/login", json={"username": "unknown", "password": "secret" * 100})
    assert invalid.status_code == 422 and "secret" not in invalid.text


def seed_input(env, client, wid, key="input-one"):
    with env.db.transaction() as session:
        actor = env.actor(session, client)
        conversation = env.spaces.create_conversation(session, actor, wid, "tutor")
        entry = env.spaces.accept_input(session, actor, wid, conversation.id, key, 1, {"answer": "3"})
        return conversation.id, entry.id


def test_inputs_outbox_atomic_scoped_and_replayable_after_restart(env):
    student, _ = env.student()
    first, second, temp = [env.workspace(student, kind, title)["workspace_id"] for kind, title in
                            [("project", "A"), ("project", "B"), ("temporary", "T")]]
    cid, first_id = seed_input(env, student, first)
    _, second_id = seed_input(env, student, second)
    _, temp_id = seed_input(env, student, temp)
    identity = Identity(env.db)
    with env.db.transaction() as session:
        actor = identity.authorize(session, student.cookies.get(SESSION_COOKIE), student.headers["X-CSRF-Token"], "student", True)
        replay = env.spaces.accept_input(session, actor, first, cid, "input-one", 1, {"answer": "3"})
        assert replay.id == first_id
        assert set(env.spaces.input_ids(session, actor, real_only=False)) == {first_id, second_id}
        assert env.spaces.input_ids(session, actor, first, real_only=False) == [first_id]
        assert env.spaces.input_ids(session, actor, temp, real_only=False) == [temp_id]
        assert env.spaces.input_ids(session, actor) == []  # synthetic 不能计入真实学生证据
        assert session.scalar(select(func.count()).select_from(OutboxJob)) == 3
    with pytest.raises(DomainError, match="不同内容"):
        with env.db.transaction() as session:
            env.spaces.accept_input(session, env.actor(session, student), first, cid, "input-one", 1, {"answer": "4"})
    with pytest.raises(DomainError, match="未找到会话"):
        with env.db.transaction() as session:
            env.spaces.accept_input(session, env.actor(session, student), second, cid, "bad", 1, {})
    with env.db.transaction() as session:
        actor = env.actor(session, student)
        env.spaces.change(session, actor, first, 1, status="archived")
    with pytest.raises(DomainError, match="已归档"):
        with env.db.transaction() as session:
            env.spaces.accept_input(session, env.actor(session, student), first, cid, "new-key", 2, {})


def test_input_rollback_leaves_no_half_accepted_job(env, monkeypatch):
    student, _ = env.student()
    wid = env.workspace(student)["workspace_id"]
    original = Session.add
    def fail_job(self, instance, *args, **kwargs):
        if isinstance(instance, OutboxJob):
            raise RuntimeError("injected outbox failure")
        return original(self, instance, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Session, "add", fail_job)
        with pytest.raises(RuntimeError, match="injected"):
            seed_input(env, student, wid)
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(LearningInput)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxJob)) == 0


def test_lease_recovery_fencing_and_retry_limit(env, monkeypatch):
    student, _ = env.student()
    seed_input(env, student, env.workspace(student)["workspace_id"])
    clock = [now()]
    monkeypatch.setattr("app.infrastructure.jobs.outbox.now", lambda: clock[0])
    queue = Outbox(env.db)
    original = queue.claim(lease_seconds=1)
    assert original and queue.claim() is None
    clock[0] += 2
    recovered = Outbox(env.db).claim()
    assert recovered["job_id"] == original["job_id"] and recovered["lease_token"] != original["lease_token"]
    with pytest.raises(DomainError, match="失效"):
        queue.finish(original["job_id"], original["lease_token"])
    queue.finish(recovered["job_id"], recovered["lease_token"])
    assert queue.claim() is None
    seed_input(env, student, env.workspace(student)["workspace_id"])
    for attempt in range(5):
        job = queue.claim()
        queue.finish(job["job_id"], job["lease_token"], "model_unavailable")
        clock[0] += 61
    assert queue.claim() is None
    with env.db.transaction() as session:
        assert set(session.scalars(select(OutboxJob.status))) == {"completed", "dead"}


def test_concurrent_revision_conflict_is_deterministic(env):
    student, _ = env.student()
    wid = env.workspace(student)["workspace_id"]
    token, csrf = student.cookies.get(SESSION_COOKIE), student.headers["X-CSRF-Token"]
    def rename(title):
        try:
            with env.db.transaction() as session:
                actor = env.identity.authorize(session, token, csrf, "student", True)
                env.spaces.change(session, actor, wid, 1, title=title)
            return 200
        except DomainError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(rename, ["新标题 A", "新标题 B"]))
    assert sorted(results) == [200, 409]


def test_backup_restore_and_legacy_quarantine(env, tmp_path):
    student, _ = env.student()
    wid = env.workspace(student)["workspace_id"]
    seed_input(env, student, wid)
    old_path = tmp_path / "legacy.sqlite3"
    legacy = Store(str(old_path))
    legacy.ensure_learner("legacy-learner")
    legacy.append_snapshot(MentalStateSnapshot(learner_id="legacy-learner"))
    legacy.conn.close()
    result = import_legacy(env.db, old_path, tmp_path / "legacy-snapshot.sqlite3")
    assert result["tables"]["snapshots"]["count"] == 1
    again = import_legacy(env.db, old_path, tmp_path / "legacy-snapshot-two.sqlite3")
    assert again["already_imported"]
    with env.db.transaction() as session:
        assert set(session.scalars(select(LegacyRecord.source_kind))) == {"legacy_unknown"}
        assert session.scalar(select(func.count()).select_from(Account)) == 2
    destination = tmp_path / "backup.json"
    counts = backup(env.db, destination)
    with recovery_database(env.db, tmp_path) as recovery:
        assert restore(recovery, destination) == counts
        with recovery.transaction() as session:
            assert session.get(Workspace, wid) is not None
            assert session.scalar(select(func.count()).select_from(AuthSession)) == 0
            assert session.scalar(select(func.count()).select_from(OutboxJob)) == 1
        with pytest.raises(ValueError, match="拒绝覆盖"):
            restore(recovery, destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["data"]["tables"]["accounts"][0]["display_name"] = "tampered"
    corrupted = tmp_path / "corrupted.json"
    corrupted.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="校验失败"):
        restore(env.db, corrupted)


def test_concurrent_bootstrap_and_database_restart(database):
    def initialize(name):
        try:
            return Identity(database).bootstrap(name, PASSWORD, "测试管理员")["role"]
        except DomainError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(initialize, ["admin.one", "admin.two"]), key=str) == [409, "admin"]
    database.close()  # 断开连接池，随后从持久介质重新读取。
    with database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Account)) == 1


def test_concurrent_input_idempotency(env):
    student, _ = env.student()
    wid = env.workspace(student)["workspace_id"]
    with env.db.transaction() as session:
        cid = env.spaces.create_conversation(session, env.actor(session, student), wid, "tutor").id
    def submit(_):
        with env.db.transaction() as session:
            return env.spaces.accept_input(session, env.actor(session, student), wid, cid, "same-key", 1, {"answer": "3"}).id
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(submit, range(2)))
    assert result[0] == result[1]
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(OutboxJob)) == 1


def test_archive_input_race_and_foreign_actor(env):
    student, _ = env.student()
    outsider, _ = env.student("student.two")
    wid = env.workspace(student)["workspace_id"]
    with env.db.transaction() as session:
        cid = env.spaces.create_conversation(session, env.actor(session, student), wid, "tutor").id
    with pytest.raises(DomainError, match="未找到工作空间"):
        with env.db.transaction() as session:
            env.spaces.accept_input(session, env.actor(session, outsider), wid, cid, "bad", 1, {})
    def operation(action):
        try:
            with env.db.transaction() as session:
                actor = env.actor(session, student)
                if action == "archive":
                    env.spaces.change(session, actor, wid, 1, status="archived")
                else:
                    env.spaces.accept_input(session, actor, wid, cid, "race", 1, {"answer": "3"})
            return 200
        except DomainError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(operation, ["archive", "input"]))
    assert result[0] == 200 and result[1] in {200, 409}
    with env.db.transaction() as session:
        assert session.get(Workspace, wid).status == "archived"
        inputs = session.scalar(select(func.count()).select_from(LearningInput))
        jobs = session.scalar(select(func.count()).select_from(OutboxJob))
        assert inputs == jobs == (1 if result[1] == 200 else 0)


def test_inputs_and_audit_cannot_be_overwritten(env):
    student, _ = env.student()
    wid = env.workspace(student)["workspace_id"]
    _, iid = seed_input(env, student, wid)
    with pytest.raises(DBAPIError, match="append-only"):
        with env.db.transaction() as session:
            session.execute(update(LearningInput).where(LearningInput.id == iid).values(payload={"answer": "changed"}))
    with pytest.raises(DBAPIError, match="append-only"):
        with env.db.transaction() as session:
            session.execute(update(AuditEvent).values(action="changed"))


def test_cookie_flags_cross_session_csrf_and_restarted_app(env):
    student, _ = env.student()
    other_session = env.client("student.one")
    assert student.post("/api/v1/student/workspaces", headers={"X-CSRF-Token": other_session.headers["X-CSRF-Token"]},
                        json={"kind": "project", "title": "x"}).status_code == 403
    with TestClient(create_app(Settings(cookie_secure=True), env.db), base_url="https://testserver") as secure:
        login = secure.post("/api/v1/auth/login", json={"username": "student.one", "password": PASSWORD})
        cookies = login.headers.get_list("set-cookie")
        assert any("wenjin_session=" in cookie and "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie for cookie in cookies)
    env.db.close()
    with TestClient(create_app(Settings(), env.db)) as restarted:
        restarted.cookies.update(student.cookies)
        assert restarted.get("/api/v1/auth/me").json()["account"]["username"] == "student.one"


def test_crashed_workers_eventually_dead_letter(env, monkeypatch):
    student, _ = env.student()
    seed_input(env, student, env.workspace(student)["workspace_id"])
    clock = [now()]
    monkeypatch.setattr("app.infrastructure.jobs.outbox.now", lambda: clock[0])
    queue = Outbox(env.db)
    for attempt in range(5):
        assert queue.claim(lease_seconds=1)["attempts"] == attempt + 1
        clock[0] += 2
    assert queue.claim() is None
    with env.db.transaction() as session:
        job = session.scalar(select(OutboxJob))
        assert job.status == "dead" and job.error_code == "lease_expired"


def test_workspace_creation_retries_do_not_duplicate(env):
    student, _ = env.student()
    headers = {"Idempotency-Key": "create-first-project"}
    body = {"kind": "project", "title": "只建一个"}
    first = student.post("/api/v1/student/workspaces", headers=headers, json=body)
    replay = student.post("/api/v1/student/workspaces", headers=headers, json=body)
    assert first.status_code == replay.status_code == 201
    assert first.json()["workspace_id"] == replay.json()["workspace_id"]
    assert student.post("/api/v1/student/workspaces", headers=headers, json={**body, "title": "另一内容"}).status_code == 409
    assert student.post("/api/v1/student/workspaces", json=body).status_code == 422
    with env.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Workspace)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "workspace.create")) == 1
