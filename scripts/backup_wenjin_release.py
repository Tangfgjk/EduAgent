"""Create a paired, private PostgreSQL/files release after stopping local app writers."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = ("data/backups/", "data/private/deployment/")
ADMIN_CONFIG = {"data/system/postgres/compose.env", "data/system/postgres/init.sql"}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(connection):
    connection.execute("SET TIME ZONE 'UTC'")
    connection.execute("SET DateStyle='ISO, YMD'")
    connection.execute("SET extra_float_digits=3")
    tables = connection.execute(
        "SELECT table_schema,table_name FROM information_schema.tables "
        "WHERE table_type='BASE TABLE' AND table_schema NOT IN ('information_schema','pg_catalog') "
        "AND table_schema NOT LIKE 'pg_toast%' ORDER BY 1,2"
    ).fetchall()
    result = {}
    for schema, table in tables:
        query = sql.SQL(
            "SELECT count(*)::bigint, md5(COALESCE(string_agg(row_to_json(t)::text, E'\\n' "
            'ORDER BY row_to_json(t)::text COLLATE "C"),\'\')) FROM {}.{} t'
        ).format(sql.Identifier(schema), sql.Identifier(table))
        count, digest = connection.execute(query).fetchone()
        result[schema + "." + table] = {"count": count, "digest": digest}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--container", default="wenjin-learning-postgres-1")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if directory.exists():
        raise ValueError("Choose a new private backup directory")
    config = json.loads((ROOT / "data/system/postgres/connection.json").read_text())
    directory.mkdir(parents=True, mode=0o700)
    manifest = {
        "format": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(ROOT),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "excluded_runtime_paths": [*EXCLUDED, *sorted(ADMIN_CONFIG)],
    }
    with psycopg.connect(**config) as connection:
        others = connection.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
            "AND pid<>pg_backend_pid() AND backend_type='client backend'"
        ).fetchone()[0]
        if others:
            raise RuntimeError("Stop other database clients before paired release")
        connection.commit()
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        snapshot = connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
        manifest["tables"] = inventory(connection)
        with (directory / "database.dump").open("xb") as output:
            subprocess.run(
                ["docker", "exec", args.container, "pg_dump", "-U", "postgres",
                 "--dbname=wenjin", "--format=custom", "--no-owner", "--no-acl",
                 "--snapshot=" + snapshot], stdout=output, check=True, cwd=ROOT
            )
    manifest["source_cluster_id"] = subprocess.check_output(
        ["docker", "exec", args.container, "psql", "-U", "postgres", "-d", "wenjin",
         "-Atc", "SELECT system_identifier FROM pg_control_system()"], text=True
    ).strip()
    manifest["files"] = {}
    with tarfile.open(directory / "runtime.tar.gz", "w:gz", compresslevel=1) as archive:
        for path in sorted((ROOT / "data").rglob("*")):
            name = path.relative_to(ROOT).as_posix()
            if any(name == prefix.rstrip("/") or name.startswith(prefix) for prefix in EXCLUDED):
                continue
            if name in ADMIN_CONFIG:
                continue
            if path.is_symlink():
                raise ValueError("Refusing symbolic link in runtime release")
            if not path.is_file():
                continue
            manifest["files"][name] = sha256(path)
            info = archive.gettarinfo(str(path), arcname=name)
            info.uid = info.gid = 10001
            info.uname = info.gname = "wenjin"
            info.mode = 0o600
            with path.open("rb") as stream:
                archive.addfile(info, stream)
    manifest["database_sha256"] = sha256(directory / "database.dump")
    manifest["runtime_sha256"] = sha256(directory / "runtime.tar.gz")
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "backup": str(directory), "tables": len(manifest["tables"]),
        "rows": sum(x["count"] for x in manifest["tables"].values()),
        "files": len(manifest["files"]), "paired_release": True,
    }))


if __name__ == "__main__":
    main()
