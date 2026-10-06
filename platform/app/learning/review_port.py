"""Versioned deterministic exponential retention, with explicit time inputs."""
from __future__ import annotations

from datetime import datetime, timedelta
from math import exp, log

from app.learning.mastery_port import observation_skip_reason
from app.learning.schema import LearningEvidence, RetentionState, ReviewTask

SCHEDULER_VERSION = "exponential-v1"
RETENTION_PARAMETERS = "retention-default-v1"
TARGET_RETRIEVABILITY = 0.8
RECALL_ASSESSMENTS = frozenset({"review", "pre", "post", "transfer"})


def require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit timezone-aware time is required")


def update_retention(state: RetentionState, evidence: LearningEvidence) -> tuple[RetentionState, str | None]:
    """Only independent recall changes retention; late observations require replay."""
    if (state.scheduler_version, state.parameter_set_id) != (SCHEDULER_VERSION, RETENTION_PARAMETERS):
        raise ValueError("Unsupported retention scheduler or parameters")
    reason = observation_skip_reason(state, evidence)
    if reason:
        return state, reason
    if evidence.assessment_kind not in RECALL_ASSESSMENTS:
        return state, "not_recall_assessment"
    if state.last_review_at and evidence.occurred_at < state.last_review_at:
        return state, "late_evidence_requires_replay"
    if evidence.verdict_status == "passed":
        stability = min(3650.0, max(0.25, state.stability) * 1.8)
        difficulty = max(0.0, state.difficulty - 0.05)
        lapses = state.lapse_count
    else:
        stability = max(0.25, state.stability * 0.35)
        difficulty = min(1.0, state.difficulty + 0.1)
        lapses = state.lapse_count + 1
    interval = timedelta(days=max(0.05, -log(TARGET_RETRIEVABILITY) * stability))
    return state.model_copy(update={
        "state_version": state.state_version + 1,
        "stability": stability, "difficulty": difficulty, "lapse_count": lapses,
        "last_review_at": evidence.occurred_at,
        "next_review_at": evidence.occurred_at + interval,
        "evidence_refs": [*state.evidence_refs, evidence.evidence_id],
    }), None


def retrievability(state: RetentionState, as_of: datetime) -> float:
    """R(t)=exp(-elapsed_days/stability); future reviews cannot affect the past."""
    require_aware(as_of)
    if state.last_review_at is None:
        return 0.0
    if as_of < state.last_review_at:
        raise ValueError("as_of precedes the retention snapshot; rebuild an historical snapshot")
    elapsed_days = (as_of - state.last_review_at).total_seconds() / 86400
    return exp(-elapsed_days / max(0.25, state.stability))


def review_task(state: RetentionState, as_of: datetime) -> ReviewTask | None:
    """Emit an actionable due task; never read wall-clock time."""
    require_aware(as_of)
    if state.last_review_at is None or state.next_review_at is None:
        return None
    probability = retrievability(state, as_of)
    if as_of < state.next_review_at:
        return None
    return ReviewTask(task_id=f"review:{state.learner_id}:{state.kc_id}:{state.state_version}",
        learner_id=state.learner_id, kc_id=state.kc_id, due_at=state.next_review_at, as_of=as_of,
        retrievability=probability, forgetting_risk=1 - probability, state_version=state.state_version,
        evidence_refs=list(state.evidence_refs), reason="Scheduled recall is due under exponential-v1")
