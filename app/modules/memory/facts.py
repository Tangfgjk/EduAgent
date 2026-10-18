from sqlalchemy import select

from app.infrastructure.db.models import FactJudgment, LearningFact, ReviewCard, now
from app.modules.learning.content_catalog import get_pack
from app.modules.memory.projections import rebuild


def add_card_source(session, fact, reason):
    card = session.scalar(select(ReviewCard).where(ReviewCard.workspace_id == fact.workspace_id,
        ReviewCard.task_id == fact.task_id, ReviewCard.content_version == fact.content_version,
        ReviewCard.source_kind == fact.source_kind).with_for_update())
    if card is None:
        card = ReviewCard(workspace_id=fact.workspace_id, task_id=fact.task_id,
            content_version=fact.content_version, source_kind=fact.source_kind,
            source_fact_ids=[fact.id], reasons=[reason], next_due_at=fact.created_at)
        session.add(card)
    elif fact.id not in card.source_fact_ids:
        card.source_fact_ids = [*card.source_fact_ids, fact.id]
        card.reasons = sorted(set([*card.reasons, reason]))
        card.next_due_at, card.schedule_step = now(), 0
        card.revision += 1


def publish_turn(session, run, entry, before, state, refresh=True):
    action = entry.payload.get("action")
    if action not in {"answer", "hint", "reflect", "skip"}:
        return None
    existing = session.scalar(select(LearningFact).where(LearningFact.input_id == entry.id))
    if existing:
        return existing
    pack = get_pack(before["unit_id"], session)
    task = next(item for item in pack.tasks if item["id"] == before["task"]["task_id"])
    status = state["view"]["feedback"]["status"] if action == "answer" else "skipped" if action == "skip" else None
    fact = LearningFact(workspace_id=run.workspace_id, run_id=run.id, input_id=entry.id,
        kind="reflection" if action == "reflect" else action, source_kind=entry.source_kind,
        task_id=task["id"], content_version=pack.version, created_at=entry.created_at,
        payload={"assessment_exposure": before["task"].get("exposure_status"), "support_decision": before.get("support_decision", {}), "stage": task["stage"], "status": status, "hints_used": len(state["view"]["hints"]) if action == "hint" else len(before["hints"]),
            "attempt": before["attempts"] + (1 if action == "answer" else 0),
            "turn": state["turn"], "task_snapshot": task,
            "revealed_hint": state["view"]["hints"][-1] if action == "hint" else None,
            "verifier": "bounded-rational.v1" if action == "answer" else None})
    session.add(fact)
    session.flush()
    if status == "failed":
        add_card_source(session, fact, "numeric_error")
    elif status == "passed" and task["stage"] == "practice" and before["hints"]:
        add_card_source(session, fact, "assisted_success")
    if refresh:
        rebuild(session, run.workspace_id)
    return fact


def publish_judgment(session, review, actor_id, refresh=True):
    fact = session.scalar(select(LearningFact).where(LearningFact.input_id == review.input_id))
    if fact is None:
        return  # 旧版记录待经核对的历史重建；不臆造 L1 来源。
    existing = session.scalar(select(FactJudgment).where(FactJudgment.review_id == review.id))
    if existing:
        return
    session.add(FactJudgment(fact_id=fact.id, review_id=review.id, conclusion=review.conclusion,
        note=review.note, actor_id=actor_id))
    if review.conclusion == "incorrect":
        add_card_source(session, fact, "human_review")
    if refresh:
        rebuild(session, fact.workspace_id)
