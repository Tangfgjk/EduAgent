"""Immutable course review bundles and fail-closed, externally verified admission.

Structural checks are not teacher approval. Review authority and source rights
must be supplied by a trusted application, never by learner request booleans.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.learning.assets import AssetCatalog, VersionedRef


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CourseSource(Contract):
    source_kind: Literal["synthetic_ai_generated", "local_authored", "licensed_curriculum"]
    locator: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    license_ref: str = Field(min_length=1)
    usage_scope: Literal["simulation_only", "classroom_candidate"]
    grade_band: str = Field(min_length=1)


class KnowledgeReview(Contract):
    kc_ref: VersionedRef
    observable_objective: str = Field(min_length=10)
    positive_example: str = Field(min_length=5)
    negative_example: str = Field(min_length=5)


class MisconceptionReview(Contract):
    misconception_id: str = Field(min_length=1)
    kc_refs: tuple[VersionedRef, ...] = Field(min_length=1)
    description: str = Field(min_length=5)
    positive_error_example: str = Field(min_length=5)
    negative_error_example: str = Field(min_length=5)
    evidence_basis: str = Field(min_length=5)
    intervention: str = Field(min_length=5)


class HintStep(Contract):
    level: int = Field(ge=0, le=3)
    text: str = Field(min_length=5)
    answer_exposed: bool = False


class TaskReview(Contract):
    assessment_ref: VersionedRef
    difficulty_basis: str = Field(min_length=10)
    bloom_requirement: str = Field(min_length=10)
    misconception_refs: tuple[str, ...] = Field(min_length=1)
    hints: tuple[HintStep, ...] = Field(min_length=2)
    fade_condition: str = Field(min_length=10)
    transfer_difference: str | None = Field(default=None, min_length=10)

    @model_validator(mode="after")
    def hint_ladder(self):
        if tuple(hint.level for hint in self.hints) != tuple(range(len(self.hints))):
            raise ValueError("hint ladder must be contiguous from zero")
        if any(hint.answer_exposed for hint in self.hints):
            raise ValueError("course scaffolds cannot expose the final answer")
        if len(self.misconception_refs) != len(set(self.misconception_refs)):
            raise ValueError("duplicate task misconception reference")
        return self


class RubricReview(Contract):
    rubric_ref: VersionedRef
    level_descriptions: tuple[str, ...] = Field(min_length=2)
    positive_example: str = Field(min_length=5)
    negative_example: str = Field(min_length=5)
    scoring_procedure: str = Field(min_length=10)
    disagreement_procedure: str = Field(min_length=10)


MeasurementPhase = Literal["pretest", "posttest", "transfer", "delayed"]


class MeasurementWindow(Contract):
    phase: MeasurementPhase
    start_offset_minutes: int
    end_offset_minutes: int
    min_independent_items_per_kc: int = Field(default=2, ge=2, le=20)
    unassisted_required: Literal[True] = True

    @model_validator(mode="after")
    def ordered(self):
        if self.start_offset_minutes >= self.end_offset_minutes:
            raise ValueError("measurement window must be nonempty")
        return self


class CourseReviewPackage(Contract):
    schema_version: Literal["course-review-package-v3.1"] = "course-review-package-v3.1"
    course_ref: VersionedRef
    title: str = Field(min_length=1)
    created_at: AwareDatetime
    review_status: Literal["draft", "pending", "reviewed", "rejected"] = "pending"
    source: CourseSource
    catalog: AssetCatalog
    knowledge_reviews: tuple[KnowledgeReview, ...] = Field(min_length=1)
    task_reviews: tuple[TaskReview, ...] = Field(min_length=1)
    rubric_reviews: tuple[RubricReview, ...] = Field(min_length=1)
    misconceptions: tuple[MisconceptionReview, ...] = Field(min_length=1)
    measurement_windows: tuple[MeasurementWindow, ...] = Field(min_length=4, max_length=4)
    educational_effect_claim: Literal[False] = False
    real_learner_data_included: Literal[False] = False

    @property
    def content_hash(self) -> str:
        serialized = json.dumps(self.model_dump(mode="json"), ensure_ascii=False,
                                sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def load(cls, path: Path | str) -> "CourseReviewPackage":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8-sig"))


class CourseReview(Contract):
    review_ref: str = Field(min_length=1)
    reviewer_ref: str = Field(min_length=1)
    reviewed_at: AwareDatetime
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approve", "revise", "reject"]
    reviewed_assessment_refs: tuple[VersionedRef, ...] = Field(min_length=1)
    comments: str = Field(min_length=10)


class CourseAdmissionReport(Contract):
    course_ref: VersionedRef
    package_sha256: str
    as_of: AwareDatetime
    structural_issues: tuple[str, ...]
    simulation_admitted: bool
    human_pilot_admitted: bool
    human_pilot_blockers: tuple[str, ...]
    review_ref: str | None = None
    assessment_count: int
    kc_count: int
    educational_effect_claim: Literal[False] = False


def _coverage_issues(actual, expected, label):
    if len(actual) != len(set(actual)):
        yield f"duplicate_{label}"
    if set(actual) != set(expected):
        yield f"incomplete_{label}_coverage"


def structural_issues(package: CourseReviewPackage) -> tuple[str, ...]:
    """Check the complete frozen snapshot, not labels or review_status alone."""
    catalog = package.catalog
    issues = []
    kc_keys = {item.ref.key for item in catalog.knowledge}
    assets = {item.ref.key: item for item in catalog.assessments}
    rubric_keys = {item.ref.key for item in catalog.rubrics}
    issues.extend(_coverage_issues([item.kc_ref.key for item in package.knowledge_reviews], kc_keys, "kc_review"))
    issues.extend(_coverage_issues([item.assessment_ref.key for item in package.task_reviews], assets, "task_review"))
    issues.extend(_coverage_issues([item.rubric_ref.key for item in package.rubric_reviews], rubric_keys, "rubric_review"))
    misconception_ids = [item.misconception_id for item in package.misconceptions]
    if len(misconception_ids) != len(set(misconception_ids)):
        issues.append("duplicate_misconception")
    for misconception in package.misconceptions:
        if any(ref.key not in kc_keys for ref in misconception.kc_refs):
            issues.append("unknown_misconception_kc")
    windows = {window.phase: window for window in package.measurement_windows}
    if set(windows) != {"pretest", "posttest", "transfer", "delayed"}:
        issues.append("incomplete_measurement_windows")
    else:
        if windows["pretest"].end_offset_minutes > windows["posttest"].start_offset_minutes:
            issues.append("pre_post_windows_overlap")
        if windows["posttest"].end_offset_minutes > windows["transfer"].start_offset_minutes:
            issues.append("post_transfer_windows_overlap")
        if windows["transfer"].end_offset_minutes > windows["delayed"].start_offset_minutes:
            issues.append("transfer_delayed_windows_overlap")
        if windows["delayed"].start_offset_minutes < 1440:
            issues.append("delayed_window_not_delayed")
    for task in package.task_reviews:
        asset = assets.get(task.assessment_ref.key)
        if asset is None:
            continue
        if any(ref not in misconception_ids for ref in task.misconception_refs):
            issues.append(f"unknown_task_misconception:{asset.ref.key}")
        if asset.kind == "transfer" and task.transfer_difference is None:
            issues.append(f"transfer_context_missing:{asset.ref.key}")
        if not asset.answer:
            issues.append(f"verifiable_answer_missing:{asset.ref.key}")
        variable, answer = asset.answer.get("var"), asset.answer.get("value")
        if variable and answer is not None:
            direct_answer = re.compile(rf"(?i)(?<![a-z0-9_]){re.escape(str(variable))}\s*[=＝]\s*"
                                       rf"{re.escape(str(answer))}(?![a-z0-9_.])")
            if any(direct_answer.search(hint.text) for hint in task.hints):
                issues.append(f"unmarked_direct_answer_hint:{asset.ref.key}")
        for misconception in package.misconceptions:
            if misconception.misconception_id in task.misconception_refs and not (
                    {ref.key for ref in misconception.kc_refs} & {ref.key for ref in asset.kc_refs}):
                issues.append(f"misconception_kc_mismatch:{asset.ref.key}")
    for kc_key in sorted(kc_keys):
        kc_assets = [asset for asset in assets.values() if kc_key in {ref.key for ref in asset.kc_refs}]
        for phase in ("diagnostic", "practice", "pretest", "posttest", "transfer", "delayed"):
            phase_assets = [asset for asset in kc_assets if asset.kind == phase]
            required = windows[phase].min_independent_items_per_kc if phase in windows else 1
            if len({asset.ref.asset_id for asset in phase_assets}) < required:
                issues.append(f"blueprint_coverage_missing:{kc_key}:{phase}")
        measured = [asset for asset in kc_assets if asset.kind in windows]
        stems = [" ".join(asset.stem.split()) for asset in measured]
        if len(stems) != len(set(stems)):
            issues.append(f"repeated_measurement_stem:{kc_key}")
        ids = [asset.ref.asset_id for asset in measured]
        if len(ids) != len(set(ids)):
            issues.append(f"repeated_measurement_item:{kc_key}")
        pre = {(asset.rubric_ref.key, asset.difficulty_band, asset.comparison_group)
               for asset in kc_assets if asset.kind == "pretest"}
        for phase in ("posttest", "transfer", "delayed"):
            post = {(asset.rubric_ref.key, asset.difficulty_band, asset.comparison_group)
                    for asset in kc_assets if asset.kind == phase}
            if not pre or pre != post:
                issues.append(f"incomparable_blueprint:{kc_key}:{phase}")
    return tuple(sorted(set(issues)))


def admit_course(package: CourseReviewPackage, *, as_of: datetime, review: CourseReview | None = None,
                 verify_review: Callable[[CourseReview], bool] | None = None,
                 verify_source_rights: Callable[[CourseSource], bool] | None = None) -> CourseAdmissionReport:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("explicit timezone-aware as_of required")
    issues = structural_issues(package)
    blockers = list(issues)
    if package.created_at > as_of:
        blockers.append("future_course_package")
    if package.review_status == "rejected":
        blockers.append("course_rejected")
    if package.source.usage_scope != "classroom_candidate":
        blockers.append("source_simulation_only")
    if verify_source_rights is None or not verify_source_rights(package.source):
        blockers.append("source_rights_unverified")
    if review is None:
        blockers.append("human_review_missing")
    else:
        if verify_review is None or not verify_review(review):
            blockers.append("review_authority_unverified")
        if review.package_sha256 != package.content_hash:
            blockers.append("review_version_hash_mismatch")
        if not package.created_at <= review.reviewed_at <= as_of:
            blockers.append("review_time_invalid")
        if review.decision != "approve":
            blockers.append("human_approval_missing")
        reviewed = [ref.key for ref in review.reviewed_assessment_refs]
        blockers.extend(_coverage_issues(reviewed, [asset.ref.key for asset in package.catalog.assessments], "teacher_task"))
    return CourseAdmissionReport(course_ref=package.course_ref, package_sha256=package.content_hash,
        as_of=as_of, structural_issues=issues,
        simulation_admitted=not issues and package.created_at <= as_of and package.review_status != "rejected",
        human_pilot_admitted=not blockers, human_pilot_blockers=tuple(sorted(set(blockers))),
        review_ref=review.review_ref if review else None,
        assessment_count=len(package.catalog.assessments), kc_count=len(package.catalog.knowledge))
