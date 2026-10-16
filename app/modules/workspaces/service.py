from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import AuditEvent, Conversation, LearningInput, OutboxJob, Workspace, new_id
from app.modules.errors import DomainError
from app.modules.identity.security import digest
from app.modules.identity.service import Actor


@dataclass(frozen=True)
class Scope:
    account_id: str
    learner_id: str
    workspace_id: str
    workspace_kind: str
    project_id: str | None
    source_kind: str


def public_workspace(workspace: Workspace) -> dict:
    return {"workspace_id": workspace.id, **{key: getattr(workspace, key)
            for key in ("kind", "project_id", "title", "status", "revision")}}


class Workspaces:
    def _student(self, actor: Actor):
        if actor.account.role != "student":
            raise DomainError(403, "role_forbidden", "请使用学生账号")

    def owned(self, session: Session, actor: Actor, wid: str, active: bool = False) -> Workspace:
        self._student(actor)
        workspace = session.scalar(select(Workspace).where(
            Workspace.id == wid, Workspace.owner_id == actor.account.id).with_for_update())
        if workspace is None:
            raise DomainError(404, "not_found", "未找到工作空间")
        if active and workspace.status != "active":
            raise DomainError(409, "workspace_archived", "工作空间已归档，请先恢复")
        return workspace

    def scope(self, session: Session, actor: Actor, wid: str, active: bool = False) -> Scope:
        workspace = self.owned(session, actor, wid, active)
        return Scope(actor.account.id, actor.account.learner_id, wid, workspace.kind,
                     workspace.project_id, actor.account.source_kind)

    def create(self, session: Session, actor: Actor, kind: str, title: str, key: str) -> dict:
        self._student(actor)
        if kind not in {"project", "temporary"} or not title.strip() or len(title.strip()) > 120:
            raise DomainError(422, "invalid_workspace", "请提供工作空间类型与 1–120 字标题")
        if not key or len(key) > 128:
            raise DomainError(422, "invalid_idempotency_key", "需要有效幂等键")
        request_hash = digest(json.dumps({"kind": kind, "title": title.strip()}, ensure_ascii=False, sort_keys=True))
        existing = session.scalar(select(Workspace).where(Workspace.owner_id == actor.account.id, Workspace.creation_key == key))
        if existing:
            if existing.creation_hash != request_hash:
                raise DomainError(409, "idempotency_conflict", "该创建标识已用于不同内容")
            return public_workspace(existing)
        workspace = Workspace(owner_id=actor.account.id, kind=kind, title=title.strip(),
                              project_id=new_id() if kind == "project" else None,
                              creation_key=key, creation_hash=request_hash)
        session.add(workspace)
        session.flush()
        session.add(AuditEvent(actor_id=actor.account.id, action="workspace.create", target_id=workspace.id))
        return public_workspace(workspace)

    def list(self, session: Session, actor: Actor, status: str | None, offset: int, limit: int):
        self._student(actor)
        query = select(Workspace).where(Workspace.owner_id == actor.account.id)
        if status:
            query = query.where(Workspace.status == status)
        return [public_workspace(row) for row in session.scalars(query.order_by(
            Workspace.created_at.desc(), Workspace.id).offset(offset).limit(limit))]

    def change(self, session: Session, actor: Actor, wid: str, revision: int,
               title: str | None = None, status: str | None = None):
        workspace = self.owned(session, actor, wid)
        # 状态已达到目标且 revision 为当前版本时幂等返回；旧 revision 仍冲突。
        if workspace.revision != revision:
            raise DomainError(409, "revision_conflict", "该工作空间已更新，请刷新后重试")
        if title is not None and workspace.status != "active":
            raise DomainError(409, "workspace_archived", "归档空间不能修改标题")
        if title is not None and (not title.strip() or len(title.strip()) > 120):
            raise DomainError(422, "invalid_title", "标题长度须为 1–120 个字符")
        if status is not None and status not in {"active", "archived"}:
            raise DomainError(422, "invalid_status", "无效状态")
        changed = (title is not None and workspace.title != title.strip()) or (status is not None and workspace.status != status)
        if changed:
            if title is not None:
                workspace.title = title.strip()
            if status is not None:
                workspace.status = status
            workspace.revision += 1
            session.add(AuditEvent(actor_id=actor.account.id, action="workspace.update", target_id=wid))
        return public_workspace(workspace)

    # 以下是 S04 的持久化基础接口；S02 不对外暴露未接编排的聊天端点。
    def create_conversation(self, session: Session, actor: Actor, wid: str, kind: str) -> Conversation:
        self.owned(session, actor, wid, active=True)
        if kind not in {"tutor", "roundtable", "teach"}:
            raise DomainError(422, "invalid_conversation", "无效会话类型")
        conversation = Conversation(workspace_id=wid, kind=kind)
        session.add(conversation)
        session.flush()
        return conversation

    def accept_input(self, session: Session, actor: Actor, wid: str, cid: str,
                     key: str, expected_revision: int, payload: dict) -> LearningInput:
        scope = self.scope(session, actor, wid)
        conversation = session.scalar(select(Conversation).where(
            Conversation.id == cid, Conversation.workspace_id == wid).with_for_update())
        if conversation is None:
            raise DomainError(404, "not_found", "未找到会话")
        if not key or len(key) > 128:
            raise DomainError(422, "invalid_idempotency_key", "需要有效幂等键")
        # 包括 expected_revision；同键必须重放原始请求，而不是用键绕过版本检查。
        request_hash = digest(json.dumps({"revision": expected_revision, "payload": payload},
                                         ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        existing = session.scalar(select(LearningInput).where(
            LearningInput.conversation_id == cid, LearningInput.idempotency_key == key))
        if existing:
            if existing.request_hash != request_hash:
                raise DomainError(409, "idempotency_conflict", "该提交标识已用于不同内容")
            return existing
        self.owned(session, actor, wid, active=True)
        if conversation.revision != expected_revision:
            raise DomainError(409, "revision_conflict", "会话已更新，请刷新后重试")
        entry = LearningInput(workspace_id=wid, conversation_id=cid, account_id=actor.account.id,
                              idempotency_key=key, request_hash=request_hash,
                              payload=payload, source_kind=scope.source_kind)
        session.add(entry)
        session.flush()
        session.add(OutboxJob(input_id=entry.id))
        conversation.revision += 1
        return entry

    def input_ids(self, session: Session, actor: Actor, wid: str | None = None, real_only: bool = True) -> list[str]:
        self._student(actor)
        query = select(LearningInput.id).join(Workspace, Workspace.id == LearningInput.workspace_id).where(
            Workspace.owner_id == actor.account.id)
        if wid:
            self.owned(session, actor, wid)
            query = query.where(Workspace.id == wid)
        else:
            query = query.where(Workspace.kind == "project")
        if real_only:
            query = query.where(LearningInput.source_kind == "real_student")
        return list(session.scalars(query.order_by(LearningInput.created_at, LearningInput.id)))
