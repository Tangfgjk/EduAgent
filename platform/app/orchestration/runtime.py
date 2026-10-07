"""Local optimistic session transactions: prepare on a snapshot, commit a changeset.

SQLite backup is deliberately conservative for V1. No model or network work runs
under the production lock/transaction. Only allowlisted runtime tables are copied
back; unrelated data, schemas, facts and sequence rows are never replaced/deleted.
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable
from uuid import uuid4

from app.governance.service import GovernanceService
from app.governance.runtime_sources import RuntimeSources
from app.learning.assets import load_catalog
from app.learning.service import LearningService
from app.orchestration.session import TutorSession, load_bank
from app.storage.db import Store
from app.storage.trigger_state import PersistentTriggerState
from app.core.clock import CallableClock, SystemClock


class RuntimeConflict(ValueError):
    """The database/session changed while a turn was prepared; retry safely."""


TABLES = ("learners", "sessions", "event_clock", "verdicts", "learning_evidence", "learning_states",
          "evidence_consumption", "learning_transitions", "learning_rebuilds", "learning_audit",
          "events", "snapshots", "runtime_sessions", "gateway_receipts", "governance_reviews", "trigger_state")
MUTABLE = frozenset({"learning_states", "runtime_sessions", "trigger_state"})


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(text, answer):
    return hashlib.sha256(dump(dict(text=text, answer=answer)).encode()).hexdigest()


def rows(conn, table):
    columns = tuple(row["name"] for row in conn.execute(f"PRAGMA table_info({table})"))
    pk = tuple(row["name"] for row in sorted(conn.execute(f"PRAGMA table_info({table})"), key=lambda r: r["pk"]) if row["pk"])
    if not pk:
        raise RuntimeError("Runtime changeset table requires an explicit primary key")
    records = {tuple(row[key] for key in pk): tuple(row[c] for c in columns)
               for row in conn.execute(f"SELECT * FROM {table}")}
    return columns, pk, records


class SessionRuntime:
    def __init__(self, store: Store, llm, catalog=None, *, policy_factory=None, clock=None, bank_factory=None):
        self.store, self.llm, self.catalog = store, llm, catalog or load_catalog()
        self.policy_factory, self.clock = policy_factory, clock
        self.bank_factory = bank_factory or load_bank
        GovernanceService(store)
        with store.lock:
            store.conn.execute("CREATE TABLE IF NOT EXISTS runtime_sessions (session_id TEXT PRIMARY KEY,learner_id TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL)")
            store.conn.execute("CREATE TABLE IF NOT EXISTS gateway_receipts (session_id TEXT,attempt_id TEXT,content_hash TEXT,payload TEXT,PRIMARY KEY(session_id,attempt_id))")
            store.conn.commit()

    def _token(self):
        return self.store.conn.total_changes, self.store.conn.execute("PRAGMA data_version").fetchone()[0]

    def _capture(self, learner_id):
        replica = Store()
        try:
            with self.store.lock:
                LearningService(self.store)._authorize(learner_id)
                token = self._token()
                self.store.conn.backup(replica.conn)
                if self._token() != token:
                    raise RuntimeConflict("Database changed during snapshot capture")
            baseline = {table: rows(replica.conn, table) for table in TABLES}
            governance = GovernanceService(replica)
            replica.governance_review_sink = lambda action, reason: governance.enqueue_safety_review(learner_id, action, "R-05")
            return replica, baseline, token
        except Exception:
            replica.close()
            raise

    @staticmethod
    def _state(store, sid, learner_id):
        row = store.conn.execute("SELECT learner_id,version,payload FROM runtime_sessions WHERE session_id=?", (sid,)).fetchone()
        if row is None:
            raise KeyError("Session has no recoverable runtime state")
        if row["learner_id"] != learner_id:
            raise PermissionError("Session belongs to another learner")
        return row["version"], json.loads(row["payload"])

    def load(self, sid: str, learner_id: str) -> TutorSession:
        with self.store.lock:
            LearningService(self.store)._authorize(learner_id)
            _, state = self._state(self.store, sid, learner_id)
            session = TutorSession.restore(self.store, self.llm, state)
            session._asset_catalog = self.catalog
            self._bind_sources(session)
            return session

    def _bind_sources(self, session):
        sources = RuntimeSources(self.bank_factory(), self.catalog)
        sources.assert_bank(session.bank)
        session._runtime_sources = sources
        session.trigger.state = PersistentTriggerState(session.store, session.learner_id, session.session_id)
        session.clock_port = CallableClock(self.clock) if self.clock else SystemClock()

    def public_state(self, sid, learner_id):
        session = self.load(sid, learner_id)
        with self.store.lock:
            rows = self.store.conn.execute(
                "SELECT payload FROM events WHERE session_id=? AND learner_pseudo_id=? "
                "ORDER BY event_seq", (sid, learner_id)
            ).fetchall()
        messages = []
        for row in rows:
            event = json.loads(row[0])
            value = event.get("observation", {}).get("text")
            if value and event.get("observation", {}).get("kind") in {"utterance", "answer", "help_seeking"}:
                messages.append(dict(who="我" if event.get("actor", {}).get("kind") == "student"
                                     else "桂子问津", text=value))
        return dict(session_id=sid, learner_id=learner_id, session_type=session.session_type,
                    project_id=session.project_id, task_id=session.task_id, ui=session._ui_state(None),
                    turn_count=session.turn_count, messages=messages)

    def _receipt(self, sid, learner_id, key, content_hash):
        with self.store.lock:
            LearningService(self.store)._authorize(learner_id)
            self._state(self.store, sid, learner_id)
            row = self.store.conn.execute("SELECT content_hash,payload FROM gateway_receipts WHERE session_id=? AND attempt_id=?", (sid, key)).fetchone()
            if row:
                if row["content_hash"] != content_hash:
                    raise RuntimeConflict("attempt_id content conflict")
                return json.loads(row["payload"])
            return None

    def _commit(self, replica, baseline, token, learner_id, fault):
        changes = []
        for table in TABLES:
            columns, pk, old = baseline[table]
            new_columns, new_pk, new = rows(replica.conn, table)
            if (columns, pk) != (new_columns, new_pk) or old.keys() - new.keys():
                raise RuntimeError("Runtime changeset cannot alter schemas or delete facts")
            for key, values in new.items():
                if key not in old or old[key] != values:
                    if key in old and table not in MUTABLE:
                        raise RuntimeError(f"Runtime cannot rewrite append-only {table}")
                    changes.append((table, columns, pk, key, old.get(key), values))
        with self.store.lock:
            conn = self.store.conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                LearningService(self.store)._authorize(learner_id)
                if self._token() != token:
                    raise RuntimeConflict("Database changed while model work ran; retry the same attempt")
                for table, columns, pk, key, prior, values in changes:
                    current_columns = tuple(row["name"] for row in conn.execute(f"PRAGMA table_info({table})"))
                    if current_columns != columns:
                        raise RuntimeConflict("Runtime schema changed")
                    if prior is None:
                        conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)
                    else:
                        predicate = " AND ".join(f"{column}=?" for column in pk)
                        current = conn.execute(f"SELECT * FROM {table} WHERE {predicate}", key).fetchone()
                        if current is None or tuple(current[c] for c in columns) != prior:
                            raise RuntimeConflict("Runtime state compare-and-swap conflict")
                        conn.execute(f"UPDATE {table} SET {','.join(column+'=?' for column in columns)} WHERE {predicate}", (*values, *key))
                    fault(table)
                fault("before_commit")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def create(self, learner_id, contract=None, session_type="explore", *,
               project_id=None, task_id=None, fault: Callable | None = None):
        replica, baseline, token = self._capture(learner_id)
        try:
            session = TutorSession(replica, self.llm, learner_id, contract=contract, session_type=session_type,
                                   bank=self.bank_factory(), project_id=project_id, task_id=task_id)
            if self.policy_factory:
                session.policy = self.policy_factory()
            if self.clock:
                session._clock = self.clock
            session._asset_catalog = self.catalog
            self._bind_sources(session)
            result = session.start()
            replica.conn.execute("INSERT INTO runtime_sessions VALUES (?,?,?,?)",
                                 (session.session_id, learner_id, 1, dump(session.export_state())))
            replica.conn.commit()
            self._commit(replica, baseline, token, learner_id, fault or (lambda _: None))
            return dict(session_id=session.session_id, reply=result.reply, ui=result.ui, denial=result.denial)
        finally:
            replica.close()

    def message(self, sid, learner_id, *, text="", answer=None, attempt_id=None, fault: Callable | None = None):
        key = attempt_id or uuid4().hex
        content_hash = fingerprint(text, answer)
        receipt = self._receipt(sid, learner_id, key, content_hash)
        if receipt:
            return receipt
        replica, baseline, token = self._capture(learner_id)
        try:
            version, state = self._state(replica, sid, learner_id)
            existing = replica.conn.execute("SELECT content_hash,payload FROM gateway_receipts WHERE session_id=? AND attempt_id=?", (sid, key)).fetchone()
            if existing:
                if existing["content_hash"] != content_hash:
                    raise RuntimeConflict("attempt_id content conflict")
                return json.loads(existing["payload"])
            session = TutorSession.restore(replica, self.llm, state)
            if self.policy_factory:
                session.policy = self.policy_factory()
            if self.clock:
                session._clock = self.clock
            session._asset_catalog = self.catalog
            self._bind_sources(session)
            result = session.handle_turn(text=text, answer=answer, attempt_id=f"session:{sid}:{key}")
            response = dict(reply=result.reply, ui=result.ui, denial=result.denial, events=len(result.events), attempt_id=key)
            replica.conn.execute("UPDATE runtime_sessions SET version=?,payload=? WHERE session_id=? AND version=?",
                                 (version + 1, dump(session.export_state()), sid, version))
            replica.conn.execute("INSERT INTO gateway_receipts VALUES (?,?,?,?)", (sid, key, content_hash, dump(response)))
            replica.conn.commit()
            self._commit(replica, baseline, token, learner_id, fault or (lambda _: None))
            return response
        except RuntimeConflict:
            receipt = self._receipt(sid, learner_id, key, content_hash)
            if receipt:
                return receipt
            raise
        finally:
            replica.close()

    def reflection(self, sid, learner_id, payload):
        replica, baseline, token = self._capture(learner_id)
        try:
            version, state = self._state(replica, sid, learner_id)
            session = TutorSession.restore(replica, self.llm, state)
            session._asset_catalog = self.catalog
            self._bind_sources(session)
            response = session.submit_reflection(payload)
            replica.conn.execute("UPDATE runtime_sessions SET version=?,payload=? WHERE session_id=?",
                                 (version + 1, dump(session.export_state()), sid))
            replica.conn.commit()
            self._commit(replica, baseline, token, learner_id, lambda _: None)
            return response
        finally:
            replica.close()
