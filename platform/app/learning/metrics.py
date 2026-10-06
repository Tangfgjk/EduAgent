"""Comparable unassisted measurements; scores do not imply causal efficacy."""
from __future__ import annotations

from datetime import datetime
from statistics import mean
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.learning.assets import AssetCatalog, VersionedRef
from app.learning.schema import LearningEvidence


class PerformanceObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    evidence_id: str = Field(min_length=1)
    learner_id: str = Field(min_length=1)
    kc_ref: VersionedRef
    assessment_ref: VersionedRef
    rubric_ref: VersionedRef
    comparison_group: str = Field(min_length=1)
    difficulty_band: str = Field(min_length=1)
    assessment_kind: Literal["pretest", "posttest", "transfer", "delayed", "practice"]
    score: float = Field(ge=0, le=1)
    occurred_at: AwareDatetime
    hint_level: int = Field(default=0, ge=0)
    assistance_mode: str = "none"
    answer_exposed: bool = False
    verified: bool = True
    confidence: float = Field(default=1, ge=0, le=1)
    action_id: str | None = None
    policy_version: str | None = None


class PerformanceGain(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["comparable", "insufficient_data", "incomparable"]
    reason: str
    pre_mean: float | None = None
    post_mean: float | None = None
    gain: float | None = None
    pre_count: int = 0
    post_count: int = 0
    excluded_count: int = 0
    evidence_refs: tuple[str, ...] = ()
    assessment_refs: tuple[str, ...] = ()
    as_of: AwareDatetime
    window_start: AwareDatetime | None = None
    window_end: AwareDatetime | None = None
    metric_version: str = "comparable-unassisted-gain-v1"
    uncertainty_method: str = "descriptive_only_no_causal_or_statistical_claim"


def observation_from_evidence(evidence: LearningEvidence, catalog: AssetCatalog,
                              kc_ref: VersionedRef | None = None) -> PerformanceObservation | None:
    """Project authorized evidence using an exact asset version, never invented comparability.

    Authorization and correction selection belong to the caller's evidence repository.
    Unknown/mismatched assets return no measurement instead of a synthetic score.
    """
    if evidence.verifier_details.get("diagnostic_response") in {"skipped", "dont_know", "guessed"}:
        return None
    details = evidence.verifier_details.get("original_verifier_details", evidence.verifier_details)
    if details.get("independence_verified") is False or details.get("first_exposure") is False:
        return None
    asset = next((item for item in catalog.assessments
                  if item.ref.asset_id == evidence.assessment_id
                  and item.ref.version == evidence.assessment_version), None)
    if asset is None or asset.kind not in {"pretest", "posttest", "transfer", "delayed"}:
        return None
    expected_kind = {"pretest": "pre", "posttest": "post", "transfer": "transfer", "delayed": "review"}[asset.kind]
    if evidence.assessment_kind != expected_kind or evidence.rubric_version != asset.rubric_ref.version:
        return None
    if evidence.difficulty_band != asset.difficulty_band:
        return None
    refs = [ref for ref in asset.kc_refs if ref.asset_id in evidence.kc_refs and (kc_ref is None or ref == kc_ref)]
    if len(refs) != 1:
        return None
    return PerformanceObservation(
        evidence_id=evidence.evidence_id, learner_id=evidence.learner_id, kc_ref=refs[0],
        assessment_ref=asset.ref, rubric_ref=asset.rubric_ref, comparison_group=asset.comparison_group,
        difficulty_band=asset.difficulty_band, assessment_kind=asset.kind, score=evidence.score,
        occurred_at=evidence.occurred_at, hint_level=evidence.hint_level, assistance_mode=evidence.assistance_mode,
        answer_exposed=evidence.answer_exposed, verified=evidence.verdict_status != "unverifiable",
        confidence=evidence.confidence if evidence.confidence is not None else 0,
        action_id=evidence.action_ref, policy_version=evidence.policy_version)


def performance_gain(observations: tuple[PerformanceObservation, ...], *, learner_id: str,
                     kc_ref: VersionedRef, as_of: datetime, min_samples: int = 2,
                     comparison_group: str | None = None, min_confidence: float = 0.7,
                     post_kind: Literal["posttest", "transfer", "delayed"] = "posttest") -> PerformanceGain:
    if min_samples < 1 or not 0 <= min_confidence <= 1:
        raise ValueError("invalid measurement parameters")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone aware")
    unique: dict[str, PerformanceObservation] = {}
    for observation in observations:
        if observation.learner_id != learner_id or observation.kc_ref != kc_ref:
            continue
        if observation.evidence_id in unique and unique[observation.evidence_id] != observation:
            raise ValueError("conflicting measurement evidence ID")
        unique[observation.evidence_id] = observation
    candidates = [item for item in unique.values() if item.occurred_at <= as_of
                  and item.assessment_kind in ("pretest", post_kind)
                  and (comparison_group is None or item.comparison_group == comparison_group)]
    eligible = [item for item in candidates if item.verified and item.confidence >= min_confidence
                and item.hint_level == 0 and item.assistance_mode == "none" and not item.answer_exposed]
    # Keep the first eligible attempt per item and phase; retries are not independent samples.
    distinct: dict[tuple[str, str], PerformanceObservation] = {}
    for item in sorted(eligible, key=lambda value: (value.occurred_at, value.evidence_id)):
        distinct.setdefault((item.assessment_kind, item.assessment_ref.asset_id), item)
    eligible = list(distinct.values())
    groups: dict[tuple[str, str, str], list[PerformanceObservation]] = {}
    for item in eligible:
        key = (item.rubric_ref.key, item.difficulty_band, item.comparison_group)
        groups.setdefault(key, []).append(item)
    paired = []
    for key, group in groups.items():
        pre = [item for item in group if item.assessment_kind == "pretest"]
        post = [item for item in group if item.assessment_kind == post_kind]
        if len(pre) >= min_samples and len(post) >= min_samples:
            paired.append((key, pre, post))
    common = dict(as_of=as_of, excluded_count=len(candidates) - len(eligible))
    if len(paired) > 1:
        return PerformanceGain(status="incomparable", reason="multiple_comparison_groups_select_one", **common)
    if not paired:
        pre_count = sum(item.assessment_kind == "pretest" for item in eligible)
        post_count = sum(item.assessment_kind == post_kind for item in eligible)
        status = "incomparable" if pre_count >= min_samples and post_count >= min_samples else "insufficient_data"
        return PerformanceGain(status=status, reason="no_matched_rubric_difficulty_group_with_required_samples",
                               pre_count=pre_count, post_count=post_count, **common)
    _, pre, post = paired[0]
    if max(item.occurred_at for item in pre) >= min(item.occurred_at for item in post):
        return PerformanceGain(status="incomparable", reason="pretest_window_must_precede_posttest", **common)
    # Repeating the exact item measures familiarity; gains require disjoint item versions.
    if {item.assessment_ref.asset_id for item in pre} & {item.assessment_ref.asset_id for item in post}:
        return PerformanceGain(status="incomparable", reason="repeated_assessment_not_independent_comparison", **common)
    selected = pre + post
    pre_mean, post_mean = mean(item.score for item in pre), mean(item.score for item in post)
    return PerformanceGain(status="comparable", reason="descriptive_matched_unassisted_performance",
                           pre_mean=pre_mean, post_mean=post_mean, gain=post_mean - pre_mean,
                           pre_count=len(pre), post_count=len(post),
                           evidence_refs=tuple(sorted(item.evidence_id for item in selected)),
                           assessment_refs=tuple(sorted({item.assessment_ref.key for item in selected})),
                           window_start=min(item.occurred_at for item in selected),
                           window_end=max(item.occurred_at for item in selected), **common)
