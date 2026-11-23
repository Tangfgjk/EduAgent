"""Verified, non-destructive migration staging. Does NOT switch application storage."""

import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from psycopg import sql
from psycopg.types.json import Jsonb

from deeptutor.learning.personal_training import connection


def canonical(row):
    return json.dumps(
        list(row),
        ensure_ascii=False,
        separators=(",", ":"),
        default=lambda v: {"base64": base64.b64encode(v).decode()},
        allow_nan=False,
    )


def digest(rows):
    # Multiset digest handles tables without primary keys and preserves duplicates.
    hashes = sorted(hashlib.sha256(canonical(row).encode()).hexdigest() for row in rows)
    return hashlib.sha256("".join(hashes).encode()).hexdigest()


def stage():
    batch = uuid.uuid4().hex
    backup = ROOT / "data/backups/migration" / batch
    backup.mkdir(parents=True, exist_ok=False)
    report = {
        "batch": batch,
        "created_at": time.time(),
        "status": "staged_not_active",
        "sources": [],
    }
    sources = sorted((ROOT / "data").rglob("chat_history.db")) + sorted(
        (ROOT / "data").rglob("mastery.sqlite3")
    )
    sources = [p for p in sources if "backups" not in p.relative_to(ROOT / "data").parts]
    with connection() as pg:
        pg.execute("CREATE SCHEMA IF NOT EXISTS migration_staging")
        pg.execute(
            "CREATE TABLE IF NOT EXISTS migration_staging.batches (id text PRIMARY KEY, report jsonb NOT NULL)"
        )
        for source in sources:
            relative = str(source.relative_to(ROOT)).replace("\\", "/")
            schema = "stage_" + hashlib.sha256((batch + relative).encode()).hexdigest()[:24]
            pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            snapshot = backup / (hashlib.sha256(relative.encode()).hexdigest()[:16] + ".sqlite3")
            with (
                sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as origin,
                sqlite3.connect(snapshot) as target,
            ):
                origin.backup(target)
            source_report = {
                "source": relative,
                "schema": schema,
                "backup": str(snapshot.relative_to(ROOT)),
                "tables": [],
            }
            with sqlite3.connect(snapshot) as db:
                tables = db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                for (name,) in tables:
                    quoted = '"' + name.replace('"', '""') + '"'
                    columns = db.execute("PRAGMA table_info(" + quoted + ")").fetchall()
                    rows = db.execute("SELECT * FROM " + quoted).fetchall()
                    definitions = []
                    for column in columns:
                        declared = (column[2] or "TEXT").upper()
                        kind = (
                            "bigint"
                            if "INT" in declared
                            else "double precision"
                            if any(t in declared for t in ("REAL", "FLOAT", "DOUBLE"))
                            else "bytea"
                            if "BLOB" in declared
                            else "text"
                        )
                        definitions.append(
                            sql.SQL("{} {}").format(sql.Identifier(column[1]), sql.SQL(kind))
                        )
                    table = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(name))
                    pg.execute(
                        sql.SQL("CREATE TABLE {} ({})").format(
                            table, sql.SQL(",").join(definitions)
                        )
                    )
                    with pg.cursor() as cur:
                        with cur.copy(sql.SQL("COPY {} FROM STDIN").format(table)) as copy:
                            for row in rows:
                                copy.write_row(row)
                    copied = pg.execute(sql.SQL("SELECT * FROM {}").format(table)).fetchall()
                    # psycopg bytea may be returned as bytes; canonical preserves representation.
                    if len(rows) != len(copied) or digest(rows) != digest(copied):
                        raise RuntimeError("Content verification failed: " + relative + ":" + name)
                    source_report["tables"].append(
                        {
                            "name": name,
                            "count": len(rows),
                            "digest": digest(rows),
                            "sqlite_columns": columns,
                        }
                    )
            report["sources"].append(source_report)
        accounts = ROOT / "data/system/auth/users.json"
        if accounts.exists():
            raw = accounts.read_bytes()
            (backup / "users.json").write_bytes(raw)
            body = json.loads(raw)
            pg.execute(
                "CREATE TABLE IF NOT EXISTS migration_staging.accounts (batch text PRIMARY KEY, body jsonb NOT NULL, digest text NOT NULL)"
            )
            pg.execute(
                "INSERT INTO migration_staging.accounts VALUES (%s,%s,%s)",
                (batch, Jsonb(body), hashlib.sha256(raw).hexdigest()),
            )
            stored = pg.execute(
                "SELECT body FROM migration_staging.accounts WHERE batch=%s", (batch,)
            ).fetchone()[0]
            if stored != body:
                raise RuntimeError("Account verification failed")
            report["accounts_verified"] = True
        pg.execute("INSERT INTO migration_staging.batches VALUES (%s,%s)", (batch, Jsonb(report)))
    output = backup / "report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "batch": batch,
                "sources": len(report["sources"]),
                "tables": sum(len(s["tables"]) for s in report["sources"]),
                "rows": sum(t["count"] for s in report["sources"] for t in s["tables"]),
                "accounts_verified": report.get("accounts_verified", False),
                "report": str(output),
                "status": report["status"],
            }
        )
    )


if __name__ == "__main__":
    stage()
