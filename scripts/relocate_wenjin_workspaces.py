"""Relocate owner-scoped auxiliary SQLite workspace registries after a runtime move."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3


def relocate_catalogs(root: Path, source: str, files) -> dict:
    root = root.resolve()
    source = source.replace("\\", "/").rstrip("/")
    if root.as_posix().lower() == source.lower():
        raise RuntimeError("Refusing to relocate the source installation")
    report = {"catalogs": 0, "registrations": 0, "metadata": 0, "directories": 0, "unavailable": []}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for name in files:
        relative = PurePosixPath(name)
        if relative.name != "workspaces.sqlite3" or relative.parent.name != ".runtime":
            continue
        database = (root / name).resolve()
        if not database.is_relative_to(root / "data") or not database.is_file():
            raise RuntimeError("Invalid workspace registry path")
        account_root = database.parent.parent
        directories = set()

        def mapped(value, *, managed=False):
            if not isinstance(value, str):
                return None
            normalized = value.replace("\\", "/")
            if not normalized.lower().startswith(source.lower() + "/"):
                return None
            suffix = PurePosixPath(normalized[len(source) + 1:])
            if suffix.is_absolute() or ".." in suffix.parts:
                raise RuntimeError("Workspace metadata escapes runtime home")
            target = (root / suffix).resolve()
            if not target.is_relative_to(account_root):
                raise RuntimeError("Workspace metadata escapes its account")
            if target.exists() and not target.is_dir():
                raise RuntimeError("Workspace registration does not name a directory")
            if not target.is_dir():
                if not managed:
                    report["unavailable"].append(name)
                    return None
                directories.add(target)
            return str(target)

        with sqlite3.connect(database, timeout=30) as connection:
            connection.execute("BEGIN IMMEDIATE")
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"workspaces", "metadata"} <= tables:
                raise RuntimeError("Unexpected workspace registry schema")
            updates = []
            for identifier, payload in connection.execute("SELECT id,payload FROM workspaces"):
                row = json.loads(payload)
                destination = mapped(row.get("path"), managed=bool(row.get("follows_root")) or row.get("kind") == "session")
                if destination is not None:
                    row["path"] = destination
                    updates.append((json.dumps(row, ensure_ascii=False), identifier))
            root_metadata = connection.execute("SELECT value FROM metadata WHERE key='root'").fetchone()
            destination_root = mapped(json.loads(root_metadata[0]), managed=True) if root_metadata else None
            if not updates and destination_root is None:
                continue
            # A separate read connection snapshots the committed, pre-migration state.
            backup = root / "data/backups/workspace-relocation" / stamp / relative
            backup.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            with sqlite3.connect(database) as original, sqlite3.connect(backup) as snapshot:
                original.backup(snapshot)
            os.chmod(backup, 0o600)
            for directory in directories:
                directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            connection.executemany("UPDATE workspaces SET payload=? WHERE id=?", updates)
            if destination_root is not None:
                connection.execute("UPDATE metadata SET value=? WHERE key='root'", (json.dumps(destination_root),))
            report["catalogs"] += 1
            report["registrations"] += len(updates)
            report["metadata"] += int(destination_root is not None)
            report["directories"] += len(directories)
    return report
