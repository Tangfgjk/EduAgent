import sqlite3

import pytest

from app.storage.archive_cli import backup_sqlite, main, restore_archive


def test_sqlite_backup_is_consistent_and_refuses_overwrite(tmp_path):
    source, copied = tmp_path / "source.sqlite3", tmp_path / "backup.sqlite3"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO sample VALUES (1,'immutable')")
    assert backup_sqlite(source, copied)["integrity_check"] == "ok"
    with sqlite3.connect(copied) as conn:
        assert conn.execute("SELECT value FROM sample").fetchone()[0] == "immutable"
    before = copied.read_bytes()
    with pytest.raises(FileExistsError):
        backup_sqlite(source, copied)
    assert copied.read_bytes() == before


def test_backup_missing_or_corrupt_source_has_no_target(tmp_path):
    copied = tmp_path / "backup.sqlite3"
    with pytest.raises(ValueError):
        backup_sqlite(tmp_path / "missing", copied)
    corrupt = tmp_path / "corrupt"
    corrupt.write_bytes(b"not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError):
        backup_sqlite(corrupt, copied)
    assert not copied.exists()


def test_restore_command_refuses_existing_and_removes_failed_new_target(tmp_path):
    class BrokenArchive:
        def restore_to_sqlite(self, *args, **kwargs):
            raise ValueError("bad checksum")
    target = tmp_path / "restore.sqlite3"
    with pytest.raises(ValueError):
        restore_archive(BrokenArchive(), "learner", target)
    assert not target.exists()
    target.write_bytes(b"keep this")
    with pytest.raises(FileExistsError):
        restore_archive(BrokenArchive(), "learner", target)
    assert target.read_bytes() == b"keep this"


def test_cli_error_does_not_echo_configuration(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("EDUAGENT_POSTGRES_DSN", "a credential that must not be printed")
    assert main(["--env-file", str(tmp_path / "missing"), "health"]) == 1
    assert "credential" not in capsys.readouterr().out
