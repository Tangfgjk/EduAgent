"""Durable local lab receipts with an OS-released SQLite single-owner lock."""
import json
from pathlib import Path
import sqlite3
from threading import Lock


class DurableLabJobs:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.lock = Lock()
        self.owner = self.connection = None
        for name in ("lab-owner.sqlite3", "lab-jobs.sqlite3"):
            path = self.root / name
            if path.is_symlink() or path.resolve().parent != self.root:
                raise ValueError("Lab database must remain inside trusted output root")
        try:
            # Separate database: the exclusive lock does not block job persistence.
            # A process crash releases the lock automatically; no stale PID guessing.
            self.owner = sqlite3.connect(self.root / "lab-owner.sqlite3", timeout=0, check_same_thread=False)
            self.owner.execute("BEGIN EXCLUSIVE")
            self.connection = sqlite3.connect(self.root / "lab-jobs.sqlite3", check_same_thread=False)
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            self.connection.commit()
        except Exception:
            self.close()
            raise RuntimeError("Lab output root already owned or unavailable") from None

    def put(self, job):
        payload = {key: value for key, value in job.items() if key != "stop_event"}
        with self.lock, self.connection:
            self.connection.execute("INSERT INTO jobs VALUES (?,?) ON CONFLICT(job_id) DO UPDATE SET payload=excluded.payload",
                (job["job_id"], json.dumps(payload, ensure_ascii=False)))

    def get(self, job_id):
        with self.lock:
            row = self.connection.execute("SELECT payload FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def recent(self, limit=20):
        with self.lock:
            rows = self.connection.execute("SELECT payload FROM jobs ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

    def recover(self):
        """Interrupted execution is explicit, never silently restarted as a new learner."""
        with self.lock:
            rows = self.connection.execute("SELECT payload FROM jobs").fetchall()
        for row in rows:
            job = json.loads(row[0])
            if job["state"] not in {"running", "stopping"}:
                continue
            job.update(state="interrupted", error="process_interrupted", report=None)
            run_id = job.get("run_id") or job.get("resume_run_id")
            if run_id:
                path = (self.root / run_id).resolve()
                if path.parent == self.root and (path / "simulation.marker").is_file():
                    report = path / "report.json"
                    if report.is_file():
                        try:
                            payload = json.loads(report.read_text(encoding="utf-8"))
                            if payload.get("status") in {"completed", "stopped", "failed", "timed_out", "unsupported"}:
                                job.update(report=payload, state=payload["status"], error=None)
                        except ValueError:
                            pass
                    if job["state"] == "interrupted" and (path / "checkpoint.json").is_file():
                        job.update(run_id=run_id, report={"status": "interrupted", "resumable": True,
                            "source_kind": "synthetic_ai_generated", "recovery_requires_version_checks": True})
            self.put(job)

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.owner is not None:
            self.owner.rollback()
            self.owner.close()
            self.owner = None
