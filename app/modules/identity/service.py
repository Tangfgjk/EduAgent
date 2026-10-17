"""账号生命周期与会话授权：所有修改与授权处于同一业务事务。"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.infrastructure.db.database import Database
from app.infrastructure.db.models import Account, AuditEvent, AuthSession, LoginThrottle, SystemState, new_id, now
from app.modules.errors import DomainError
from app.modules.identity.security import HASHER, STUDENT_PASSWORD_MIN_LENGTH, digest, password_hash, username, verify


@dataclass(frozen=True)
class Actor:
    account: Account
    token_hash: str


def public_account(account: Account) -> dict:
    return {key: getattr(account, key) for key in ("id", "username", "display_name", "role", "learner_id", "enabled")}


class Identity:
    def __init__(self, db: Database, ttl: int = 86400, data_mode: str = "synthetic"):
        self.db, self.ttl, self.data_mode = db, ttl, data_mode
        self._dummy_hash = HASHER.hash(secrets.token_urlsafe(32))

    def bootstrap(self, name: str, password: str, display_name: str):
        name, encoded = username(name), password_hash(password)
        with self.db.transaction() as session:
            state = session.get(SystemState, 1, with_for_update=True)
            if state is None or state.admin_initialized:
                raise DomainError(409, "admin_initialized", "首个管理员已初始化，不能重复执行")
            account = Account(username=name, display_name=display_name, password_hash=encoded, role="admin")
            session.add(account)
            session.flush()
            state.admin_initialized = True
            session.add(AuditEvent(actor_id=account.id, action="admin.bootstrap", target_id=account.id))
            return public_account(account)

    def login(self, name: str, password: str, client_address: str, *, expected_role: str | None = None) -> tuple[dict, str, str]:
        normalized = name.strip().lower()
        error, result = None, None
        with self.db.transaction() as session:
            timestamp = now()
            buckets = []
            # 两个持久化桶；锁顺序固定，避免并发登录锁反转。代理头不作为可信 IP。
            for key in sorted([digest("user:" + normalized), digest("ip:" + client_address)]):
                insert = pg_insert if self.db.engine.dialect.name == "postgresql" else sqlite_insert
                session.execute(insert(LoginThrottle).values(key=key, failures=0, window_start=timestamp).on_conflict_do_nothing())
                bucket = session.get(LoginThrottle, key, with_for_update=True)
                if timestamp - bucket.window_start >= 900:
                    bucket.failures, bucket.window_start = 0, timestamp
                buckets.append(bucket)
            if any(bucket.failures >= 20 for bucket in buckets):
                error = DomainError(429, "login_rate_limited", "登录尝试过多，请稍后再试")
            else:
                account = session.scalar(select(Account).where(Account.username == normalized).with_for_update())
                valid = verify(account.password_hash if account else self._dummy_hash, password)
                if account is None or not account.enabled or not valid:
                    for bucket in buckets:
                        bucket.failures += 1
                    error = DomainError(401, "invalid_credentials", "账号或密码不正确")
                elif expected_role and account.role != expected_role:
                    label = "管理员" if expected_role == "admin" else "学生"
                    error = DomainError(403, "role_forbidden", f"请使用{label}账号登录此端")
                else:
                    # IP 桶保留窗口内失败数，避免用一个正确账号绕过其他账号的失败限制。
                    for bucket in buckets:
                        if bucket.key == digest("user:" + normalized):
                            bucket.failures = 0
                    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                    session.execute(delete(AuthSession).where(AuthSession.expires_at <= timestamp))
                    session.add(AuthSession(token_hash=digest(token), account_id=account.id,
                                            csrf_hash=digest(csrf), expires_at=timestamp + self.ttl))
                    session.add(AuditEvent(actor_id=account.id, action="auth.login", target_id=account.id))
                    result = public_account(account), token, csrf
        # 失败计数须先提交，不能与异常一起回滚。
        if error:
            raise error
        return result

    def authorize(self, session: Session, token: str, csrf: str | None, role: str | None, write: bool) -> Actor:
        hashed = digest(token)
        account = session.scalar(select(Account).join(AuthSession).where(
            AuthSession.token_hash == hashed, AuthSession.expires_at > now(), Account.enabled.is_(True)
        ).with_for_update(of=Account))
        if account is None:
            raise DomainError(401, "unauthenticated", "请先登录")
        login = session.get(AuthSession, hashed, populate_existing=True)
        if login is None or login.expires_at <= now():
            raise DomainError(401, "unauthenticated", "请重新登录")
        if write and (not csrf or not secrets.compare_digest(login.csrf_hash, digest(csrf))):
            raise DomainError(403, "csrf_failed", "请求校验失败，请刷新后重试")
        if role and account.role != role:
            raise DomainError(403, "role_forbidden", "当前账号无权执行此操作")
        return Actor(account, hashed)

    def create_student(self, session: Session, actor: Actor, name: str, password: str, display_name: str):
        if actor.account.role != "admin":
            raise DomainError(403, "role_forbidden", "需要管理员权限")
        name = username(name)
        if session.scalar(select(Account.id).where(Account.username == name)):
            raise DomainError(409, "username_exists", "该账号已存在")
        account = Account(username=name, display_name=display_name, password_hash=password_hash(password, minimum=STUDENT_PASSWORD_MIN_LENGTH),
                          role="student", learner_id=new_id(), source_kind=self.data_mode)
        session.add(account)
        session.flush()
        session.add(AuditEvent(actor_id=actor.account.id, action="student.create", target_id=account.id))
        return public_account(account)

    def update_student(self, session: Session, actor: Actor, target: str, password: str | None = None):
        if actor.account.role != "admin":
            raise DomainError(403, "role_forbidden", "需要管理员权限")
        student = session.scalar(select(Account).where(Account.id == target, Account.role == "student").with_for_update())
        if student is None:
            raise DomainError(404, "not_found", "未找到学生账号")
        if password is None:
            student.enabled = False
            action = "student.disable"
        else:
            student.password_hash = password_hash(password, minimum=STUDENT_PASSWORD_MIN_LENGTH)
            action = "student.reset_password"
        session.execute(delete(AuthSession).where(AuthSession.account_id == target))
        session.add(AuditEvent(actor_id=actor.account.id, action=action, target_id=target))
        return public_account(student)

    def logout(self, session: Session, actor: Actor):
        session.execute(delete(AuthSession).where(AuthSession.token_hash == actor.token_hash))
        session.add(AuditEvent(actor_id=actor.account.id, action="auth.logout", target_id=actor.account.id))
