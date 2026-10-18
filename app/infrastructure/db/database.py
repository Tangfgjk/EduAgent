"""每个用例一个事务；SQLite 本机适配与 PostgreSQL 使用同一模型/迁移。"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

HEAD = "g08_001"


class Database:
    def __init__(self, url: str):
        parsed = make_url(url)
        if parsed.get_backend_name() not in {"sqlite", "postgresql"}:
            raise ValueError("仅支持 PostgreSQL 和本地开发 SQLite")
        options = {"pool_pre_ping": True}
        if parsed.get_backend_name() == "postgresql":
            options["connect_args"] = {"connect_timeout": 5}
        if parsed.get_backend_name() == "sqlite":
            if parsed.database not in {None, "", ":memory:"}:
                Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
            options["connect_args"] = {"check_same_thread": False, "timeout": 15}
            if parsed.database in {None, "", ":memory:"}:
                options["poolclass"] = StaticPool
        self.engine = create_engine(url, **options)
        if self.engine.dialect.name == "sqlite":
            @event.listens_for(self.engine, "connect")
            def foreign_keys(conn, _):
                conn.execute("PRAGMA foreign_keys=ON")
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self):
        with self.sessions() as session:
            try:
                if self.engine.dialect.name == "sqlite":
                    session.execute(text("BEGIN IMMEDIATE"))
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def migrate(self):
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with self.engine.begin() as conn:
            config.attributes["connection"] = conn
            command.upgrade(config, "head")

    def assert_ready(self):
        with self.engine.connect() as conn:
            if conn.execute(text("SELECT version_num FROM alembic_version")).scalar() != HEAD:
                raise RuntimeError("数据库版本不匹配，请先执行 python -m app.manage migrate")

    def close(self):
        self.engine.dispose()
