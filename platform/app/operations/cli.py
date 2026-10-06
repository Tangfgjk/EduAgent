"""Trusted local operator CLI. Does not manage or restart the running server."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from app.governance.authority import Principal
from app.learning.assets import load_catalog
from app.operations.local import BackupPolicy, backup_online, health_report, restore_new_quarantined, run_worker_batch
from app.storage.db import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--learner-id", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health")
    backup = commands.add_parser("backup")
    backup.add_argument("--root", type=Path, required=True)
    backup.add_argument("--filename", required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--root", type=Path, required=True)
    restore.add_argument("--backup-name", required=True)
    restore.add_argument("--destination-name", required=True)
    restore.add_argument("--sha256", required=True)
    for command in (backup, restore):
        command.add_argument("--policy-id", required=True)
        command.add_argument("--policy-approved", action="store_true")
    worker = commands.add_parser("worker")
    worker.add_argument("--max-jobs", type=int, default=10)
    worker.add_argument("--max-attempts", type=int, default=3)
    worker.add_argument("--deadline-seconds", type=float, default=10)
    args = parser.parse_args(argv)
    store = None
    connection = None
    try:
        source = args.database.resolve()
        if not source.is_file():
            raise ValueError("Source database must exist")
        principal = Principal("trusted-local-operator", "privacy_officer", frozenset({args.learner_id}))
        if args.command == "health":
            connection = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
            result = health_report(connection)
        elif args.command == "restore":
            result = restore_new_quarantined(principal, args.learner_id, trusted_root=args.root,
                backup_name=args.backup_name, destination_name=args.destination_name,
                expected_sha256=args.sha256, policy=BackupPolicy(args.policy_id, args.policy_approved))
        else:
            store = Store(str(source))
            if args.command == "backup":
                result = backup_online(store, principal, args.learner_id, trusted_root=args.root,
                    filename=args.filename, policy=BackupPolicy(args.policy_id, args.policy_approved))
            else:
                catalog = load_catalog()
                graph = {item.ref.asset_id: [ref.asset_id for ref in item.prerequisite_refs] for item in catalog.knowledge}
                result = run_worker_batch(store, principal, args.learner_id, graph,
                    max_jobs=args.max_jobs, max_attempts=args.max_attempts, deadline_seconds=args.deadline_seconds)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__, "message": "Local operation blocked or failed; no live service switch was performed"}))
        return 1
    finally:
        if store is not None:
            store.close()
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
