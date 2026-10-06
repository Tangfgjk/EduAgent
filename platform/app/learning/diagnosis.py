"""Deterministic first-session diagnosis; unknown never implies mastery."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.learning.assets import AssetCatalog, VersionedRef


class DiagnosticObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    evidence_id: str = Field(min_length=1)
    learner_id: str = Field(min_length=1)
    event_seq: int | None = Field(default=None, ge=1)
    assessment_ref: VersionedRef
    response: Literal["correct", "incorrect", "partial", "unverifiable", "skipped", "dont_know", "guessed"]
    hint_level: int = Field(default=0, ge=0)
    answer_exposed: bool = False
    assistance_mode: Literal["none", "hint", "worked_example", "teacher"] = "none"


class DiagnosticDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["task", "complete", "needs_learning", "budget_exhausted", "missing_assets"]
    assessment_ref: VersionedRef | None = None
    kc_ref: VersionedRef | None = None
    reason: str
    evidence_refs: tuple[str, ...] = ()
    algorithm_version: str = "deterministic-diagnosis-v1"
    catalog_version: str


def next_task(catalog: AssetCatalog, target_kc_refs: tuple[VersionedRef, ...],
              observations: tuple[DiagnosticObservation, ...] = (), *,
              learner_id: str, max_tasks: int = 8, required_correct: int = 2) -> DiagnosticDecision:
    if max_tasks < 1 or required_correct < 1 or not target_kc_refs:
        raise ValueError("positive diagnosis budget, evidence count and targets required")
    order = catalog.topological_order(target_kc_refs)
    assets = {asset.ref.key: asset for asset in catalog.assessments}
    knowledge = {asset.ref.key: asset for asset in catalog.knowledge}
    by_id: dict[str, DiagnosticObservation] = {}
    for observation in observations:
        if observation.learner_id != learner_id:
            continue
        if observation.assessment_ref.key not in assets:
            raise ValueError("diagnostic observation references unknown assessment version")
        observed_asset = assets[observation.assessment_ref.key]
        if observed_asset.kind != "diagnostic" or not any(ref.key in order for ref in observed_asset.kc_refs):
            continue
        if observation.evidence_id in by_id and by_id[observation.evidence_id] != observation:
            raise ValueError("conflicting diagnostic evidence ID")
        by_id[observation.evidence_id] = observation
    unique = tuple(by_id.values())
    evidence_refs = tuple(sorted(by_id))
    attempted = {observation.assessment_ref.key for observation in unique}
    for key in order:
        # Only direct single-KC diagnostic items establish an initial readiness signal.
        items = sorted((asset for asset in catalog.assessments
                        if asset.kind == "diagnostic" and len(asset.kc_refs) == 1
                        and asset.kc_refs[0].key == key), key=lambda item: item.ref.key)
        item_keys = {item.ref.key for item in items}
        eligible = [observation for observation in unique
                    if observation.assessment_ref.key in item_keys
                    and observation.hint_level == 0 and not observation.answer_exposed
                    and observation.assistance_mode == "none"]
        # A fresh independent retry may follow remediation. It replaces only the
        # diagnostic readiness signal, while the original evidence stays intact.
        latest: dict[str, DiagnosticObservation] = {}
        for observation in eligible:
            previous = latest.get(observation.assessment_ref.key)
            if previous is None:
                latest[observation.assessment_ref.key] = observation
            else:
                if previous.event_seq is None or observation.event_seq is None:
                    raise ValueError("event_seq required for repeated diagnostic attempts")
                if previous.event_seq == observation.event_seq:
                    raise ValueError("diagnostic event_seq must distinguish repeated attempts")
                if observation.event_seq > previous.event_seq:
                    latest[observation.assessment_ref.key] = observation
        eligible = list(latest.values())
        correct_items = {observation.assessment_ref.key for observation in eligible
                         if observation.response == "correct"}
        kwargs = dict(kc_ref=knowledge[key].ref,
                      evidence_refs=evidence_refs, catalog_version=catalog.catalog_version)
        if any(observation.response in ("incorrect", "dont_know") for observation in eligible):
            return DiagnosticDecision(status="needs_learning", reason="prerequisite_or_target_diagnostic_gap", **kwargs)
        if len(correct_items) >= required_correct:
            continue
        if len(unique) >= max_tasks:
            return DiagnosticDecision(status="budget_exhausted", reason="diagnostic_budget_reached_unknown_state", **kwargs)
        available = [item for item in items if item.ref.key not in attempted]
        if not available:
            return DiagnosticDecision(status="missing_assets", reason="insufficient_distinct_diagnostic_items", **kwargs)
        return DiagnosticDecision(status="task", assessment_ref=available[0].ref,
                                  reason="probe_unknown_prerequisite_or_target", **kwargs)
    return DiagnosticDecision(status="complete", reason="initial_readiness_not_mastery", evidence_refs=evidence_refs,
                              catalog_version=catalog.catalog_version)
