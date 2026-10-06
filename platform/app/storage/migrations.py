"""Versioned, transactional SQLite foundation migrations, including legacy V3 adoption.

Feature-owned CREATE IF NOT EXISTS tables remain feature-owned; this registry covers
the Store foundation and trigger state, not a claim of full PostgreSQL migration.
Never downgrade a database produced by a newer application.
"""
from __future__ import annotations

import sqlite3

from app.core.clock import SystemClock

SCHEMA_VERSION = 2


def migrate(conn: sqlite3.Connection, baseline_schema: str) -> int:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        version = int(row[0] or 0)
        if version > SCHEMA_VERSION:
            raise ValueError("Database schema is newer than this application; downgrade refused")
        if version < 1:
            for statement in baseline_schema.split(";"):
                if statement.strip():
                    conn.execute(statement)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
            if "event_seq" not in columns:
                conn.execute("ALTER TABLE events ADD COLUMN event_seq INTEGER")
            for event in conn.execute("SELECT event_id FROM events WHERE event_seq IS NULL ORDER BY rowid").fetchall():
                seq = conn.execute("INSERT INTO event_clock DEFAULT VALUES").lastrowid
                conn.execute("UPDATE events SET event_seq=? WHERE event_id=?", (seq, event[0]))
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS events_seq ON events(event_seq)")
            conn.execute("INSERT INTO schema_migrations VALUES (1,?)", (SystemClock().now().isoformat(),))
        if version < 2:
            conn.execute("CREATE TABLE IF NOT EXISTS trigger_state (learner_id TEXT NOT NULL, scope_id TEXT NOT NULL, rule_id TEXT NOT NULL, last_fired TEXT NOT NULL, PRIMARY KEY(learner_id,scope_id,rule_id))")
            conn.execute("INSERT INTO schema_migrations VALUES (2,?)", (SystemClock().now().isoformat(),))
        conn.commit()
        return SCHEMA_VERSION
    except Exception:
        conn.rollback()
        raise
