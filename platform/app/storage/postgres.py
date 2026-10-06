"""PostgreSQL archive adapter for evidence/state backups.

This is intentionally an archive boundary: SQLite Store remains the runtime
source. The adapter owns its schema, idempotent evidence identity, deterministic
export/import and checksum verification. Restore targets offline evidence only.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from app.learning.schema import LearningEvidence
from app.learning.service import LearningService, _hash

ARCHIVE_SCHEMA_VERSION = "evidence-archive-v1"


def stable_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


DDL = """
CREATE TABLE IF NOT EXISTS eduagent_archive_meta (
    schema_version TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS eduagent_evidence (
    learner_id TEXT NOT NULL, evidence_id TEXT NOT NULL, event_seq BIGINT NOT NULL,
    content_hash TEXT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL,
    PRIMARY KEY (learner_id, evidence_id), UNIQUE (learner_id, event_seq)
);
CREATE TABLE IF NOT EXISTS eduagent_learning_states (
    learner_id TEXT NOT NULL, kc_id TEXT NOT NULL, consumer TEXT NOT NULL,
    state_version INTEGER NOT NULL, payload JSONB NOT NULL,
    PRIMARY KEY (learner_id, kc_id, consumer)
);
CREATE TABLE IF NOT EXISTS eduagent_transitions (
    transition_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, event_seq BIGINT NOT NULL,
    consumer TEXT NOT NULL, payload JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS eduagent_archive_receipts (
    learner_id TEXT NOT NULL, operation_id TEXT NOT NULL, content_hash TEXT NOT NULL,
    payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (learner_id, operation_id)
);
"""


class PostgresArchive:
    def __init__(self, dsn: str, *, connect_timeout: float = 5):
        self.dsn, self.connect_timeout = dsn, connect_timeout

    @contextmanager
    def connection(self):
        with psycopg.connect(self.dsn, connect_timeout=self.connect_timeout, row_factory=dict_row) as conn:
            yield conn

    def bootstrap(self) -> None:
        with self.connection() as conn:
            conn.execute(DDL)
            conn.execute("INSERT INTO eduagent_archive_meta(schema_version) VALUES (%s) ON CONFLICT DO NOTHING",
                         (ARCHIVE_SCHEMA_VERSION,))

    def health(self) -> dict:
        with self.connection() as conn:
            row = conn.execute("SELECT current_database() AS database, version() AS version").fetchone()
            schema = conn.execute("SELECT schema_version FROM eduagent_archive_meta ORDER BY created_at DESC LIMIT 1").fetchone()
            return {"ok": True, "database": row["database"], "server_version": row["version"],
                    "schema_version": schema["schema_version"] if schema else None}

    def export_from_sqlite(self, store, learner_id: str, *, as_of: datetime | None = None, fault=None) -> dict:
        """Read a fixed SQLite evidence/state set and insert it idempotently."""
        if as_of is not None:
            raise ValueError("Historical archive export is not supported; use learning replay for as_of state")
        with store.lock:
            LearningService(store)._authorize(learner_id)
            query = "SELECT payload,event_seq FROM learning_evidence WHERE learner_id=? ORDER BY event_seq"
            selected = []
            for row in store.conn.execute(query, (learner_id,)).fetchall():
                evidence = LearningEvidence.model_validate_json(row["payload"])
                if as_of is None or evidence.occurred_at <= as_of:
                    selected.append((evidence, int(row["event_seq"])))
            states = [json.loads(row["payload"]) for row in store.conn.execute(
                "SELECT payload FROM learning_states WHERE learner_id=? ORDER BY kc_id,consumer", (learner_id,))]
            transitions = [json.loads(row["payload"]) for row in store.conn.execute(
                "SELECT payload FROM learning_transitions WHERE learner_id=? ORDER BY event_seq,rowid", (learner_id,))]
        evidence_payload = [{"evidence": e.model_dump(mode="json"), "event_seq": seq} for e, seq in selected]
        operation_payload = {"learner_id": learner_id, "as_of": as_of.isoformat() if as_of else None,
                             "evidence": evidence_payload, "states": states, "transitions": transitions}
        operation_id = f"export:{learner_id}:{payload_hash(operation_payload)}"
        digest = payload_hash(operation_payload)
        checkpoint = fault or (lambda _: None)
        self.bootstrap()
        with self.connection() as conn:
            # Serialize each learner across independent processes/connections.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (learner_id,))
            prior = conn.execute("SELECT content_hash,payload FROM eduagent_archive_receipts WHERE learner_id=%s AND operation_id=%s",
                                 (learner_id, operation_id)).fetchone()
            if prior:
                if prior["content_hash"] != digest:
                    raise ValueError("archive operation identity conflict")
                with store.lock:
                    LearningService(store)._authorize(learner_id)
                    return prior["payload"]
            for record in evidence_payload:
                evidence = record["evidence"]
                existing = conn.execute("SELECT content_hash,event_seq FROM eduagent_evidence WHERE learner_id=%s AND evidence_id=%s FOR UPDATE",
                                        (learner_id, evidence["evidence_id"])).fetchone()
                if existing and (existing["content_hash"] != payload_hash(evidence) or existing["event_seq"] != record["event_seq"]):
                    raise ValueError("PostgreSQL evidence identity conflict")
                conn.execute("""INSERT INTO eduagent_evidence
                    (learner_id,evidence_id,event_seq,content_hash,occurred_at,payload)
                    VALUES (%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (learner_id,evidence_id) DO UPDATE SET
                    event_seq=EXCLUDED.event_seq, content_hash=EXCLUDED.content_hash,
                    occurred_at=EXCLUDED.occurred_at,payload=EXCLUDED.payload
                    WHERE eduagent_evidence.content_hash=EXCLUDED.content_hash""",
                    (learner_id, evidence["evidence_id"], record["event_seq"], payload_hash(evidence),
                     evidence["occurred_at"], stable_json(evidence)))
                checkpoint("evidence")
            for state in states:
                consumer = "mastery" if "p_mastery" in state else "retention"
                existing = conn.execute("SELECT state_version,payload FROM eduagent_learning_states WHERE learner_id=%s AND kc_id=%s AND consumer=%s",
                                        (learner_id, state["kc_id"], consumer)).fetchone()
                if existing and existing["state_version"] == state["state_version"] and existing["payload"] != state:
                    raise ValueError("PostgreSQL state version identity conflict")
                conn.execute("""INSERT INTO eduagent_learning_states
                    (learner_id,kc_id,consumer,state_version,payload) VALUES (%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (learner_id,kc_id,consumer) DO UPDATE SET
                    state_version=EXCLUDED.state_version,payload=EXCLUDED.payload
                    WHERE eduagent_learning_states.state_version <= EXCLUDED.state_version""",
                    (learner_id, state["kc_id"], consumer,
                     state["state_version"], stable_json(state)))
                checkpoint("state")
            for transition in transitions:
                existing = conn.execute("SELECT payload FROM eduagent_transitions WHERE transition_id=%s", (transition["transition_id"],)).fetchone()
                if existing and existing["payload"] != transition:
                    raise ValueError("PostgreSQL transition identity conflict")
                conn.execute("INSERT INTO eduagent_transitions(transition_id,learner_id,event_seq,consumer,payload) VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING",
                             (transition["transition_id"], learner_id, transition["event_seq"], transition["consumer"], stable_json(transition)))
                checkpoint("transition")
            result = {"operation_id": operation_id, "learner_id": learner_id,
                      "evidence_count": len(evidence_payload), "state_count": len(states),
                      "transition_count": len(transitions), "content_hash": digest,
                      "schema_version": ARCHIVE_SCHEMA_VERSION}
            conn.execute("INSERT INTO eduagent_archive_receipts(learner_id,operation_id,content_hash,payload) VALUES (%s,%s,%s,%s::jsonb)",
                         (learner_id, operation_id, digest, stable_json(result)))
            checkpoint("receipt")
            with store.lock:
                LearningService(store)._authorize(learner_id)
                conn.commit()
            return result

    def evidence(self, learner_id: str) -> list[LearningEvidence]:
        self.bootstrap()
        with self.connection() as conn:
            rows = conn.execute("SELECT content_hash,payload FROM eduagent_evidence WHERE learner_id=%s ORDER BY event_seq", (learner_id,)).fetchall()
            if any(payload_hash(row["payload"]) != row["content_hash"] for row in rows):
                raise ValueError("Archive checksum mismatch")
            return [LearningEvidence.model_validate(row["payload"]) for row in rows]

    def restore_to_sqlite(self, store, learner_id: str, *, operation_id: str) -> dict:
        """Restore only evidence facts into a fresh/empty SQLite archive consumer.

        State snapshots remain independently rebuildable; this function never
        overwrites a non-empty learner's evidence or derived state.
        """
        self.bootstrap()
        with self.connection() as conn:
            rows = conn.execute("SELECT evidence_id,event_seq,content_hash,payload FROM eduagent_evidence WHERE learner_id=%s ORDER BY event_seq", (learner_id,)).fetchall()
        inserted = 0
        with store.lock:
            store.conn.execute("BEGIN IMMEDIATE")
            try:
                nonempty = store.conn.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0]
                if nonempty:
                    existing_rows = store.conn.execute("SELECT evidence_id FROM learning_evidence").fetchall()
                    if set(row[0] for row in existing_rows) != set(row["evidence_id"] for row in rows):
                        raise ValueError("Restore requires an empty SQLite target or the identical restored evidence set")
                for row in rows:
                    evidence = LearningEvidence.model_validate(row["payload"])
                    if payload_hash(evidence.model_dump(mode="json")) != row["content_hash"]:
                        raise ValueError("Archive checksum mismatch")
                    existing = store.conn.execute("SELECT content_hash FROM learning_evidence WHERE evidence_id=?", (row["evidence_id"],)).fetchone()
                    if existing and existing[0] != _hash(evidence):
                        raise ValueError("SQLite restore evidence identity conflict")
                    if not existing:
                        store.conn.execute("INSERT INTO learning_evidence(evidence_id,learner_id,event_seq,content_hash,payload) VALUES (?,?,?,?,?)",
                                           (evidence.evidence_id, learner_id, row["event_seq"], _hash(evidence), evidence.model_dump_json()))
                        inserted += 1
                if rows:
                    store.conn.execute("INSERT OR IGNORE INTO event_clock(seq) VALUES (?)", (max(row["event_seq"] for row in rows),))
                store.conn.commit()
            except Exception:
                store.conn.rollback()
                raise
        return {"operation_id": operation_id, "learner_id": learner_id, "inserted_evidence": inserted,
                "available_evidence": len(rows), "schema_version": ARCHIVE_SCHEMA_VERSION}
