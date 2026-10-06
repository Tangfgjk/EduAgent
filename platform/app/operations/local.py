"""Explicit local backups, quarantined restores and bounded correction worker."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable

from app.core.schema import utcnow
from app.governance.authority import AuthorizationDenied, Principal
from app.learning.recovery import RecoveryJobs
from app.learning.service import ConsentDenied, LearningService


@dataclass(frozen=True)
class BackupPolicy:
    policy_id: str = "unreviewed"
    approved: bool = False


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _target(root: Path, name: str):
    root = root.resolve()
    if not root.is_dir() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.sqlite3", name):
        raise ValueError("Existing trusted root and simple .sqlite3 filename required")
    target = (root / name).resolve()
    if target.parent != root:
        raise ValueError("Target must stay inside operator-provisioned root")
    return target


def _policy(policy):
    if not policy.approved or not policy.policy_id or policy.policy_id == "unreviewed":
        raise AuthorizationDenied("Operator-approved local backup retention policy required")


def _subjects(connection):
    subjects = {row[0] for row in connection.execute("SELECT learner_id FROM learners")}
    for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        table = row[0].replace('"', '""')
        columns = {column[1] for column in connection.execute(f'PRAGMA table_info("{table}")')}
        for column in ("learner_id", "learner_pseudo_id"):
            if column in columns:
                subjects.update(value[0] for value in connection.execute(f'SELECT DISTINCT "{column}" FROM "{table}"'))
    return subjects


def backup_online(store, principal: Principal, learner_id: str, *, trusted_root: Path,
                  filename: str, policy: BackupPolicy, fault: Callable[[str], None] | None = None):
    """Consistent sqlite backup under lock; never copy unconsented/foreign subjects."""
    principal.require(learner_id, "backup")
    _policy(policy)
    target = _target(trusted_root, filename)
    manifest = target.with_suffix(".sqlite3.manifest.json")
    checkpoint = fault or (lambda _: None)
    created = []
    copied = None
    with store.lock:
        LearningService(store)._authorize(learner_id)
        if store.conn.in_transaction:
            raise RuntimeError("Backup requires a committed source snapshot")
        subjects = _subjects(store.conn)
        if subjects - {learner_id}:
            raise AuthorizationDenied("Scoped backup refuses foreign or unassigned learner records")
        try:
            with target.open("xb"):
                pass
            created.append(target)
            with manifest.open("xb"):
                pass
            created.append(manifest)
            checkpoint("reserved")
            copied = sqlite3.connect(target)
            store.conn.backup(copied)
            checkpoint("copied")
            if copied.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup integrity check failed")
            copied.close()
            copied = None
            record = dict(format="eduagent-sqlite-backup-v1", sha256=_sha(target),
                integrity_check="ok", created_at=utcnow().isoformat(), policy_id=policy.policy_id,
                consistent_online_snapshot=True, encrypted=False, compliance_claim=False)
            manifest.write_text(json.dumps(record, sort_keys=True, indent=2), encoding="utf-8")
            checkpoint("manifest")
            return record
        except Exception:
            if copied is not None:
                copied.close()
            # Remove only this invocation's exclusively reserved files.
            for path in reversed(created):
                path.unlink(missing_ok=True)
            raise


def restore_new_quarantined(principal: Principal, learner_id: str, *, trusted_root: Path,
                            backup_name: str, destination_name: str, expected_sha256: str,
                            policy: BackupPolicy, fault: Callable[[str], None] | None = None):
    """Restore to a NEW staged DB only; all identities are quarantined by default."""
    principal.require(learner_id, "backup")
    _policy(policy)
    source = _target(trusted_root, backup_name)
    target = _target(trusted_root, destination_name)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or not source.is_file() or _sha(source) != expected_sha256:
        raise ValueError("Trusted backup hash mismatch or missing backup")
    checkpoint = fault or (lambda _: None)
    original = copied = None
    created = False
    try:
        with target.open("xb"):
            pass
        created = True
        original = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        if original.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup SQLite integrity check failed")
        subjects = _subjects(original)
        if subjects - {learner_id}:
            raise AuthorizationDenied("Restore refuses a foreign-subject backup")
        copied = sqlite3.connect(target)
        original.backup(copied)
        original.close()
        original = None
        checkpoint("copied")
        # Recheck the source bytes to catch a changed snapshot while copying.
        if _sha(source) != expected_sha256:
            raise ValueError("Backup changed while being restored")
        copied.execute("CREATE TABLE IF NOT EXISTS governance_privacy(learner_id TEXT PRIMARY KEY,state TEXT NOT NULL,payload TEXT NOT NULL)")
        for subject in subjects:
            old = copied.execute("SELECT state FROM governance_privacy WHERE learner_id=?", (subject,)).fetchone()
            if old and old[0] == "deleted":
                continue
            record = dict(learner_id=subject, state="quarantined", operation="backup_restore",
                reason_code="fresh_consent_and_policy_review_required", policy_id=policy.policy_id,
                at=utcnow().isoformat(), compliance_claim=False)
            copied.execute("INSERT INTO governance_privacy VALUES (?,?,?) ON CONFLICT(learner_id) DO UPDATE SET state=excluded.state,payload=excluded.payload", (subject, "quarantined", json.dumps(record)))
            version = "restore-" + utcnow().isoformat()
            consent = dict(learner_id=subject, scopes=[], version=version, source="local-backup-restore", at=utcnow().isoformat())
            copied.execute("INSERT INTO consent_records VALUES (?,?,?)", (subject, version, json.dumps(consent)))
        copied.commit()
        checkpoint("quarantined")
        if copied.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Restored SQLite integrity check failed")
        copied.close()
        copied = None
        return dict(format="eduagent-quarantined-restore-v1", source_sha256=expected_sha256,
            restored_sha256=_sha(target), integrity_check="ok", quarantined=True,
            live_service_switched=False, compliance_claim=False)
    except Exception:
        if original is not None:
            original.close()
        if copied is not None:
            copied.close()
        if created:
            target.unlink(missing_ok=True)
        raise


def health_report(connection):
    """Read-only aggregate health; no names, IDs, evidence, keys or error details."""
    integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    jobs = {}
    if "correction_jobs" in tables:
        for row in connection.execute("SELECT status,COUNT(*) FROM correction_jobs GROUP BY status"):
            status = row[0] if row[0] in {"pending", "failed", "completed"} else "unknown"
            jobs[status] = jobs.get(status, 0) + row[1]
    return dict(ok=integrity == "ok", integrity_check="ok" if integrity == "ok" else "failed",
        sqlite_version=sqlite3.sqlite_version, table_count=len(tables), correction_job_counts=jobs,
        privacy_lifecycle_enabled="governance_privacy" in tables,
        continuous_monitoring=False, production_readiness_claim=False)


def run_worker_batch(store, principal: Principal, learner_id: str, graph, *, max_jobs: int = 10,
                     max_attempts: int = 3, deadline_seconds: float = 10,
                     clock=time.monotonic, fault=None):
    principal.require(learner_id, "worker")
    if not 1 <= max_jobs <= 100 or not 1 <= max_attempts <= 10 or not 0 < deadline_seconds <= 60:
        raise ValueError("Worker budgets out of bounds")
    start = clock()
    recovery = RecoveryJobs(store)
    with store.lock:
        LearningService(store)._authorize(learner_id)
        eligible = [json.loads(row[0]) for row in store.conn.execute(
            "SELECT payload FROM correction_jobs WHERE learner_id=? AND status IN ('pending','failed') AND json_extract(payload,'$.attempts')<? ORDER BY rowid LIMIT ?",
            (learner_id, max_attempts, max_jobs)).fetchall()]
    completed = failed = blocked = processed = 0
    stopped = "batch_limit"
    for job in eligible:
        if clock() - start >= deadline_seconds:
            stopped = "deadline"
            break
        try:
            LearningService(store)._authorize(learner_id)
            recovery.run(learner_id, job["job_id"], graph, fault=fault)
            completed += 1
        except ConsentDenied:
            blocked += 1
            stopped = "privacy_boundary"
            break
        except Exception:
            failed += 1
        processed += 1
    return dict(processed=processed, completed=completed, failed=failed, blocked=blocked,
        max_jobs=max_jobs, max_attempts=max_attempts, stop_reason=stopped,
        elapsed_seconds=round(clock() - start, 6), proposals_never_auto_signed=True)
