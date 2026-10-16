from sqlalchemy import func, select

from app.infrastructure.db.models import AgentRun, AnswerReview, AuditEvent, Conversation, FactJudgment, LearningFact, LearningInput, LearningProjection, MemoryNote, RecallAttempt, ReviewCard, Workspace, now
from app.modules.errors import DomainError
from app.modules.learning.content_service import require_content, require_activity_content
from app.modules.learning.activity_catalog import activity_for_version
from app.modules.learning.activity_service import require_idle
from app.modules.learning.content_catalog import pack_for_task
from app.modules.learning.numeric_verifier import verify
from app.modules.learning.service import request_hash
from app.modules.memory.projections import DIGEST, MEMORY, rebuild, validate_memory_sources
from app.modules.workspaces.service import Workspaces

INTERVAL_DAYS = (1, 3, 7)  # 开发默认参数，未做真实学生校准。


def task_out(task):
    return {"task_id": task["id"], "stem": task["stem"], "answer_format": task["format"]}


def attempt_out(row):
    return {"attempt_id": row.id, **{key: getattr(row, key) for key in (
        "fact_id", "status", "counted_for_schedule", "due_before", "due_after", "created_at")}}


def note_out(row):
    return {"note_id": row.id, "fact_id": row.fact_id, "text": row.text, "created_at": row.created_at}


class Memory:
    spaces = Workspaces()

    def fact(self, session, actor, wid, fid):
        self.spaces.owned(session, actor, wid)
        fact = session.scalar(select(LearningFact).where(LearningFact.id == fid, LearningFact.workspace_id == wid))
        if fact is None:
            raise DomainError(404, "not_found", "未找到此空间中的来源")
        entry = session.get(LearningInput, fact.input_id)
        return {"fact_id": fact.id, **{key: getattr(fact, key) for key in (
            "workspace_id", "run_id", "input_id", "kind", "source_kind", "content_version", "created_at")},
            "task": task_out(fact.payload["task_snapshot"]), "stage": fact.payload["stage"],
            "status": fact.payload["status"], "hints_used": fact.payload["hints_used"],
            "answer": entry.payload.get("answer"), "reasoning": entry.payload.get("reasoning"),
            "activity_version": fact.payload.get("activity_version"),
            "independent": fact.payload.get("independent"), "reasoning_reviewed": fact.payload.get("reasoning_reviewed"),
            "reflection": entry.payload.get("reflection"), "revealed_hint": fact.payload.get("revealed_hint"),
            "notes": [note_out(row) for row in session.scalars(select(MemoryNote).where(MemoryNote.fact_id == fid).order_by(MemoryNote.created_at, MemoryNote.id))],
            "judgments": [{"judgment_id": row.id, "conclusion": row.conclusion, "note": row.note, "created_at": row.created_at}
                          for row in session.scalars(select(FactJudgment).where(FactJudgment.fact_id == fid).order_by(FactJudgment.created_at, FactJudgment.id))]}

    def card(self, session, actor, wid, cid):
        self.spaces.owned(session, actor, wid)
        card = session.scalar(select(ReviewCard).where(ReviewCard.id == cid, ReviewCard.workspace_id == wid).with_for_update())
        if card is None:
            raise DomainError(404, "not_found", "未找到此空间中的复习条目")
        return card

    def card_out(self, session, card):
        fact = session.get(LearningFact, card.source_fact_ids[0])
        return {"card_id": card.id, "workspace_id": card.workspace_id,
            "task": task_out(fact.payload["task_snapshot"]), **{key: getattr(card, key) for key in (
                "source_kind", "content_version", "source_fact_ids", "reasons", "revision", "schedule_step", "next_due_at")},
            "due": card.next_due_at <= now(), "attempts": [attempt_out(row) for row in session.scalars(
                select(RecallAttempt).where(RecallAttempt.card_id == card.id).order_by(RecallAttempt.created_at.desc(), RecallAttempt.id.desc()).limit(10))]}

    def cards(self, session, actor, wid, status="all", offset=0):
        self.spaces.owned(session, actor, wid)
        query = select(ReviewCard).where(ReviewCard.workspace_id == wid)
        if status == "due": query = query.where(ReviewCard.next_due_at <= now())
        if status == "scheduled": query = query.where(ReviewCard.next_due_at > now())
        rows = session.scalars(query.order_by(ReviewCard.next_due_at, ReviewCard.id).offset(offset).limit(20))
        return {"items": [self.card_out(session, row) for row in rows], "interval_days": list(INTERVAL_DAYS),
            "due_count": session.scalar(select(func.count()).select_from(ReviewCard).where(ReviewCard.workspace_id == wid, ReviewCard.next_due_at <= now()))}

    def recall(self, session, actor, wid, cid, payload, key):
        card = self.card(session, actor, wid, cid)
        signature = request_hash({"card_id": cid, **payload})
        existing = session.scalar(select(RecallAttempt).where(RecallAttempt.workspace_id == wid, RecallAttempt.idempotency_key == key))
        if existing:
            if existing.request_hash != signature:
                raise DomainError(409, "idempotency_conflict", "该标识已用于不同复做请求")
            return {"attempt": attempt_out(existing), "card": self.card_out(session, card)}
        self.spaces.owned(session, actor, wid, active=True)
        if card.revision != payload["expected_revision"]:
            raise DomainError(409, "revision_conflict", "复习条目已更新，请刷新后重试")
        pack = pack_for_task(card.task_id, card.content_version)
        require_content(session, actor.account.source_kind, pack.unit_id, pack.version)
        # 互动产生的复习来源仍受该互动版本的撤回约束；不重新开放已撤回内容。
        sources = session.scalars(select(LearningFact).where(LearningFact.id.in_(card.source_fact_ids)))
        for source in sources:
            activity_version = source.payload.get("activity_version")
            if activity_version:
                activity = activity_for_version(activity_version)
                if activity.parent.version != card.content_version or activity.task["id"] != card.task_id:
                    raise DomainError(409, "source_version_conflict", "互动来源与内容版本不一致，暂不能复做")
                require_activity_content(session, actor.account.source_kind, activity.unit_id)

        require_idle(session, wid, ["roundtable", "teach"])
        if card.source_kind != actor.account.source_kind:
            raise DomainError(409, "source_version_conflict", "内容版本或数据来源已变化，暂不能自动复做此条目")
        active = session.scalars(select(AgentRun).where(AgentRun.workspace_id == wid, AgentRun.kind == "tutor",
            AgentRun.status.in_(["queued", "awaiting_input"])))
        if any(run.view and run.view["phase"] in {"diagnostic", "checkpoint"} for run in active):
            raise DomainError(409, "independent_check", "当前空间正在诊断或独立检测，请完成后再复做")
        original = session.get(LearningFact, card.source_fact_ids[0])
        task = original.payload["task_snapshot"]
        status = verify(payload["answer"], task["answer"])
        conversation = self.spaces.create_conversation(session, actor, wid, "tutor")
        run = AgentRun(workspace_id=wid, conversation_id=conversation.id, creation_key="recall:" + conversation.id,
            creation_hash=signature, version="recall.v1:" + card.content_version[:16], kind="recall", status="completed")
        session.add(run)
        session.flush()
        entry = LearningInput(workspace_id=wid, conversation_id=conversation.id, account_id=actor.account.id,
            idempotency_key=key, request_hash=signature, source_kind=actor.account.source_kind,
            payload={**payload, "action": "review_answer", "run_id": run.id, "card_id": cid})
        session.add(entry)
        session.flush()
        fact = LearningFact(workspace_id=wid, run_id=run.id, input_id=entry.id, kind="review_answer",
            source_kind=entry.source_kind, task_id=task["id"], content_version=card.content_version,
            payload={"stage": "review", "status": status, "hints_used": 0, "attempt": 1, "turn": 1,
                     "task_snapshot": task, "verifier": "bounded-rational.v1", "repeated_original": True})
        session.add(fact)
        session.flush()
        timestamp, due_before = now(), card.next_due_at
        counted = status == "passed" and timestamp >= due_before
        if counted:
            card.next_due_at = timestamp + INTERVAL_DAYS[min(card.schedule_step, len(INTERVAL_DAYS) - 1)] * 86400
            card.schedule_step = min(card.schedule_step + 1, len(INTERVAL_DAYS))
        elif status == "failed":
            card.next_due_at, card.schedule_step = timestamp, 0
        card.last_reviewed_at, card.revision = timestamp, card.revision + 1
        attempt = RecallAttempt(card_id=cid, workspace_id=wid, input_id=entry.id, fact_id=fact.id,
            idempotency_key=key, request_hash=signature, status=status, counted_for_schedule=counted,
            due_before=due_before, due_after=card.next_due_at)
        session.add(attempt)
        if status == "unverifiable":
            session.add(AnswerReview(input_id=entry.id, run_id=run.id, task_id=task["id"],
                content_version=card.content_version, task_snapshot=task))
        session.add(AuditEvent(actor_id=actor.account.id, action="review.recall", target_id=cid))
        session.flush()
        rebuild(session, wid)
        return {"attempt": attempt_out(attempt), "card": self.card_out(session, card)}

    def unknowns(self, session, actor, wid, offset=0):
        self.spaces.owned(session, actor, wid)
        rows = session.execute(select(AnswerReview, LearningInput, LearningFact.id).join(
            LearningInput, AnswerReview.input_id == LearningInput.id).outerjoin(LearningFact, LearningFact.input_id == LearningInput.id)
            .where(LearningInput.workspace_id == wid).order_by(AnswerReview.created_at.desc(), AnswerReview.id).offset(offset).limit(20))
        return {"items": [{"fact_id": fid, "review_id": row.id, "task_id": row.task_id, "answer": entry.payload["answer"],
            "status": row.status, "conclusion": row.conclusion, "note": row.note} for row, entry, fid in rows]}

    def add_note(self, session, actor, wid, payload, key):
        self.fact(session, actor, wid, payload["fact_id"])
        signature = request_hash(payload)
        existing = session.scalar(select(MemoryNote).where(MemoryNote.workspace_id == wid, MemoryNote.idempotency_key == key))
        if existing:
            if existing.request_hash != signature:
                raise DomainError(409, "idempotency_conflict", "该标识已用于其他说明")
            return note_out(existing)
        self.spaces.owned(session, actor, wid, active=True)
        note = MemoryNote(workspace_id=wid, fact_id=payload["fact_id"], actor_id=actor.account.id,
            idempotency_key=key, request_hash=signature, text=payload["text"])
        session.add(note)
        session.add(AuditEvent(actor_id=actor.account.id, action="memory.self_report", target_id=payload["fact_id"]))
        session.flush()
        rebuild(session, wid)
        return note_out(note)

    def workspace_memory(self, session, workspace):
        # 与复核/worker 的写锁一致，保证同一响应中 L2 与 L3 引用版本匹配。
        workspace = session.get(Workspace, workspace.id, with_for_update=True, populate_existing=True)
        digest, memory = session.get(LearningProjection, (workspace.id, DIGEST)), session.get(LearningProjection, (workspace.id, MEMORY))
        if memory:
            validate_memory_sources(memory.source_refs)
            for claim in memory.payload["claims"]: validate_memory_sources(claim["source_refs"])
        return {"workspace_id": workspace.id, "title": workspace.title, "kind": workspace.kind, "status": workspace.status,
            "digest": {"revision": digest.revision, "source_refs": digest.source_refs, **digest.payload} if digest else None,
            "observations": {"revision": memory.revision, "source_refs": memory.source_refs, **memory.payload} if memory else None}

    def local(self, session, actor, wid):
        workspace = self.spaces.owned(session, actor, wid)
        return {"scope": "project", "project_count": 1, "items": [self.workspace_memory(session, workspace)]}

    def global_memory(self, session, actor, offset=0):
        self.spaces._student(actor)
        condition = (Workspace.owner_id == actor.account.id, Workspace.kind == "project")
        rows = session.scalars(select(Workspace).where(*condition).order_by(Workspace.created_at.desc(), Workspace.id).offset(offset).limit(20))
        return {"scope": "global", "project_count": session.scalar(select(func.count()).select_from(Workspace).where(*condition)),
                "items": [self.workspace_memory(session, workspace) for workspace in rows]}
