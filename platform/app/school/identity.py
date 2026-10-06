"""Persistent tenant/class identities and hashed local bearer sessions.

Provisioning and associations are trusted offline operations, never public APIs.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal


class SchoolDenied(PermissionError):
    pass


def password_hash(password: str) -> str:
    if len(password) < 6 or len(password) > 256:
        raise ValueError("password length must be between 6 and 256")
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 260000).hex()
    return f"pbkdf2_sha256$260000${salt}${derived}"


def password_matches(password, hashed):
    try:
        algorithm, rounds, salt, expected = hashed.split("$")
        if algorithm != "pbkdf2_sha256" or int(rounds) != 260000:
            return False
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(got, expected)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class SchoolPrincipal:
    account_id: str
    tenant_id: str
    role: Literal["student", "teacher", "parent"]


class SchoolDirectory:
    def __init__(self, path: Path | str, *, clock=None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with self.lock:
            self.conn.executescript("""
                PRAGMA foreign_keys=ON;
                PRAGMA busy_timeout=10000;
                CREATE TABLE IF NOT EXISTS tenants(tenant_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS classes(tenant_id TEXT NOT NULL,class_id TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,class_id),FOREIGN KEY(tenant_id) REFERENCES tenants(tenant_id));
                CREATE TABLE IF NOT EXISTS accounts(account_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,
                    username TEXT NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('student','teacher','parent')),
                    active INTEGER NOT NULL DEFAULT 1,UNIQUE(tenant_id,username),FOREIGN KEY(tenant_id) REFERENCES tenants(tenant_id));
                CREATE TABLE IF NOT EXISTS memberships(account_id TEXT NOT NULL,tenant_id TEXT NOT NULL,class_id TEXT NOT NULL,
                    PRIMARY KEY(account_id,class_id),FOREIGN KEY(account_id) REFERENCES accounts(account_id),
                    FOREIGN KEY(tenant_id,class_id) REFERENCES classes(tenant_id,class_id));
                CREATE TABLE IF NOT EXISTS learners(learner_id TEXT PRIMARY KEY,account_id TEXT NOT NULL UNIQUE,
                    tenant_id TEXT NOT NULL,FOREIGN KEY(account_id) REFERENCES accounts(account_id));
                CREATE TABLE IF NOT EXISTS assignments(actor_id TEXT NOT NULL,learner_id TEXT NOT NULL,tenant_id TEXT NOT NULL,
                    class_id TEXT NOT NULL,PRIMARY KEY(actor_id,learner_id,class_id),FOREIGN KEY(actor_id) REFERENCES accounts(account_id),
                    FOREIGN KEY(learner_id) REFERENCES learners(learner_id),FOREIGN KEY(tenant_id,class_id) REFERENCES classes(tenant_id,class_id));
                CREATE TABLE IF NOT EXISTS bearer_sessions(token_sha256 TEXT PRIMARY KEY,account_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,FOREIGN KEY(account_id) REFERENCES accounts(account_id));
                CREATE TABLE IF NOT EXISTS login_attempts(identity_sha256 TEXT PRIMARY KEY,window_start TEXT NOT NULL,failed INTEGER NOT NULL);
            """)
            self.conn.commit()

    def close(self):
        with self.lock:
            self.conn.close()

    def _now(self):
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("timezone-aware school clock required")
        return now

    def provision_tenant(self, tenant_id):
        if not tenant_id or len(tenant_id) > 100:
            raise ValueError("invalid tenant")
        with self.lock, self.conn:
            self.conn.execute("INSERT OR IGNORE INTO tenants VALUES (?)", (tenant_id,))

    def provision_class(self, tenant_id, class_id):
        if not class_id or len(class_id) > 100:
            raise ValueError("invalid class")
        with self.lock, self.conn:
            self.conn.execute("INSERT OR IGNORE INTO classes VALUES (?,?)", (tenant_id, class_id))

    def provision_account(self, tenant_id, username, password, role, *, learner_id=None):
        if role not in {"student", "teacher", "parent"} or not username or len(username) > 100:
            raise ValueError("invalid server role or username")
        if (role == "student") != bool(learner_id):
            raise ValueError("student requires learner identity; other roles cannot own learner")
        account_id = "account:" + secrets.token_hex(20)
        hashed = password_hash(password)
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO accounts(account_id,tenant_id,username,password_hash,role) VALUES (?,?,?,?,?)",
                              (account_id, tenant_id, username, hashed, role))
            if learner_id:
                self.conn.execute("INSERT INTO learners VALUES (?,?,?)", (learner_id, account_id, tenant_id))
        return account_id

    def membership(self, account_id, tenant_id, class_id):
        with self.lock, self.conn:
            account = self.conn.execute("SELECT tenant_id FROM accounts WHERE account_id=?", (account_id,)).fetchone()
            if account is None or account[0] != tenant_id:
                raise SchoolDenied("tenant mismatch")
            self.conn.execute("INSERT OR IGNORE INTO memberships VALUES (?,?,?)", (account_id, tenant_id, class_id))

    def assign(self, actor_id, learner_id, tenant_id, class_id):
        with self.lock, self.conn:
            actor = self.conn.execute("SELECT tenant_id,role FROM accounts WHERE account_id=?", (actor_id,)).fetchone()
            learner = self.conn.execute("SELECT account_id,tenant_id FROM learners WHERE learner_id=?", (learner_id,)).fetchone()
            if actor is None or actor[0] != tenant_id or actor[1] not in {"teacher", "parent"} or learner is None or learner[1] != tenant_id:
                raise SchoolDenied("invalid assigned learner or tenant")
            for subject in (actor_id, learner[0]):
                if not self.conn.execute("SELECT 1 FROM memberships WHERE account_id=? AND tenant_id=? AND class_id=?",
                                         (subject, tenant_id, class_id)).fetchone():
                    raise SchoolDenied("assignment requires both class memberships")
            self.conn.execute("INSERT OR IGNORE INTO assignments VALUES (?,?,?,?)", (actor_id, learner_id, tenant_id, class_id))

    def login(self, tenant_id, username, password, *, session_hours=8):
        if not 1 <= session_hours <= 24 or len(password) > 256:
            raise SchoolDenied("invalid login")
        now = self._now()
        identity = hashlib.sha256(f"{tenant_id}\0{username}".encode()).hexdigest()
        with self.lock:
            account = self.conn.execute("SELECT * FROM accounts WHERE tenant_id=? AND username=?", (tenant_id, username)).fetchone()
            attempt = self.conn.execute("SELECT window_start,failed FROM login_attempts WHERE identity_sha256=?", (identity,)).fetchone()
            failed = attempt[1] if attempt and now - datetime.fromisoformat(attempt[0]) < timedelta(minutes=1) else 0
            if failed >= 5:
                raise SchoolDenied("login rate limited")
            # Run the same PBKDF2 cost even for unknown users.
            dummy = "pbkdf2_sha256$260000$00000000000000000000000000000000$" + "0" * 64
            valid = password_matches(password, account["password_hash"] if account else dummy)
            if not account or not account["active"] or not valid:
                window = attempt[0] if failed else now.isoformat()
                self.conn.execute("INSERT INTO login_attempts VALUES (?,?,?) ON CONFLICT(identity_sha256) DO UPDATE SET failed=excluded.failed,window_start=excluded.window_start",
                                  (identity, window, failed + 1))
                self.conn.commit()
                raise SchoolDenied("invalid credentials")
            token = secrets.token_urlsafe(40)
            self.conn.execute("DELETE FROM login_attempts WHERE identity_sha256=?", (identity,))
            self.conn.execute("INSERT INTO bearer_sessions VALUES (?,?,?,0)",
                              (hashlib.sha256(token.encode()).hexdigest(), account["account_id"], (now + timedelta(hours=session_hours)).isoformat()))
            self.conn.commit()
            return token

    def authenticate(self, token):
        if not token or len(token) > 200:
            raise SchoolDenied("valid school bearer required")
        with self.lock:
            row = self.conn.execute("SELECT a.account_id,a.tenant_id,a.role,a.active,s.expires_at,s.revoked "
                "FROM bearer_sessions s JOIN accounts a ON a.account_id=s.account_id WHERE token_sha256=?",
                (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if row is None or row["revoked"] or not row["active"] or datetime.fromisoformat(row["expires_at"]) <= self._now():
                raise SchoolDenied("session revoked or expired")
            return SchoolPrincipal(row["account_id"], row["tenant_id"], row["role"])

    def revoke(self, token):
        with self.lock, self.conn:
            self.conn.execute("UPDATE bearer_sessions SET revoked=1 WHERE token_sha256=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def visible_learners(self, principal):
        with self.lock:
            account = self.conn.execute("SELECT tenant_id,role,active FROM accounts WHERE account_id=?", (principal.account_id,)).fetchone()
            if account is None or not account[2] or (account[0], account[1]) != (principal.tenant_id, principal.role):
                raise SchoolDenied("server principal changed")
            if principal.role == "student":
                rows = self.conn.execute("SELECT learner_id FROM learners WHERE account_id=? AND tenant_id=?", (principal.account_id, principal.tenant_id)).fetchall()
            else:
                rows = self.conn.execute("SELECT DISTINCT l.learner_id FROM assignments x JOIN learners l ON l.learner_id=x.learner_id "
                    "JOIN accounts a ON a.account_id=l.account_id JOIN memberships am ON am.account_id=x.actor_id AND am.tenant_id=x.tenant_id AND am.class_id=x.class_id "
                    "JOIN memberships sm ON sm.account_id=l.account_id AND sm.tenant_id=x.tenant_id AND sm.class_id=x.class_id "
                    "WHERE x.actor_id=? AND x.tenant_id=? AND l.tenant_id=? AND a.active=1 ORDER BY l.learner_id",
                    (principal.account_id, principal.tenant_id, principal.tenant_id)).fetchall()
            return [row[0] for row in rows]

    def require(self, principal, learner_id, operation):
        if operation not in {"read", "learn", "consent"} or (operation != "read" and principal.role != "student"):
            raise SchoolDenied("school role cannot perform operation")
        if learner_id not in self.visible_learners(principal):
            raise SchoolDenied("learner not assigned to current tenant/class")
