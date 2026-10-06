"""Offline pilot measurement admission; synthetic samples never become human evidence."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Callable, Literal

from pydantic import AwareDatetime, Field, model_validator

from app.learning.assets import VersionedRef
from app.learning.course_governance import Contract, CourseReviewPackage, MeasurementPhase, structural_issues


class MeasurementRecord(Contract):
    evidence_ref: str = Field(min_length=1)
    learner_ref: str = Field(min_length=1)
    assessment_ref: VersionedRef
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    phase: MeasurementPhase
    source_kind: Literal["synthetic_ai_generated", "real_learning_data"]
    purpose: Literal["simulation_precalibration", "research"]
    research_consent_ref: str | None = None
    occurred_at: AwareDatetime
    attempt: int = Field(ge=1)
    hint_level: int = Field(default=0, ge=0)
    assistance_mode: Literal["none", "hint", "answer"] = "none"
    answer_exposed: bool = False
    verified: bool = True
    score: float = Field(ge=0, le=1)


class MeasurementAdmission(Contract):
    purpose: Literal["simulation_precalibration", "research"]
    admitted_records: tuple[MeasurementRecord, ...]
    excluded_counts: dict[str, int]
    missing_blueprint_cells: tuple[str, ...]
    as_of: AwareDatetime
    package_sha256: str
    educational_effect_claim: Literal[False] = False
    automatic_parameter_promotion: Literal[False] = False
    limitations: tuple[str, ...] = ("descriptive_not_causal", "missingness_requires_review",
        "synthetic_does_not_replace_human_calibration", "course_and_pilot_approval_are_separate")


def admit_measurements(records: tuple[MeasurementRecord, ...], package: CourseReviewPackage, *,
                       purpose: Literal["simulation_precalibration", "research"],
                       lesson_anchor: datetime, as_of: datetime,
                       authorize_research: Callable[[MeasurementRecord], bool] | None = None,
                       verify_record_history: Callable[[MeasurementRecord], bool] | None = None,
                       verify_course_admission: Callable[[CourseReviewPackage], bool] | None = None) -> MeasurementAdmission:
    """Use half-open windows and first attempts, including unsuccessful ones.

    For research, a trusted history verifier must confirm actual evidence and
    first exposure across practice/hints (not just this measurement subset).
    Course approval must also be verified independently of learner requests.
    """
    if any(value.tzinfo is None or value.utcoffset() is None for value in (lesson_anchor, as_of)):
        raise ValueError("explicit timezone-aware lesson anchor and as_of required")
    if purpose not in {"simulation_precalibration", "research"}:
        raise ValueError("unsupported measurement purpose")
    issues = structural_issues(package)
    if issues:
        raise ValueError("course package structural gate failed: " + ",".join(issues))
    unique = {}
    for record in records:
        if record.evidence_ref in unique and unique[record.evidence_ref] != record:
            raise ValueError("conflicting measurement evidence identity")
        unique[record.evidence_ref] = record
    assets = {asset.ref.key: asset for asset in package.catalog.assessments}
    windows = {window.phase: window for window in package.measurement_windows}
    admitted, excluded = [], Counter()
    seen_items = set()
    learners = {record.learner_ref for record in unique.values()}
    for record in sorted(unique.values(), key=lambda item: (item.occurred_at, item.evidence_ref)):
        key = (record.learner_ref, record.assessment_ref.asset_id)
        repeated = key in seen_items
        seen_items.add(key)
        asset = assets.get(record.assessment_ref.key)
        expected_source = "synthetic_ai_generated" if purpose == "simulation_precalibration" else "real_learning_data"
        reason = None
        if record.source_kind != expected_source or record.purpose != purpose:
            reason = "source_or_purpose_mismatch"
        elif purpose == "research" and (verify_course_admission is None or not verify_course_admission(package)):
            reason = "approved_course_unverified"
        elif record.package_sha256 != package.content_hash:
            reason = "course_version_mismatch"
        elif asset is None or asset.kind != record.phase:
            reason = "assessment_phase_mismatch"
        elif record.occurred_at > as_of:
            reason = "future_observation"
        elif purpose == "research" and (not record.research_consent_ref or authorize_research is None or not authorize_research(record)):
            reason = "current_research_authorization_missing"
        elif purpose == "research" and (verify_record_history is None or not verify_record_history(record)):
            reason = "evidence_or_first_exposure_history_unverified"
        elif record.hint_level or record.assistance_mode != "none" or record.answer_exposed:
            reason = "assisted_observation"
        elif not record.verified:
            reason = "unverified_observation"
        elif record.attempt != 1 or repeated:
            reason = "repeated_or_nonfirst_attempt"
        else:
            window = windows[record.phase]
            offset = (record.occurred_at - lesson_anchor).total_seconds() / 60
            if not window.start_offset_minutes <= offset < window.end_offset_minutes:
                reason = "outside_measurement_window"
        if reason:
            excluded[reason] += 1
        else:
            admitted.append(record)
    missing = []
    for learner in sorted(learners):
        for kc in package.catalog.knowledge:
            for phase, window in windows.items():
                keys = {record.assessment_ref.asset_id for record in admitted if record.learner_ref == learner
                        and record.phase == phase and kc.ref in assets[record.assessment_ref.key].kc_refs}
                if len(keys) < window.min_independent_items_per_kc:
                    missing.append(f"{learner}:{kc.ref.key}:{phase}:{len(keys)}/{window.min_independent_items_per_kc}")
    return MeasurementAdmission(purpose=purpose, admitted_records=tuple(admitted),
        excluded_counts=dict(sorted(excluded.items())), missing_blueprint_cells=tuple(missing),
        as_of=as_of, package_sha256=package.content_hash)


class OfflinePartition(Contract):
    training: tuple[MeasurementRecord, ...]
    validation: tuple[MeasurementRecord, ...]
    heldout_learner_refs: tuple[str, ...] = Field(min_length=1)
    heldout_assessment_ids: tuple[str, ...] = Field(min_length=1)
    dropped_cross_partition_count: int = Field(ge=0)
    automatic_parameter_promotion: Literal[False] = False

    @model_validator(mode="after")
    def no_leakage(self):
        if {row.learner_ref for row in self.training} & {row.learner_ref for row in self.validation}:
            raise ValueError("learner leakage across offline partitions")
        if {row.assessment_ref.asset_id for row in self.training} & {row.assessment_ref.asset_id for row in self.validation}:
            raise ValueError("assessment leakage across offline partitions")
        return self


def offline_partition(admission: MeasurementAdmission, *, heldout_learner_refs: tuple[str, ...],
                      heldout_assessment_ids: tuple[str, ...]) -> OfflinePartition:
    """Explicit two-axis holdout; cross-cells are dropped, never fitted or validated."""
    if not heldout_learner_refs or not heldout_assessment_ids:
        raise ValueError("learner and item holdouts must be explicit and nonempty")
    learners, items = set(heldout_learner_refs), set(heldout_assessment_ids)
    train, validation, dropped = [], [], 0
    for row in admission.admitted_records:
        learner_holdout, item_holdout = row.learner_ref in learners, row.assessment_ref.asset_id in items
        if learner_holdout and item_holdout:
            validation.append(row)
        elif not learner_holdout and not item_holdout:
            train.append(row)
        else:
            dropped += 1
    if not train or not validation:
        raise ValueError("offline evaluation requires nonempty training and validation cells")
    return OfflinePartition(training=tuple(train), validation=tuple(validation),
        heldout_learner_refs=tuple(sorted(learners)), heldout_assessment_ids=tuple(sorted(items)),
        dropped_cross_partition_count=dropped)
