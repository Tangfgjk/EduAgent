"""受控离线维护：一致备份、空库恢复、旧 SQLite 隔离导入。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from sqlalchemy import insert, select

from app.infrastructure.db.database import HEAD, Database
from app.infrastructure.db.models import AuditEvent, Base, LegacyImport, LegacyRecord

EXCLUDE = {"auth_sessions", "login_throttles"}
LEGACY_TABLES = {"learners", "goal_contracts", "plan_versions", "sessions", "events", "snapshots", "verdicts", "digests"}


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def checksum(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def backup(db: Database, destination: Path) -> dict:
    db.assert_ready()
    with db.engine.connect() as connection:
        if db.engine.dialect.name == "postgresql":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin():
            if db.engine.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN")
            tables = {table.name: [dict(row) for row in connection.execute(select(table)).mappings()]
                      for table in Base.metadata.sorted_tables if table.name not in EXCLUDE}
    data = {"schema_revision": HEAD, "tables": tables}
    result = {"data": data, "sha256": checksum(data)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 不覆盖已有备份；包含密码哈希与学习原文，应按敏感文件管理。
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(canonical(result))
    destination.chmod(0o600)
    return {name: len(rows) for name, rows in tables.items()}


def restore(db: Database, source: Path) -> dict:
    db.assert_ready()
    payload = json.loads(source.read_text(encoding="utf-8"))
    data = payload["data"]
    if checksum(data) != payload["sha256"] or data["schema_revision"] != HEAD:
        raise ValueError("备份校验失败或数据库版本不匹配")
    expected = {table.name for table in Base.metadata.sorted_tables if table.name not in EXCLUDE}
    if set(data["tables"]) != expected:
        raise ValueError("备份表集合不完整")
    with db.transaction() as session:
        for table in Base.metadata.sorted_tables:
            rows = session.execute(select(table)).all()
            if table.name == "system_state":
                if any(row.admin_initialized for row in rows):
                    raise ValueError("目标库已有管理员，拒绝覆盖")
            elif rows:
                raise ValueError("恢复仅允许迁移后的空库，拒绝覆盖已有数据")
        state = Base.metadata.tables["system_state"]
        session.execute(state.delete())
        for table in Base.metadata.sorted_tables:
            rows = data["tables"].get(table.name, [])
            if rows:
                session.execute(insert(table), rows)
    # 登录会话从不恢复，防止旧 Cookie 在恢复后重新生效。
    return {name: len(rows) for name, rows in data["tables"].items()}


def import_legacy(db: Database, source: Path, snapshot: Path) -> dict:
    """源库只读；SQLite backup 生成一致副本，再原样导入隔离表。"""
    if not source.is_file() or snapshot.exists() or source.resolve() == snapshot.resolve():
        raise ValueError("需要现有源库与不存在的快照目标")
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as original:
        with sqlite3.connect(snapshot) as copy:
            original.backup(copy)
    snapshot.chmod(0o600)
    tables = {}
    with sqlite3.connect(snapshot) as connection:
        connection.row_factory = sqlite3.Row
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if not names or not names.issubset(LEGACY_TABLES):
            raise ValueError("源库不符合已知旧版本结构；快照已保留，请人工核对")
        for name in sorted(names):
            tables[name] = [dict(row) for row in connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
    manifest = {name: {"count": len(rows), "sha256": checksum(rows)} for name, rows in tables.items()}
    import_id = checksum(manifest)
    with db.transaction() as session:
        existing = session.get(LegacyImport, import_id)
        if existing:
            return {"import_id": import_id, "already_imported": True, "tables": existing.manifest}
        session.add(LegacyImport(id=import_id, manifest=manifest))
        session.flush()
        for name, rows in tables.items():
            for index, row in enumerate(rows):
                session.add(LegacyRecord(import_id=import_id, table_name=name, source_key=str(index),
                                         source_kind="legacy_unknown", payload=row))
        session.add(AuditEvent(action="legacy.import", target_id=import_id))
    return {"import_id": import_id, "already_imported": False, "tables": manifest}
