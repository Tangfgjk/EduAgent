"""Account registry with database-wide read/modify/write serialization."""

from contextvars import ContextVar
import json
import threading
from deeptutor.services.storage.postgres_adapter import config_path, connect_raw

_current = ContextVar("account_transaction", default=None)


def enabled():
    path = config_path()
    if not path.exists():
        return False
    from deeptutor.multi_user.identity import USERS_FILE
    from deeptutor.runtime.home import get_runtime_home

    expected = get_runtime_home() / "data/system/auth/users.json"
    return USERS_FILE.resolve() == expected.resolve() and bool(
        json.loads(path.read_text()).get("accounts")
    )


def read():
    current = _current.get()
    if current is not None:
        rows = current.execute(
            "SELECT username,body FROM account_records ORDER BY username"
        ).fetchall()
    else:
        with connect_raw() as c:
            rows = c.execute(
                "SELECT username,body FROM account_records ORDER BY username"
            ).fetchall()
    return dict(rows)


def lookup(user_id):
    with connect_raw() as c:
        row = c.execute(
            "SELECT username,body FROM account_records WHERE user_id=%s", (user_id,)
        ).fetchone()
    return row


def write(users):
    from psycopg.types.json import Jsonb

    current = _current.get()
    if current is None:
        with AccountLock():
            write(users)
        return
    existing = dict(current.execute("SELECT username,body FROM account_records").fetchall())
    for username, record in users.items():
        if existing.get(username) == record:
            continue
        current.execute(
            "INSERT INTO account_records(user_id,username,role,body) VALUES (%s,%s,%s,%s) ON CONFLICT(username) DO UPDATE SET user_id=EXCLUDED.user_id,role=EXCLUDED.role,body=EXCLUDED.body",
            (record["id"], username, record["role"], Jsonb(record)),
        )
    for username in existing.keys() - users.keys():
        current.execute("DELETE FROM account_records WHERE username=%s", (username,))


class AccountLock:
    def __init__(self):
        self.local = threading.RLock()
        self.tokens = threading.local()

    def __enter__(self):
        self.local.acquire()
        try:
            if enabled() and _current.get() is None:
                c = connect_raw()
                c.execute("SELECT pg_advisory_xact_lock(981307126)")
                token = _current.set(c)
                self.tokens.current = (c, token)
            stack = getattr(self.tokens, "stack", [])
            stack.append(getattr(self.tokens, "current", None))
            self.tokens.stack = stack
            self.tokens.current = None
            return self
        except BaseException:
            self.local.release()
            raise

    def __exit__(self, kind, error, tb):
        current = self.tokens.stack.pop()
        try:
            if current:
                c, token = current
                try:
                    if kind:
                        c.rollback()
                    else:
                        c.commit()
                finally:
                    _current.reset(token)
                    c.close()
        finally:
            self.local.release()
