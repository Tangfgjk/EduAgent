"""SQLite 存储层。events 与 snapshots 严格 append-only（docs/06 §2.1）。"""
from __future__ import annotations

import json
import sqlite3
import threading
from functools import wraps
from pathlib import Path

from app.storage.migrations import migrate

from app.core.schema import (
    GoalContract, InteractionEvent, MentalStateSnapshot, PlanVersion, Verdict, new_id, utcnow,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS learners (
    learner_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goal_contracts (
    goal_contract_id TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_versions (
    version_id TEXT PRIMARY KEY,
    goal_contract_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL,
    contract_id TEXT,
    session_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    learner_pseudo_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verdicts (
    verdict_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    item_id TEXT,
    status TEXT NOT NULL,
    score REAL NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS digests (
    digest_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_clock (seq INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE IF NOT EXISTS learning_evidence (
    evidence_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL,
    event_seq INTEGER NOT NULL UNIQUE, content_hash TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learning_states (
    learner_id TEXT NOT NULL, kc_id TEXT NOT NULL, consumer TEXT NOT NULL,
    version INTEGER NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (learner_id, kc_id, consumer)
);
CREATE TABLE IF NOT EXISTS evidence_consumption (
    learner_id TEXT NOT NULL, evidence_id TEXT NOT NULL, consumer TEXT NOT NULL,
    payload TEXT NOT NULL, PRIMARY KEY (learner_id, evidence_id, consumer)
);
CREATE TABLE IF NOT EXISTS learning_transitions (
    transition_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, event_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learning_rebuilds (
    rebuild_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consent_records (
    learner_id TEXT NOT NULL, version TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (learner_id, version)
);
CREATE TABLE IF NOT EXISTS learning_audit (
    audit_seq INTEGER PRIMARY KEY AUTOINCREMENT, learner_id TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learning_api_receipts (
    learner_id TEXT NOT NULL, attempt_id TEXT NOT NULL, content_hash TEXT NOT NULL,
    payload TEXT NOT NULL, PRIMARY KEY(learner_id,attempt_id)
);
CREATE TABLE IF NOT EXISTS correction_jobs (
    job_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learning_appeals (
    appeal_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, payload TEXT NOT NULL
);
"""


def _dump(model) -> str:
    return model.model_dump_json() if hasattr(model, "model_dump_json") else json.dumps(model, ensure_ascii=False)


def _synchronized(method):
    """Use the service's shared RLock; nested Store reads remain reentrant."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return guarded


class Store:
    def __init__(self, db_path: str = ":memory:"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.lock = threading.RLock()
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        try:
            self.schema_version = migrate(self.conn, _SCHEMA)
        except Exception:
            self.conn.close()
            raise

    @_synchronized
    def close(self) -> None:
        self.conn.close()

    # ---- learners ----
    @_synchronized
    def ensure_learner(self, learner_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO learners VALUES (?, ?)", (learner_id, utcnow().isoformat())
        )
        self.conn.commit()

    # ---- contracts / plan versions ----
    @_synchronized
    def save_contract(self, contract: GoalContract) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO goal_contracts VALUES (?, ?, ?, ?)",
            (contract.goal_contract_id, contract.learner_id, contract.status, _dump(contract)),
        )
        self.conn.commit()

    @_synchronized
    def get_contract(self, contract_id: str) -> GoalContract | None:
        row = self.conn.execute(
            "SELECT payload FROM goal_contracts WHERE goal_contract_id=?", (contract_id,)
        ).fetchone()
        return GoalContract.model_validate_json(row["payload"]) if row else None

    @_synchronized
    def latest_contract(self, learner_id: str) -> GoalContract | None:
        row = self.conn.execute(
            "SELECT payload FROM goal_contracts WHERE learner_id=? ORDER BY rowid DESC LIMIT 1",
            (learner_id,),
        ).fetchone()
        return GoalContract.model_validate_json(row["payload"]) if row else None

    @_synchronized
    def save_plan_version(self, plan: PlanVersion) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO plan_versions VALUES (?, ?, ?, ?)",
            (plan.version_id, plan.goal_contract_id, plan.status, _dump(plan)),
        )
        self.conn.commit()

    # ---- sessions ----
    @_synchronized
    def save_session(self, session_id: str, learner_id: str, contract_id: str | None,
                     session_type: str, status: str = "active") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, learner_id, contract_id, session_type, status, utcnow().isoformat()),
        )
        self.conn.commit()

    # ---- events（append-only：只提供写入与读取，不提供修改/删除） ----
    @_synchronized
    def append_event(self, event: InteractionEvent) -> None:
        seq = self.conn.execute("INSERT INTO event_clock DEFAULT VALUES").lastrowid
        event.event_seq = int(seq)
        self.conn.execute(
            "INSERT INTO events (event_id,learner_pseudo_id,session_id,ts,payload,event_seq) VALUES (?, ?, ?, ?, ?, ?)",
            (event.event_id, event.learner_pseudo_id, event.session_id,
             event.ts.isoformat(), _dump(event), seq),
        )
        self.conn.commit()

    @_synchronized
    def append_events(self, events: list[InteractionEvent]) -> None:
        for event in events:
            self.append_event(event)

    @_synchronized
    def events_for_learner(self, learner_id: str, limit: int = 500) -> list[InteractionEvent]:
        rows = self.conn.execute(
            "SELECT payload FROM events WHERE learner_pseudo_id=? ORDER BY event_seq LIMIT ?",
            (learner_id, limit),
        ).fetchall()
        return [InteractionEvent.model_validate_json(r["payload"]) for r in rows]

    @_synchronized
    def get_event(self, event_id: str) -> InteractionEvent | None:
        row = self.conn.execute(
            "SELECT payload FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        return InteractionEvent.model_validate_json(row["payload"]) if row else None

    # ---- snapshots（append-only） ----
    @_synchronized
    def append_snapshot(self, snapshot: MentalStateSnapshot) -> None:
        self.conn.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?)",
            (snapshot.snapshot_id, snapshot.learner_id,
             snapshot.created_at.isoformat(), _dump(snapshot)),
        )
        self.conn.commit()

    @_synchronized
    def latest_snapshot(self, learner_id: str) -> MentalStateSnapshot | None:
        row = self.conn.execute(
            "SELECT payload FROM snapshots WHERE learner_id=? "
            "ORDER BY ts DESC, rowid DESC LIMIT 1",
            (learner_id,),
        ).fetchone()
        return MentalStateSnapshot.model_validate_json(row["payload"]) if row else None

    @_synchronized
    def snapshots_for_learner(self, learner_id: str, limit: int = 20) -> list[MentalStateSnapshot]:
        rows = self.conn.execute(
            "SELECT payload FROM snapshots WHERE learner_id=? "
            "ORDER BY ts DESC, rowid DESC LIMIT ?",
            (learner_id, limit),
        ).fetchall()
        return [MentalStateSnapshot.model_validate_json(r["payload"])
                for r in reversed(rows)]

    # ---- verdicts ----
    @_synchronized
    def save_verdict(self, verdict: Verdict, session_id: str, item_id: str | None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO verdicts VALUES (?, ?, ?, ?, ?, ?)",
            (verdict.artifact_id or new_id(), session_id, item_id,
             verdict.status, verdict.score, _dump(verdict)),
        )
        self.conn.commit()

    # ---- digests ----
    @_synchronized
    def save_digest(self, payload: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO digests (created_at, payload) VALUES (?, ?)",
            (utcnow().isoformat(), json.dumps(payload, ensure_ascii=False)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    @_synchronized
    def latest_digest(self) -> dict | None:
        row = self.conn.execute(
            "SELECT payload FROM digests ORDER BY digest_id DESC LIMIT 1"
        ).fetchone()
        return json.loads(row["payload"]) if row else None
