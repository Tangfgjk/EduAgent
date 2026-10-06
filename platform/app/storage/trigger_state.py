"""Atomic cooldown claims, isolated by learner and session.

Runtime binds this to its replica; claims are committed with the turn changeset,
not before model work. Direct users may bind a Store for atomic SQLite claims.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from threading import RLock
from typing import Protocol

from app.core.clock import aware_utc


class TriggerStatePort(Protocol):
    def claim(self, rule_id: str, now: datetime, cooldown: int,
              previous: datetime | None = None) -> bool: ...


class TriggerStorePort(Protocol):
    @property
    def conn(self) -> sqlite3.Connection: ...

    @property
    def lock(self) -> RLock: ...


class PersistentTriggerState:
    def __init__(self, store: TriggerStorePort, learner_id: str, scope_id: str) -> None:
        self.store, self.learner_id, self.scope_id = store, learner_id, scope_id

    def claim(self, rule_id: str, now: datetime, cooldown: int,
              previous: datetime | None = None) -> bool:
        now = aware_utc(now)
        with self.store.lock:
            conn = self.store.conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                key = (self.learner_id, self.scope_id, rule_id)
                row = conn.execute("SELECT last_fired FROM trigger_state WHERE learner_id=? AND scope_id=? AND rule_id=?", key).fetchone()
                last = datetime.fromisoformat(row[0]) if row else previous
                # V3 continuation JSON could contain naive local timestamps. Do
                # not guess their timezone: conservatively restart its cooldown.
                if last and last.tzinfo is None:
                    last = now
                permitted = last is None or now - aware_utc(last) >= timedelta(seconds=cooldown)
                if permitted:
                    conn.execute("INSERT INTO trigger_state VALUES (?,?,?,?) ON CONFLICT(learner_id,scope_id,rule_id) DO UPDATE SET last_fired=excluded.last_fired", (*key, now.isoformat()))
                elif row is None and last is not None:
                    conn.execute("INSERT INTO trigger_state VALUES (?,?,?,?)", (*key, aware_utc(last).isoformat()))
                conn.commit()
                return permitted
            except Exception:
                conn.rollback()
                raise
