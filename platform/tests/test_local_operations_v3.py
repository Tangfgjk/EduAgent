"""Local operations exercise temporary DBs only; no user's server or data."""
import hashlib
import json
import sqlite3

import pytest

from app.core.schema import MentalStateSnapshot, utcnow
from app.governance.authority import AuthorizationDenied, Principal
from app.governance.service import GovernanceService
from app.learning.service import ConsentDenied, LearningService
from app.operations.cli import main
from app.operations.local import BackupPolicy, backup_online, health_report, restore_new_quarantined, run_worker_batch
from app.storage.db import Store


OFFICER = Principal("operator", "privacy_officer", frozenset({"student"}))
LEARNER = Principal("student", "learner", frozenset({"student"}))
POLICY = BackupPolicy("test-retention-policy", True)


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "source.sqlite3"
    store = Store(str(path))
    store.ensure_learner("student")
    LearningService(store).set_consent("student", ["teaching"], "c1", "student", utcnow())
    snapshot = MentalStateSnapshot(learner_id="student")
    snapshot.in_session_state.attention_focus = "sensitive-test-only-private-content"
    store.append_snapshot(snapshot)
    GovernanceService(store)
    yield path, store, tmp_path
    store.close()


def backup(store, root, name="backup.sqlite3", **kwargs):
    return backup_online(store, OFFICER, "student", trusted_root=root, filename=name, policy=POLICY, **kwargs)


def restore(root, record, name="restored.sqlite3", **kwargs):
    return restore_new_quarantined(OFFICER, "student", trusted_root=root, backup_name="backup.sqlite3",
        destination_name=name, expected_sha256=record["sha256"], policy=POLICY, **kwargs)


def test_online_backup_and_new_quarantined_restore_keep_live_db_unchanged(workspace):
    _, store, root = workspace
    before = list(store.conn.execute("SELECT * FROM snapshots"))
    record = backup(store, root)
    assert record["consistent_online_snapshot"] and record["integrity_check"] == "ok"
    assert "student" not in json.dumps(record) and "private-content" not in json.dumps(record)
    assert record["sha256"] == hashlib.sha256((root / "backup.sqlite3").read_bytes()).hexdigest()
    manifest = json.loads((root / "backup.sqlite3.manifest.json").read_text())
    assert manifest == record
    result = restore(root, record)
    assert result["quarantined"] and not result["live_service_switched"]
    staged = Store(str(root / "restored.sqlite3"))
    try:
        assert staged.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
        with pytest.raises(ConsentDenied):
            LearningService(staged).evidences("student")
        LearningService(staged).set_consent("student", ["teaching"], "fresh", "student", utcnow())
        with pytest.raises(ConsentDenied):
            LearningService(staged).evidences("student")
    finally:
        staged.close()
    assert list(store.conn.execute("SELECT * FROM snapshots")) == before
    assert LearningService(store).evidences("student") == []


@pytest.mark.parametrize("failure", ["existing", "traversal", "policy", "role", "withdraw", "foreign"])
def test_backup_fail_closed_preserves_targets_and_user_db(workspace, failure):
    _, store, root = workspace
    if failure == "existing":
        (root / "backup.sqlite3").write_bytes(b"existing-do-not-overwrite")
        with pytest.raises(FileExistsError):
            backup(store, root)
        assert (root / "backup.sqlite3").read_bytes() == b"existing-do-not-overwrite"
    elif failure == "traversal":
        with pytest.raises(ValueError):
            backup(store, root, "../outside.sqlite3")
    elif failure == "policy":
        with pytest.raises(AuthorizationDenied):
            backup_online(store, OFFICER, "student", trusted_root=root, filename="backup.sqlite3", policy=BackupPolicy())
    elif failure == "role":
        with pytest.raises(AuthorizationDenied):
            backup_online(store, LEARNER, "student", trusted_root=root, filename="backup.sqlite3", policy=POLICY)
    elif failure == "withdraw":
        GovernanceService(store).change_privacy(LEARNER, "student", "withdraw", reason_code="requested")
        with pytest.raises(ConsentDenied):
            backup(store, root)
    else:
        store.append_snapshot(MentalStateSnapshot(learner_id="foreign"))
        with pytest.raises(AuthorizationDenied):
            backup(store, root)
    assert store.conn.execute("SELECT COUNT(*) FROM snapshots WHERE learner_id='student'").fetchone()[0] == 1


@pytest.mark.parametrize("stage", ["reserved", "copied", "manifest"])
def test_backup_fault_removes_only_files_created_by_that_operation(workspace, stage):
    _, store, root = workspace
    sentinel = root / "keep.sqlite3"
    sentinel.write_bytes(b"keep")
    def fail(current):
        if current == stage:
            raise RuntimeError("secret-error-must-not-be-logged")
    with pytest.raises(RuntimeError):
        backup(store, root, fault=fail)
    assert not (root / "backup.sqlite3").exists()
    assert not (root / "backup.sqlite3.manifest.json").exists()
    assert sentinel.read_bytes() == b"keep"


def test_backup_existing_manifest_is_not_erased_on_reservation_failure(workspace):
    _, store, root = workspace
    manifest = root / "backup.sqlite3.manifest.json"
    manifest.write_text("existing-manifest")
    with pytest.raises(FileExistsError):
        backup(store, root)
    assert manifest.read_text() == "existing-manifest"
    assert not (root / "backup.sqlite3").exists()


@pytest.mark.parametrize("failure", ["hash", "corrupt", "overwrite", "fault", "foreign"])
def test_restore_failures_do_not_overwrite_live_database(workspace, failure):
    path, store, root = workspace
    record = backup(store, root)
    if failure == "hash":
        record["sha256"] = "0" * 64
        with pytest.raises(ValueError):
            restore(root, record)
    elif failure == "corrupt":
        (root / "backup.sqlite3").write_bytes(b"corrupt")
        record["sha256"] = hashlib.sha256(b"corrupt").hexdigest()
        with pytest.raises(sqlite3.DatabaseError):
            restore(root, record)
    elif failure == "overwrite":
        with pytest.raises(FileExistsError):
            restore(root, record, name=path.name)
    elif failure == "fault":
        with pytest.raises(RuntimeError):
            restore(root, record, fault=lambda _: (_ for _ in ()).throw(RuntimeError("injected")))
    else:
        connection = sqlite3.connect(root / "backup.sqlite3")
        connection.execute("INSERT INTO snapshots VALUES ('foreign','other','now','{}')")
        connection.commit()
        connection.close()
        record["sha256"] = hashlib.sha256((root / "backup.sqlite3").read_bytes()).hexdigest()
        with pytest.raises(AuthorizationDenied):
            restore(root, record)
    assert store.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
    assert not (root / "restored.sqlite3").exists()


def test_health_is_read_only_aggregate_and_cli_does_not_print_private_values(workspace, capsys):
    path, store, _ = workspace
    before = store.conn.total_changes
    report = health_report(store.conn)
    assert report["ok"] and not report["production_readiness_claim"]
    assert "student" not in json.dumps(report) and "sensitive" not in json.dumps(report)
    assert store.conn.total_changes == before
    assert main(["--database", str(path), "--learner-id", "student", "health"]) == 0
    output = capsys.readouterr().out
    assert "private-content" not in output and "source.sqlite3" not in output


def test_cli_failed_restore_sanitizes_error_and_never_switches_server(workspace, capsys):
    path, _, root = workspace
    assert main(["--database", str(path), "--learner-id", "student", "restore",
        "--root", str(root), "--backup-name", "missing.sqlite3", "--destination-name", "new.sqlite3",
        "--sha256", "0" * 64, "--policy-id", "test", "--policy-approved"]) == 1
    output = capsys.readouterr().out
    assert "missing.sqlite3" not in output and "no live service switch" in output


def recovery_workspace():
    from tests.test_recovery_jobs import workspace
    service, jobs = workspace()
    actor = Principal("teacher", "teacher", frozenset({"s1"}))
    return service, jobs, actor


def test_bounded_worker_completes_once_without_auto_signing():
    service, jobs, actor = recovery_workspace()
    try:
        record = run_worker_batch(service.store, actor, "s1", {"MATH.G7.EQ.SOLVE": []}, max_jobs=1)
        assert record["completed"] == 1 and record["processed"] == 1
        assert record["proposals_never_auto_signed"]
        assert service.store.conn.execute("SELECT status FROM plan_versions").fetchone()[0] == "proposed"
        assert run_worker_batch(service.store, actor, "s1", {}, max_jobs=1)["processed"] == 0
    finally:
        service.store.close()


def test_worker_fault_records_safe_retry_and_attempt_budget_then_resumes():
    service, jobs, actor = recovery_workspace()
    try:
        failed = run_worker_batch(service.store, actor, "s1", {}, max_attempts=1,
            fault=lambda _: (_ for _ in ()).throw(RuntimeError("private-secret")))
        assert failed["failed"] == 1 and "private-secret" not in json.dumps(failed)
        assert service.store.conn.execute("SELECT COUNT(*) FROM plan_versions").fetchone()[0] == 0
        assert run_worker_batch(service.store, actor, "s1", {}, max_attempts=1)["processed"] == 0
        assert run_worker_batch(service.store, actor, "s1", {}, max_attempts=2)["completed"] == 1
    finally:
        service.store.close()


def test_worker_deadline_privacy_and_assignment_fail_closed():
    service, jobs, actor = recovery_workspace()
    try:
        ticks = iter([0, 20, 20])
        report = run_worker_batch(service.store, actor, "s1", {}, deadline_seconds=1, clock=lambda: next(ticks))
        assert report["stop_reason"] == "deadline" and report["processed"] == 0
        with pytest.raises(AuthorizationDenied):
            run_worker_batch(service.store, actor, "foreign", {})
        GovernanceService(service.store).change_privacy(
            Principal("learner", "learner", frozenset({"s1"})), "s1", "withdraw", reason_code="requested")
        with pytest.raises(ConsentDenied):
            run_worker_batch(service.store, actor, "s1", {})
    finally:
        service.store.close()


@pytest.mark.parametrize("budget", [{"max_jobs": 0}, {"max_jobs": 101}, {"max_attempts": 0}, {"deadline_seconds": 61}])
def test_worker_budget_bounds_reject_before_changes(workspace, budget):
    _, store, _ = workspace
    with pytest.raises(ValueError):
        run_worker_batch(store, OFFICER, "student", {}, **budget)
