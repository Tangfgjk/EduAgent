"""Restore a consistent pg_dump into a disposable database and compare row counts."""

import json
from pathlib import Path
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deeptutor.learning.personal_training import connection


def verify():
    suffix = uuid.uuid4().hex[:12]
    database = "wenjin_recovery_" + suffix
    filename = "/tmp/" + database + ".dump"
    backup = ROOT / "data/backups/postgres" / ("recovery-" + suffix + ".dump")
    backup.parent.mkdir(parents=True, exist_ok=True)
    docker = [
        "docker",
        "compose",
        "-f",
        str(ROOT / "compose.wenjin-postgres.yaml"),
        "exec",
        "-T",
        "--user",
        "postgres",
        "postgres",
    ]
    cfg = json.loads((ROOT / "data/system/postgres/connection.json").read_text())
    created = False
    try:
        with connection() as source:
            source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            snapshot = source.execute("SELECT pg_export_snapshot()").fetchone()[0]
            tables = source.execute(
                "SELECT table_schema,table_name FROM information_schema.tables WHERE table_type='BASE TABLE' AND table_schema NOT IN ('information_schema','pg_catalog') AND table_schema NOT LIKE 'pg_toast%%' ORDER BY 1,2"
            ).fetchall()
            counts = {
                (schema, table): source.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                        sql.Identifier(schema), sql.Identifier(table)
                    )
                ).fetchone()[0]
                for schema, table in tables
            }
            subprocess.run(
                docker
                + [
                    "pg_dump",
                    "--dbname=wenjin",
                    "--format=custom",
                    "--no-owner",
                    "--no-acl",
                    "--snapshot=" + snapshot,
                    "--file=" + filename,
                ],
                check=True,
                capture_output=True,
            )
        subprocess.run(
            ["docker", "cp", "wenjin-learning-postgres-1:" + filename, str(backup)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            docker + ["createdb", "--owner=wenjin_app", database], check=True, capture_output=True
        )
        created = True
        subprocess.run(
            docker
            + [
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                "--role=wenjin_app",
                "--dbname=" + database,
                filename,
            ],
            check=True,
            capture_output=True,
        )
        cfg["dbname"] = database
        with psycopg.connect(**cfg) as restored:
            for (schema, table), count in counts.items():
                actual = restored.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                        sql.Identifier(schema), sql.Identifier(table)
                    )
                ).fetchone()[0]
                if actual != count:
                    raise RuntimeError("Recovery row-count mismatch")
        report = {
            "status": "passed",
            "tables": len(counts),
            "rows": sum(counts.values()),
            "backup": str(backup),
            "source_snapshot_consistent": True,
            "restored_database_removed": True,
        }
        (ROOT / "data/backups/postgres/recovery-report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report))
    finally:
        if created:
            subprocess.run(docker + ["dropdb", database], check=True, capture_output=True)
        subprocess.run(docker + ["rm", "-f", "--", filename], check=True, capture_output=True)


if __name__ == "__main__":
    verify()
