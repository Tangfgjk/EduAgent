"""Replay a fixed evidence set in persisted event order, without external calls."""
from dataclasses import dataclass, field
from datetime import datetime

from app.learning.mastery_port import update_mastery
from app.learning.review_port import require_aware, update_retention
from app.learning.schema import LearningEvidence, MasteryState, RetentionState


@dataclass
class ReplayResult:
    mastery: dict[str, MasteryState] = field(default_factory=dict)
    retention: dict[str, RetentionState] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    skip_reasons: dict[str, dict[str, str]] = field(default_factory=dict)


def replay(evidences: list[LearningEvidence], learner_id: str, as_of: datetime) -> ReplayResult:
    """Use the caller's fixed set, filter occurred_at, and remove superseded facts.

    Event sequence is authoritative. A late retention observation is consumed
    at max(previous effective review time, occurred_at), with an explicit replay
    annotation. The immutable fact keeps its original occurred_at. This is a
    versioned processing-time policy, not a reconstruction of chronological truth.
    """
    require_aware(as_of)
    selected = [item for item in evidences if item.learner_id == learner_id
                and item.occurred_at <= as_of]
    by_id, by_seq = {}, {}
    for item in selected:
        if item.event_seq is None:
            raise ValueError("Replay requires persisted event_seq")
        if item.evidence_id in by_id and by_id[item.evidence_id] != item:
            raise ValueError("Conflicting evidence identity")
        if item.event_seq in by_seq and by_seq[item.event_seq] != item.evidence_id:
            raise ValueError("Conflicting event sequence")
        by_id[item.evidence_id], by_seq[item.event_seq] = item, item.evidence_id
    superseded = set()
    for item in by_id.values():
        if item.supersedes:
            target = by_id.get(item.supersedes)
            if target is None or target.event_seq >= item.event_seq:
                raise ValueError("Correction requires an earlier fact in the fixed replay set")
            if set(target.kc_refs) != set(item.kc_refs):
                raise ValueError("KC mapping corrections require a separate asset migration")
            superseded.add(item.supersedes)
    ordered = sorted((item for item in by_id.values() if item.evidence_id not in superseded),
                     key=lambda item: item.event_seq)
    result = ReplayResult(evidence_refs=[item.evidence_id for item in ordered])
    for item in ordered:
        for kc_id in dict.fromkeys(item.kc_refs):
            mastery = result.mastery.setdefault(kc_id, MasteryState(learner_id=learner_id, kc_id=kc_id))
            retention = result.retention.setdefault(kc_id, RetentionState(learner_id=learner_id, kc_id=kc_id))
            result.mastery[kc_id], mastery_skip = update_mastery(mastery, item)
            result.retention[kc_id], retention_skip = update_retention(retention, item)
            if retention_skip == "late_evidence_requires_replay":
                effective = item.model_copy(update={"occurred_at": retention.last_review_at})
                result.retention[kc_id], ignored = update_retention(retention, effective)
                assert ignored is None
                retention_skip = "late_evidence_replayed_at_effective_time"
            reasons = result.skip_reasons.setdefault(item.evidence_id, {})
            if mastery_skip:
                reasons[f"mastery:{kc_id}"] = mastery_skip
            if retention_skip:
                reasons[f"retention:{kc_id}"] = retention_skip
    return result
