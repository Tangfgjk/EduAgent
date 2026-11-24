import json
import sqlite3
import sys

import pytest
from unittest.mock import MagicMock

from scripts import relocate_wenjin_release as migration

SOURCE = r"C:\Users\student\Desktop\DeepTutor\DeepTutor"


def catalogue(root, relative="data/user/.runtime/workspaces.sqlite3"):
    database = root / relative
    database.parent.mkdir(parents=True)
    workspace = relative.split("/.runtime/", 1)[0] + "/workspaces/ws-general"
    row = {"workspace_id": "ws-general", "owner_id": "owner", "path": SOURCE + "/" + workspace,
           "display_name": "General", "kind": "general", "follows_root": True,
           "instructions": "Do not change this example: " + SOURCE + "/data/example"}
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE workspaces(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
        connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        connection.execute("INSERT INTO workspaces VALUES (?,?)", (row["workspace_id"], json.dumps(row)))
        connection.execute("INSERT INTO metadata VALUES ('root',?)", (json.dumps(SOURCE + "/" + workspace.rsplit("/", 1)[0]),))
    return database, row, root / workspace


def test_release_relocates_auxiliary_workspace_registry_without_changing_identity(tmp_path, monkeypatch):
    database, original, destination = catalogue(tmp_path)
    config = tmp_path / "data/system/postgres"
    config.mkdir(parents=True)
    (config / "business.json").write_text(json.dumps({"resources": {}, "databases": []}))
    (config / "connection.json").write_text("{}")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source_root": SOURCE, "files": {database.relative_to(tmp_path).as_posix(): "hash"}}))
    connection = MagicMock()
    connection.__enter__.return_value.execute.return_value.fetchall.return_value = []
    monkeypatch.setattr(migration.psycopg, "connect", lambda **_: connection)
    monkeypatch.setenv("DEEPTUTOR_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["relocate", str(manifest), "--apply"])
    migration.main()
    with sqlite3.connect(database) as connection:
        updated = json.loads(connection.execute("SELECT payload FROM workspaces").fetchone()[0])
    assert updated["path"] == str(destination)
    assert destination.is_dir()
    assert {k: v for k, v in updated.items() if k != "path"} == {k: v for k, v in original.items() if k != "path"}


def test_catalog_relocation_is_backed_up_and_idempotent(tmp_path):
    from scripts.relocate_wenjin_workspaces import relocate_catalogs

    database, original, destination = catalogue(tmp_path)
    files = [database.relative_to(tmp_path).as_posix()]
    first = relocate_catalogs(tmp_path, SOURCE, files)
    assert first["registrations"] == 1
    assert first["metadata"] == 1
    assert destination.is_dir()
    backups = list((tmp_path / "data/backups/workspace-relocation").rglob("workspaces.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert json.loads(connection.execute("SELECT payload FROM workspaces").fetchone()[0]) == original
    second = relocate_catalogs(tmp_path, SOURCE, files)
    assert second["catalogs"] == 0
    assert len(list((tmp_path / "data/backups/workspace-relocation").rglob("workspaces.sqlite3"))) == 1


def test_per_user_registry_keeps_its_own_root(tmp_path):
    from scripts.relocate_wenjin_workspaces import relocate_catalogs

    database, original, destination = catalogue(tmp_path, "data/users/student/user/.runtime/workspaces.sqlite3")
    result = relocate_catalogs(tmp_path, SOURCE, [database.relative_to(tmp_path).as_posix()])
    assert result["registrations"] == 1
    assert destination.is_relative_to(tmp_path / "data/users/student/user")
    with sqlite3.connect(database) as connection:
        row = json.loads(connection.execute("SELECT payload FROM workspaces").fetchone()[0])
    assert row["owner_id"] == original["owner_id"]
    assert row["path"] == str(destination)


@pytest.mark.parametrize("suffix", ["data/user/../../outside", "data/users/another/user/workspaces"])
def test_catalog_cannot_relocate_outside_its_owner(tmp_path, suffix):
    from scripts.relocate_wenjin_workspaces import relocate_catalogs

    database, row, _ = catalogue(tmp_path)
    row["path"] = SOURCE + "/" + suffix
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE workspaces SET payload=?", (json.dumps(row),))
    before = database.read_bytes()
    with pytest.raises(RuntimeError, match="escapes"):
        relocate_catalogs(tmp_path, SOURCE, [database.relative_to(tmp_path).as_posix()])
    assert database.read_bytes() == before


def test_external_folders_are_not_silently_reassigned(tmp_path):
    from scripts.relocate_wenjin_workspaces import relocate_catalogs

    database, row, _ = catalogue(tmp_path)
    row["path"] = "D:/unavailable-course"
    row["follows_root"] = False
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE workspaces SET payload=?", (json.dumps(row),))
    relocate_catalogs(tmp_path, SOURCE, [database.relative_to(tmp_path).as_posix()])
    with sqlite3.connect(database) as connection:
        assert json.loads(connection.execute("SELECT payload FROM workspaces").fetchone()[0])["path"] == row["path"]
