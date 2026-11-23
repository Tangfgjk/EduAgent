"""PostgreSQL adapter for the existing parameterized repository interface.

Activation is explicit per migrated database; unknown SQLite dialect features fail
instead of silently falling back to another user's data or the old database.
"""

import hashlib
import json
from pathlib import Path
import re
import sqlite3

try:
    import psycopg
    from psycopg import sql
    import sqlglot
    from sqlglot import exp
except ModuleNotFoundError:
    psycopg = sql = sqlglot = exp = None


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Result:
    def __init__(self, rows=(), rowcount=0, lastrowid=None):
        self.rows = list(rows)
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows

    def __iter__(self):
        return iter(self.fetchall())


def resource_key(path):
    from deeptutor.runtime.home import get_runtime_home

    try:
        return Path(path).resolve().relative_to(get_runtime_home().resolve()).as_posix()
    except ValueError:
        return None


def resource(path):
    cfg = config_path()
    if not cfg.exists():
        return None
    return json.loads(cfg.read_text()).get("resources", {}).get(resource_key(path))


def schema_name(path):
    registered = resource(path)
    return (
        registered["schema"]
        if registered
        else "business_" + hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:24]
    )


def config_path():
    from deeptutor.runtime.home import get_runtime_home

    return get_runtime_home() / "data/system/postgres/business.json"


def enabled(path):
    config = config_path()
    if not config.exists():
        return False
    data = json.loads(config.read_text())
    return resource_key(path) in data.get("resources", {}) or str(Path(path).resolve()) in data.get(
        "databases", []
    )


def connect_raw():
    from deeptutor.learning.personal_training import connection

    return connection()


def translate(statement):
    statement = re.sub(
        r"json_extract\(\s*([\w.]+)\s*,\s*'\$\.([^']+)'\s*\)",
        lambda m: "wenjin_json(" + m[1] + ",'" + m[2] + "')",
        statement,
        flags=re.I,
    )
    statement = re.sub(r"json_valid\(", "wenjin_valid(", statement, flags=re.I)
    statement = re.sub(
        r"(COALESCE\(wenjin_json\([^)]*\),\s*)0(\))", r"\g<1>'0'\2", statement, flags=re.I
    )
    ignore = bool(re.search(r"INSERT\s+OR\s+IGNORE", statement, re.I))
    statement = re.sub(r"INSERT\s+OR\s+IGNORE", "INSERT", statement, flags=re.I)
    tree = sqlglot.parse_one(statement, read="sqlite")
    for like in list(tree.find_all(exp.Like)):
        like.replace(exp.ILike(**like.copy().args))
    for column in tree.find_all(exp.Column):
        if column.name.lower() == "rowid":
            column.set("this", exp.to_identifier("_legacy_rowid"))
    for collate in list(tree.find_all(exp.Collate)):
        if str(collate.expression).upper() == "NOCASE":
            parent = collate.parent
            collate.replace(exp.Lower(this=collate.this.copy()))
            if isinstance(parent, exp.EQ):
                if not isinstance(parent.left, exp.Lower):
                    parent.set("this", exp.Lower(this=parent.left.copy()))
                if not isinstance(parent.right, exp.Lower):
                    parent.set("expression", exp.Lower(this=parent.right.copy()))
    for condition in tree.find_all(exp.EQ):
        if any(
            isinstance(node, exp.Anonymous) and node.name.lower() == "wenjin_json"
            for node in condition.left.walk()
        ) and isinstance(condition.right, (exp.Placeholder, exp.Literal)):
            condition.set(
                "expression", exp.Cast(this=condition.right.copy(), to=exp.DataType.build("TEXT"))
            )
    for select in tree.find_all(exp.Select):
        order = select.args.get("order")
        if select.args.get("group") is None or order is None:
            continue
        aliases = {
            value.alias: value.this for value in select.expressions if isinstance(value, exp.Alias)
        }
        for column in list(order.find_all(exp.Column)):
            if not column.table and column.name in aliases:
                column.replace(aliases[column.name].copy())
    generated = tree.sql(dialect="postgres")
    generated = re.sub(
        r"ON CONFLICT\s*\(([^)]*)\)",
        lambda m: "ON CONFLICT (" + re.sub(r"\s+NULLS\s+(FIRST|LAST)", "", m[1], flags=re.I) + ")",
        generated,
        flags=re.I,
    )
    if ignore:
        generated += " ON CONFLICT DO NOTHING"
    return generated


class Connection:
    def __init__(self, path, autocommit=False):
        if psycopg is None:
            raise RuntimeError("Install requirements/postgres.txt before enabling PostgreSQL")
        self.raw = connect_raw()
        self.raw.autocommit = autocommit
        self.schema = schema_name(path)
        self.changes = 0
        self.raw.execute(
            sql.SQL("SET search_path TO {},public").format(sql.Identifier(self.schema))
        )
        if not autocommit:
            self.raw.commit()
        self.identities = {
            row[0]: row[1]
            for row in self.raw.execute(
                "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=%s AND is_identity='YES'",
                (self.schema,),
            )
            if row[1] != "_legacy_rowid"
        }
        if not autocommit:
            self.raw.commit()

    def execute(self, statement, params=()):
        simple = statement.strip().rstrip(";")
        if simple.upper().startswith("PRAGMA"):
            raise NotImplementedError("SQLite PRAGMA cannot run on active PostgreSQL storage")
        if simple.upper() == "SELECT CHANGES()":
            return Result([Row(changes=self.changes)])
        if simple.upper() in {"BEGIN IMMEDIATE", "BEGIN"}:
            if self.raw.info.transaction_status == psycopg.pq.TransactionStatus.IDLE:
                self.raw.execute("BEGIN")
            self.raw.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (self.schema,))
            return Result()
        query = translate(simple)
        insert = re.match(r'INSERT INTO ([\w"]+)', query, re.I)
        identity = self.identities.get(insert[1].strip('"')) if insert else None
        if identity and "RETURNING" not in query.upper():
            query += ' RETURNING "' + identity + '"'
        try:
            cursor = self.raw.execute(
                re.sub(r"%(?!s)", "%%", query) if params else query, params or None
            )
            self.changes = max(0, cursor.rowcount)
            rows = (
                [
                    Row(zip([c.name for c in cursor.description], values))
                    for values in cursor.fetchall()
                ]
                if cursor.description
                else []
            )
            return Result(rows, cursor.rowcount, rows[0][0] if identity and rows else None)
        except psycopg.IntegrityError as error:
            raise sqlite3.IntegrityError(str(error)) from error

    def executemany(self, statement, parameters):
        count = 0
        for params in parameters:
            count += max(0, self.execute(statement, params).rowcount)
        return Result(rowcount=count)

    def executescript(self, script):
        for statement in sqlglot.parse(script, read="sqlite"):
            if statement:
                self.execute(statement.sql(dialect="sqlite"))

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, kind, error, tb):
        if kind:
            self.rollback()
        else:
            self.commit()


def enroll(path):
    """Onboard a new user's fully initialized store after global cutover."""
    cfg = config_path()
    if not cfg.exists():
        return
    settings = json.loads(cfg.read_text())
    if not settings.get("auto_enroll") or enabled(path):
        return
    from deeptutor.runtime.home import get_runtime_home

    resolved = Path(path).resolve()
    try:
        relative = resolved.relative_to((get_runtime_home() / "data").resolve())
    except ValueError:
        return
    if any(part in {"private", "backups"} for part in relative.parts):
        return
    from deeptutor.services.file_io import atomic_write_text
    from deeptutor.services.storage.postgres_migration import migrate

    with connect_raw() as lock:
        lock.execute("SELECT pg_advisory_xact_lock(981307127)")
        settings = json.loads(cfg.read_text())
        if str(resolved) in settings.get("databases", []):
            return
        migrate(resolved)
        settings.setdefault("databases", []).append(str(resolved))
        settings.setdefault("resources", {})[resource_key(resolved)] = {
            "schema": schema_name(resolved),
            "scope_id": hashlib.sha256(str(resolved).encode()).hexdigest(),
        }
        atomic_write_text(cfg, json.dumps(settings, ensure_ascii=False, indent=2))
