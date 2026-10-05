"""SQLite 存储层。events 与 snapshots 严格 append-only（docs/06 §2.1）。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.core.schema import (
    GoalContract, InteractionEvent, MentalStateSnapshot, PlanVersion, Verdict, utcnow,
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
"""


def _dump(model) -> str:
    return model.model_dump_json() if hasattr(model, "model_dump_json") else json.dumps(model, ensure_ascii=False)


class Store:
    def __init__(self, db_path: str = ":memory:"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # ---- learners ----
    def ensure_learner(self, learner_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO learners VALUES (?, ?)", (learner_id, utcnow().isoformat())
        )
        self.conn.commit()

    # ---- contracts / plan versions ----
    def save_contract(self, contract: GoalContract) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO goal_contracts VALUES (?, ?, ?, ?)",
            (contract.goal_contract_id, contract.learner_id, contract.status, _dump(contract)),
        )
        self.conn.commit()

    def get_contract(self, contract_id: str) -> GoalContract | None:
        row = self.conn.execute(
            "SELECT payload FROM goal_contracts WHERE goal_contract_id=?", (contract_id,)
        ).fetchone()
        return GoalContract.model_validate_json(row["payload"]) if row else None

    def latest_contract(self, learner_id: str) -> GoalContract | None:
        row = self.conn.execute(
            "SELECT payload FROM goal_contracts WHERE learner_id=? ORDER BY rowid DESC LIMIT 1",
            (learner_id,),
        ).fetchone()
        return GoalContract.model_validate_json(row["payload"]) if row else None

    def save_plan_version(self, plan: PlanVersion) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO plan_versions VALUES (?, ?, ?, ?)",
            (plan.version_id, plan.goal_contract_id, plan.status, _dump(plan)),
        )
        self.conn.commit()

    def save_plan_update(self, version_id: str, content: str,
                         status: str = "draft") -> None:
        """智能体运行时的计划版本（轻量记录；契约级 PLAN_VERSION 走 save_plan_version）。"""
        self.conn.execute(
            "INSERT OR REPLACE INTO plan_versions VALUES (?, ?, ?, ?)",
            (version_id, "agent-unit", status,
             json.dumps({"content": content}, ensure_ascii=False)),
        )
        self.conn.commit()

    # ---- sessions ----
    def save_session(self, session_id: str, learner_id: str, contract_id: str | None,
                     session_type: str, status: str = "active") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, learner_id, contract_id, session_type, status, utcnow().isoformat()),
        )
        self.conn.commit()

    # ---- events（append-only：只提供写入与读取，不提供修改/删除） ----
    def append_event(self, event: InteractionEvent) -> None:
        self.conn.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
            (event.event_id, event.learner_pseudo_id, event.session_id,
             event.ts.isoformat(), _dump(event)),
        )
        self.conn.commit()

    def append_events(self, events: list[InteractionEvent]) -> None:
        for event in events:
            self.append_event(event)

    def events_for_learner(self, learner_id: str, limit: int = 500) -> list[InteractionEvent]:
        rows = self.conn.execute(
            "SELECT payload FROM events WHERE learner_pseudo_id=? ORDER BY ts LIMIT ?",
            (learner_id, limit),
        ).fetchall()
        return [InteractionEvent.model_validate_json(r["payload"]) for r in rows]

    def get_event(self, event_id: str) -> InteractionEvent | None:
        row = self.conn.execute(
            "SELECT payload FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        return InteractionEvent.model_validate_json(row["payload"]) if row else None

    # ---- snapshots（append-only） ----
    def append_snapshot(self, snapshot: MentalStateSnapshot) -> None:
        self.conn.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?)",
            (snapshot.snapshot_id, snapshot.learner_id,
             snapshot.created_at.isoformat(), _dump(snapshot)),
        )
        self.conn.commit()

    def latest_snapshot(self, learner_id: str) -> MentalStateSnapshot | None:
        row = self.conn.execute(
            "SELECT payload FROM snapshots WHERE learner_id=? "
            "ORDER BY ts DESC, rowid DESC LIMIT 1",
            (learner_id,),
        ).fetchone()
        return MentalStateSnapshot.model_validate_json(row["payload"]) if row else None

    def snapshots_for_learner(self, learner_id: str, limit: int = 20) -> list[MentalStateSnapshot]:
        rows = self.conn.execute(
            "SELECT payload FROM snapshots WHERE learner_id=? "
            "ORDER BY ts DESC, rowid DESC LIMIT ?",
            (learner_id, limit),
        ).fetchall()
        return [MentalStateSnapshot.model_validate_json(r["payload"])
                for r in reversed(rows)]

    # ---- verdicts ----
    def save_verdict(self, verdict: Verdict, session_id: str, item_id: str | None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO verdicts VALUES (?, ?, ?, ?, ?, ?)",
            (verdict.artifact_id or new_id(), session_id, item_id,
             verdict.status, verdict.score, _dump(verdict)),
        )
        self.conn.commit()

    # ---- digests ----
    def save_digest(self, payload: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO digests (created_at, payload) VALUES (?, ?)",
            (utcnow().isoformat(), json.dumps(payload, ensure_ascii=False)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def latest_digest(self) -> dict | None:
        row = self.conn.execute(
            "SELECT payload FROM digests ORDER BY digest_id DESC LIMIT 1"
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    # ---- 原始查询（供 L0 汇总） ----
    def raw_event_payloads(self) -> list[dict]:
        rows = self.conn.execute("SELECT payload FROM events ORDER BY ts").fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def raw_verdict_payloads(self) -> list[dict]:
        rows = self.conn.execute("SELECT payload, item_id FROM verdicts ORDER BY rowid").fetchall()
        out = []
        for r in rows:
            d = json.loads(r["payload"])
            d["item_id"] = r["item_id"]   # item_id 在表列上，注入 payload 供 L0 聚合
            out.append(d)
        return out


def new_id() -> str:
    from app.core.schema import new_id as _new

    return _new()
