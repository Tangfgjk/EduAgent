"""Offline backup/archive commands; never overwrite an existing target."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3

from app.config import _load_dotenv
from app.storage.db import Store
from app.storage.postgres import PostgresArchive


def backup_sqlite(source: Path, destination: Path) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if not source.is_file():
        raise ValueError("SQLite source file does not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reserve exclusively so operator mistakes cannot overwrite a database.
    with destination.open("xb"):
        pass
    original = copied = None
    success = False
    try:
        original = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        copied = sqlite3.connect(destination)
        original.backup(copied)
        if copied.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite backup integrity check failed")
        success = True
    finally:
        if original is not None:
            original.close()
        if copied is not None:
            copied.close()
        if not success:
            destination.unlink(missing_ok=True)
    return {"source": str(source), "backup": str(destination), "integrity_check": "ok"}


def restore_archive(archive: PostgresArchive, learner_id: str, destination: Path) -> dict:
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb"):
        pass
    store = Store(str(destination))
    try:
        return archive.restore_to_sqlite(store, learner_id, operation_id="offline-restore:" + destination.name)
    except Exception:
        store.close()
        destination.unlink(missing_ok=True)
        raise
    finally:
        store.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[2] / "data" / "postgres" / ".env.local")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("sqlite-backup")
    backup.add_argument("source", type=Path)
    backup.add_argument("destination", type=Path)
    export = commands.add_parser("export")
    export.add_argument("source", type=Path)
    export.add_argument("learner_id")
    restore = commands.add_parser("restore")
    restore.add_argument("learner_id")
    restore.add_argument("destination", type=Path)
    commands.add_parser("health")
    args = parser.parse_args(argv)
    try:
        if args.command == "sqlite-backup":
            result = backup_sqlite(args.source, args.destination)
        else:
            _load_dotenv(args.env_file)
            dsn = os.getenv("EDUAGENT_POSTGRES_DSN", "")
            if not dsn:
                raise ValueError("EDUAGENT_POSTGRES_DSN is not configured")
            archive = PostgresArchive(dsn)
            if args.command == "health":
                archive.bootstrap()
                result = archive.health()
            elif args.command == "restore":
                result = restore_archive(archive, args.learner_id, args.destination)
            else:
                if not args.source.is_file():
                    raise ValueError("SQLite source file does not exist")
                store = Store(str(args.source))
                try:
                    result = archive.export_from_sqlite(store, args.learner_id)
                finally:
                    store.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        # Connection exceptions must never echo operator-supplied credentials.
        print(json.dumps({"ok": False, "error_type": type(exc).__name__, "message": "Archive operation failed; existing targets are never overwritten"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
