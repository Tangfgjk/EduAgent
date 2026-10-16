"""业务关系、不可变输入与工作队列。教学状态仍由原核心 Schema 表达。"""
from __future__ import annotations

import time
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid4())


def now() -> int:
    return int(time.time())


class Base(DeclarativeBase):
    pass


class SystemState(Base):
    __tablename__ = "system_state"
    id: Mapped[int] = mapped_column(primary_key=True)
    admin_initialized: Mapped[bool] = mapped_column(Boolean, default=False)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint("role IN ('admin','student')"),
                      CheckConstraint("source_kind IN ('synthetic','adult_test','real_student')"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    learner_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    source_kind: Mapped[str] = mapped_column(String(24), default="synthetic")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[int] = mapped_column(BigInteger)


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint("(kind = 'project' AND project_id IS NOT NULL) OR (kind = 'temporary' AND project_id IS NULL)"),
        CheckConstraint("status IN ('active','archived')"), CheckConstraint("revision >= 1"),
        Index("ix_workspaces_owner_created", "owner_id", "created_at", "id"),
        Index("ix_workspaces_creation_key", "owner_id", "creation_key", unique=True),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    kind: Mapped[str] = mapped_column(String(16))
    project_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)
    creation_key: Mapped[str | None] = mapped_column(String(128))
    creation_hash: Mapped[str | None] = mapped_column(String(64))


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("id", "workspace_id"), CheckConstraint("kind IN ('tutor','roundtable','teach')"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class LearningInput(Base):
    __tablename__ = "learning_inputs"
    __table_args__ = (
        ForeignKeyConstraint(["conversation_id", "workspace_id"], ["conversations.id", "conversations.workspace_id"]),
        UniqueConstraint("conversation_id", "idempotency_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(String(36))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class OutboxJob(Base):
    __tablename__ = "outbox_jobs"
    __table_args__ = (CheckConstraint("status IN ('pending','leased','completed','dead')"),
                      Index("ix_outbox_ready", "status", "available_at", "lease_until"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    input_id: Mapped[str] = mapped_column(ForeignKey("learning_inputs.id"), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[int] = mapped_column(BigInteger, default=now)
    lease_until: Mapped[int | None] = mapped_column(BigInteger)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(64))


class LearningProjection(Base):
    """占据真实的隔离键；S06/S08 再接入投影计算，不预填任何素养分数。"""
    __tablename__ = "learning_projections"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    source_refs: Mapped[list] = mapped_column(JSON)
    payload: Mapped[dict] = mapped_column(JSON)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    action: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class LegacyImport(Base):
    __tablename__ = "legacy_imports"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    manifest: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class LegacyRecord(Base):
    """无来源历史记录先隔离保全，不伪造身份或进入正式画像。"""
    __tablename__ = "legacy_records"
    import_id: Mapped[str] = mapped_column(ForeignKey("legacy_imports.id"), primary_key=True)
    table_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(24), default="legacy_unknown")
    payload: Mapped[dict] = mapped_column(JSON)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        ForeignKeyConstraint(["conversation_id", "workspace_id"], ["conversations.id", "conversations.workspace_id"]),
        UniqueConstraint("workspace_id", "creation_key"),
        CheckConstraint("status IN ('queued','awaiting_input','completed','cancelled','failed')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(String(36))
    creation_key: Mapped[str] = mapped_column(String(128))
    creation_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="plan", server_default="plan")
    view: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    draft: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    gate_id: Mapped[str | None] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(64))
    event_seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class AgentCheckpoint(Base):
    """每个有界开发流程的官方 SqliteSaver 镜像，与业务结果原子提交。"""
    __tablename__ = "agent_checkpoints"
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), primary_key=True)
    image: Mapped[str] = mapped_column(Text)


class AgentEvent(Base):
    __tablename__ = "agent_events"
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class ContentApproval(Base):
    __tablename__ = "content_approvals"
    unit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean)
    revision: Mapped[int] = mapped_column(Integer)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    reviewed_at: Mapped[int] = mapped_column(BigInteger, default=now)


class AnswerReview(Base):
    __tablename__ = "answer_reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    input_id: Mapped[str] = mapped_column(ForeignKey("learning_inputs.id"), unique=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    task_id: Mapped[str] = mapped_column(String(64))
    content_version: Mapped[str] = mapped_column(String(64))
    task_snapshot: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    conclusion: Mapped[str | None] = mapped_column(String(24))
    note: Mapped[str | None] = mapped_column(String(1000))
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class LearningFact(Base):
    __tablename__ = "learning_facts"
    __table_args__ = (UniqueConstraint("input_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"))
    input_id: Mapped[str] = mapped_column(ForeignKey("learning_inputs.id"))
    kind: Mapped[str] = mapped_column(String(24))
    source_kind: Mapped[str] = mapped_column(String(24))
    task_id: Mapped[str] = mapped_column(String(64))
    content_version: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class FactJudgment(Base):
    __tablename__ = "fact_judgments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    fact_id: Mapped[str] = mapped_column(ForeignKey("learning_facts.id"), index=True)
    review_id: Mapped[str] = mapped_column(ForeignKey("answer_reviews.id"), unique=True)
    conclusion: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(String(1000))
    actor_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class ReviewCard(Base):
    __tablename__ = "review_cards"
    __table_args__ = (UniqueConstraint("workspace_id", "task_id", "content_version", "source_kind"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    task_id: Mapped[str] = mapped_column(String(64))
    content_version: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(24))
    source_fact_ids: Mapped[list] = mapped_column(JSON)
    reasons: Mapped[list] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    schedule_step: Mapped[int] = mapped_column(Integer, default=0)
    next_due_at: Mapped[int] = mapped_column(BigInteger, default=now)
    last_reviewed_at: Mapped[int | None] = mapped_column(BigInteger)


class RecallAttempt(Base):
    __tablename__ = "recall_attempts"
    __table_args__ = (UniqueConstraint("workspace_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    card_id: Mapped[str] = mapped_column(ForeignKey("review_cards.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    input_id: Mapped[str] = mapped_column(ForeignKey("learning_inputs.id"), unique=True)
    fact_id: Mapped[str] = mapped_column(ForeignKey("learning_facts.id"), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    counted_for_schedule: Mapped[bool] = mapped_column(Boolean)
    due_before: Mapped[int] = mapped_column(BigInteger)
    due_after: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class MemoryNote(Base):
    __tablename__ = "memory_notes"
    __table_args__ = (UniqueConstraint("workspace_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    fact_id: Mapped[str] = mapped_column(ForeignKey("learning_facts.id"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class PlanningMaterial(Base):
    __tablename__ = "planning_materials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32))
    edition: Mapped[str] = mapped_column(String(80))
    source_ref: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class MaterialRelease(Base):
    __tablename__ = "material_releases"
    material_id: Mapped[str] = mapped_column(ForeignKey("planning_materials.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class StrategyVersion(Base):
    """冻结输入与候选；审核过程另存，不覆盖版本。"""
    __tablename__ = "strategy_versions"
    id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    bundle: Mapped[dict] = mapped_column(JSON)
    policy: Mapped[dict] = mapped_column(JSON)
    checks: Mapped[list] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    hard_rules_hash: Mapped[str] = mapped_column(String(64))
    base_revision: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class StrategyReceipt(Base):
    __tablename__ = "strategy_receipts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    input_id: Mapped[str] = mapped_column(ForeignKey("learning_inputs.id"), unique=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    action: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class ActiveStrategy(Base):
    __tablename__ = "active_strategies"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    version_id: Mapped[str | None] = mapped_column(ForeignKey("strategy_versions.id"), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class StrategyActivation(Base):
    __tablename__ = "strategy_activations"
    __table_args__ = (UniqueConstraint("workspace_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    from_version: Mapped[str | None] = mapped_column(ForeignKey("strategy_versions.id"), nullable=True)
    to_version: Mapped[str | None] = mapped_column(ForeignKey("strategy_versions.id"), nullable=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    kind: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(String(1000))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)
