"""Real SQLite/PostgreSQL learning repositories; PostgreSQL is not an archive.

The unit of work owns authorization, facts, CAS projections, outbox and receipts.
This port covers the learning domain, not the SQLite SessionRuntime changeset.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from typing import Protocol

import psycopg
from psycopg import sql

from app.learning.service import ConsentDenied, EvidenceConflict


TABLES = frozenset({"consent_records", "learning_evidence", "learning_states", "learning_transitions",
                   "learning_rebuilds", "snapshots", "evidence_consumption", "learning_api_receipts",
                   "learning_audit", "correction_jobs"})
PG_DDL = """
CREATE TABLE IF NOT EXISTS repository_meta(version TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS learner_locks(learner_id TEXT PRIMARY KEY);
CREATE SEQUENCE IF NOT EXISTS event_clock;
CREATE TABLE IF NOT EXISTS consent_records(learner_id TEXT NOT NULL,version TEXT NOT NULL,
    record_seq BIGSERIAL UNIQUE,payload JSONB NOT NULL,PRIMARY KEY(learner_id,version));
CREATE TABLE IF NOT EXISTS learning_evidence(evidence_id TEXT PRIMARY KEY,learner_id TEXT NOT NULL,
    event_seq BIGINT NOT NULL UNIQUE,content_hash TEXT NOT NULL,payload JSONB NOT NULL);
CREATE INDEX IF NOT EXISTS evidence_learner ON learning_evidence(learner_id,event_seq);
CREATE TABLE IF NOT EXISTS learning_states(learner_id TEXT NOT NULL,kc_id TEXT NOT NULL,
    consumer TEXT NOT NULL CHECK(consumer IN ('mastery','retention')),version INTEGER NOT NULL,payload JSONB NOT NULL,
    PRIMARY KEY(learner_id,kc_id,consumer));
CREATE TABLE IF NOT EXISTS learning_transitions(transition_id TEXT PRIMARY KEY,learner_id TEXT NOT NULL,
    event_seq BIGINT NOT NULL,payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS learning_rebuilds(rebuild_id TEXT PRIMARY KEY,learner_id TEXT NOT NULL,payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(snapshot_id TEXT PRIMARY KEY,learner_id TEXT NOT NULL,ts TEXT NOT NULL,payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_consumption(learner_id TEXT NOT NULL,evidence_id TEXT NOT NULL,
    consumer TEXT NOT NULL,payload JSONB NOT NULL,PRIMARY KEY(learner_id,evidence_id,consumer));
CREATE TABLE IF NOT EXISTS learning_api_receipts(learner_id TEXT NOT NULL,attempt_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,payload JSONB NOT NULL,PRIMARY KEY(learner_id,attempt_id));
CREATE TABLE IF NOT EXISTS learning_audit(audit_seq BIGSERIAL PRIMARY KEY,learner_id TEXT NOT NULL,payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS correction_jobs(job_id TEXT PRIMARY KEY,learner_id TEXT NOT NULL,status TEXT NOT NULL,payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS governance_privacy(learner_id TEXT PRIMARY KEY,state TEXT NOT NULL,payload JSONB NOT NULL);
"""


class LearningRepository(Protocol):
    backend: str
    def transaction(self, learner_id: str): ...


class LearningTransaction:
    """Domain operations; callers never need dialect or raw connection access."""
    def __init__(self, conn, backend):
        self._conn, self.backend = conn, backend

    def _execute(self, statement, parameters=()):
        if self.backend == "postgres":
            statement = statement.replace("?", "%s")
        return self._conn.execute(statement, parameters)

    @staticmethod
    def _decode(value):
        return json.loads(value) if isinstance(value, str) else value

    def _payload(self, statement, parameters=()):
        row = self._execute(statement, parameters).fetchone()
        return self._decode(row[0]) if row else None

    def _payloads(self, statement, parameters=()):
        return [self._decode(row[0]) for row in self._execute(statement, parameters).fetchall()]

    def current_consent(self, learner):
        ordinal = "record_seq" if self.backend == "postgres" else "rowid"
        return self._payload(f"SELECT payload FROM consent_records WHERE learner_id=? ORDER BY {ordinal} DESC LIMIT 1", (learner,))

    def consent_version(self, learner, version):
        return self._payload("SELECT payload FROM consent_records WHERE learner_id=? AND version=?", (learner, version))

    def authorize(self, learner, purpose="teaching", evidence=None):
        if self.backend == "postgres":
            privacy_table = True
        else:
            privacy_table = self._execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_privacy'").fetchone()
        if privacy_table:
            row = self._execute("SELECT state FROM governance_privacy WHERE learner_id=?", (learner,)).fetchone()
            if row is not None and row[0] != "active":
                raise ConsentDenied("learner privacy lifecycle blocks data use")
        current = self.current_consent(learner)
        if current is None or purpose not in current["scopes"]:
            raise ConsentDenied(f"{purpose} consent required")
        if evidence is not None:
            collected = self.consent_version(learner, evidence.consent_version)
            if (collected is None or purpose not in collected["scopes"] or purpose not in evidence.consent_scope
                    or collected["source"] != evidence.authorization_source):
                raise ConsentDenied("collection consent mismatch")

    def append(self, table, values: dict):
        if table not in TABLES:
            raise ValueError("table not in learning repository allowlist")
        if not values or any(not re.fullmatch(r"[a-z_]+", key) for key in values):
            raise ValueError("invalid repository field")
        encoded = tuple(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                        if key == "payload" else value for key, value in values.items())
        placeholders = ["CAST(? AS JSONB)" if key == "payload" and self.backend == "postgres" else "?" for key in values]
        self._execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join(placeholders)})", encoded)

    def next_sequence(self):
        if self.backend == "postgres":
            return self._execute("SELECT nextval('event_clock')").fetchone()[0]
        return self._execute("INSERT INTO event_clock DEFAULT VALUES").lastrowid

    def evidence_identity(self, evidence_id):
        row = self._execute("SELECT content_hash,payload FROM learning_evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
        return (row[0], self._decode(row[1])) if row else None

    def evidence_history(self, learner):
        return self._payloads("SELECT payload FROM learning_evidence WHERE learner_id=? ORDER BY event_seq", (learner,))

    def state(self, learner, kc, consumer):
        return self._payload("SELECT payload FROM learning_states WHERE learner_id=? AND kc_id=? AND consumer=?", (learner, kc, consumer))

    def states(self, learner, consumer):
        return self._payloads("SELECT payload FROM learning_states WHERE learner_id=? AND consumer=? ORDER BY kc_id", (learner, consumer))

    def write_state(self, state, consumer, expected_version):
        if state.state_version != expected_version + 1:
            raise EvidenceConflict("state revision must increment exactly once")
        row = self._execute("SELECT version FROM learning_states WHERE learner_id=? AND kc_id=? AND consumer=?",
                            (state.learner_id, state.kc_id, consumer)).fetchone()
        if row is None:
            if expected_version != 0:
                raise EvidenceConflict("state CAS conflict")
            self.append("learning_states", dict(learner_id=state.learner_id, kc_id=state.kc_id,
                consumer=consumer, version=state.state_version, payload=state.model_dump(mode="json")))
        else:
            payload = json.dumps(state.model_dump(mode="json"), ensure_ascii=False)
            expression = "CAST(? AS JSONB)" if self.backend == "postgres" else "?"
            result = self._execute(f"UPDATE learning_states SET version=?,payload={expression} "
                "WHERE learner_id=? AND kc_id=? AND consumer=? AND version=?", (state.state_version, payload,
                state.learner_id, state.kc_id, consumer, expected_version))
            if result.rowcount != 1:
                raise EvidenceConflict("state CAS conflict")

    def consumed(self, learner, evidence_id):
        return self._payload("SELECT payload FROM evidence_consumption WHERE learner_id=? AND evidence_id=? AND consumer='mastery'", (learner, evidence_id))

    def transitions(self, learner):
        return self._payloads("SELECT payload FROM learning_transitions WHERE learner_id=? ORDER BY event_seq,transition_id", (learner,))

    def latest_snapshot(self, learner):
        return self._payload("SELECT payload FROM snapshots WHERE learner_id=? ORDER BY ts DESC,snapshot_id DESC LIMIT 1", (learner,))

    def receipt(self, learner, key):
        row = self._execute("SELECT content_hash,payload FROM learning_api_receipts WHERE learner_id=? AND attempt_id=?", (learner, key)).fetchone()
        return (row[0], self._decode(row[1])) if row else None

    def job(self, job_id):
        return self._payload("SELECT payload FROM correction_jobs WHERE job_id=?", (job_id,))


class SQLiteLearningRepository:
    backend = "sqlite"
    def __init__(self, store):
        self.store = store

    @contextmanager
    def transaction(self, learner_id):
        with self.store.lock:
            if self.store.conn.in_transaction:
                raise RuntimeError("learning repository transaction must own its boundary")
            self.store.conn.execute("BEGIN IMMEDIATE")
            try:
                yield LearningTransaction(self.store.conn, self.backend)
                self.store.conn.commit()
            except Exception:
                self.store.conn.rollback()
                raise


class PostgresLearningRepository:
    backend = "postgres"
    def __init__(self, dsn: str, *, schema: str = "eduagent_learning_v3", connect_timeout: int = 5):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            raise ValueError("invalid repository schema")
        self.dsn, self.schema, self.connect_timeout = dsn, schema, connect_timeout

    def bootstrap(self):
        with psycopg.connect(self.dsn, connect_timeout=self.connect_timeout) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {} ").format(sql.Identifier(self.schema)))
            conn.execute(sql.SQL("SET LOCAL search_path TO {} ").format(sql.Identifier(self.schema)))
            conn.execute(PG_DDL)
            existing = conn.execute("SELECT version FROM repository_meta").fetchall()
            if existing and {row[0] for row in existing} != {"learning-repository-v3.1"}:
                raise RuntimeError("repository schema version migration required")
            conn.execute("INSERT INTO repository_meta VALUES ('learning-repository-v3.1') ON CONFLICT DO NOTHING")

    @contextmanager
    def transaction(self, learner_id):
        if not learner_id:
            raise ValueError("learner-scoped transaction required")
        with psycopg.connect(self.dsn, connect_timeout=self.connect_timeout) as conn:
            conn.execute(sql.SQL("SET LOCAL search_path TO {} ").format(sql.Identifier(self.schema)))
            conn.execute("SET LOCAL lock_timeout='10s'")
            conn.execute("SET LOCAL statement_timeout='30s'")
            conn.execute("INSERT INTO learner_locks VALUES (%s) ON CONFLICT DO NOTHING", (learner_id,))
            conn.execute("SELECT learner_id FROM learner_locks WHERE learner_id=%s FOR UPDATE", (learner_id,))
            yield LearningTransaction(conn, self.backend)
