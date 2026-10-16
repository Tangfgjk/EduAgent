"""停服维护：只对可核对的 S05 检查点重建 L1，不重跑模型或覆盖原记录。"""
import base64
from copy import deepcopy
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import func, select

from app.infrastructure.db.models import Account, AgentCheckpoint, AgentRun, AnswerReview, LearningFact, LearningInput, OutboxJob, RecallAttempt, Workspace
from app.modules.learning.tutor_graph import LEGACY_VERSION, advance_tutor, version_for
from app.modules.learning.content_catalog import PACKS
from app.modules.memory.facts import publish_judgment, publish_turn
from app.modules.memory.projections import rebuild


def backfill(db, apply=False):
    counts = {"checked": 0, "eligible": 0, "skipped_version": 0, "incomplete": 0, "existing": 0}
    with db.transaction() as session:
        ids = list(session.scalars(select(AgentRun.id).where(AgentRun.kind == "tutor").order_by(AgentRun.created_at, AgentRun.id)))
    for rid in ids:
        with db.transaction() as session:
            run = session.get(AgentRun, rid)
            workspace = session.get(Workspace, run.workspace_id)
            account = session.get(Account, workspace.owner_id, with_for_update=True)
            session.get(Workspace, run.workspace_id, with_for_update=True)
            run = session.get(AgentRun, rid, with_for_update=True, populate_existing=True)
            counts["checked"] += 1
            if run.version not in {LEGACY_VERSION, *(version_for(item.unit_id) for item in PACKS)}:
                counts["skipped_version"] += 1
                continue
            checkpoint = session.get(AgentCheckpoint, rid)
            if checkpoint is None:
                counts["incomplete"] += 1
                continue
            with sqlite3.connect(":memory:", check_same_thread=False) as conn:
                conn.deserialize(base64.b64decode(checkpoint.image, validate=True))
                saved = SqliteSaver(conn).get_tuple({"configurable": {"thread_id": rid}})
                if saved is None:
                    counts["incomplete"] += 1
                    continue
                turn = saved.checkpoint["channel_values"]["turn"]
            entries = list(session.scalars(select(LearningInput).join(OutboxJob, OutboxJob.input_id == LearningInput.id).where(
                LearningInput.conversation_id == run.conversation_id, OutboxJob.status == "completed")))
            starts = [entry for entry in entries if entry.payload.get("operation") == "tutor_start"]
            inputs = sorted([entry for entry in entries if entry.payload.get("operation") == "tutor_input"],
                            key=lambda entry: entry.payload["expected_revision"])
            if len(starts) != 1 or len(inputs) < turn:
                counts["incomplete"] += 1
                continue
            state, image, _ = advance_tutor(rid, account.learner_id, starts[0].payload, None)
            turns = []
            for entry in inputs[:turn]:
                before = state["view"]
                state, image, _ = advance_tutor(rid, account.learner_id, entry.payload, image)
                if entry.payload["action"] in {"answer", "hint", "reflect", "skip"}:
                    turns.append((entry, before, state))
            # 对不上已保存视图就整体跳过，不能猜测历史结果。
            comparable = deepcopy(state["view"])
            if run.version == LEGACY_VERSION:
                comparable.pop("strategy_version_id", None)
                comparable.pop("reflection_prompt", None)
            if comparable != run.view:
                counts["incomplete"] += 1
                continue
            existing = set(session.scalars(select(LearningFact.input_id).where(LearningFact.run_id == rid)))
            missing = [item for item in turns if item[0].id not in existing]
            counts["existing"] += len(turns) - len(missing)
            if missing and session.scalar(select(func.count()).select_from(RecallAttempt).where(RecallAttempt.workspace_id == run.workspace_id)):
                # 有新复做后不补插更旧事实并重置现行安排，交由显式历史协调。
                counts["incomplete"] += 1
                continue
            counts["eligible"] += len(missing)
            if apply:
                for entry, before, state in missing:
                    publish_turn(session, run, entry, before, state, refresh=False)
                for review in session.scalars(select(AnswerReview).where(AnswerReview.run_id == rid, AnswerReview.status == "resolved")):
                    publish_judgment(session, review, review.reviewer_id, refresh=False)
                rebuild(session, run.workspace_id)
    return counts
