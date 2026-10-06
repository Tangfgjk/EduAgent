"""Evaluation and manual calibration gate; no writes or automatic promotion.

Callers provide current research authorization and reviewer verification ports.
Boolean collection flags alone never confer current access. These reports
measure verifier error against human references, not educational effectiveness.
"""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from datetime import datetime
from typing import Callable, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)


class CalibrationObservation(Contract):
    observation_id: str=Field(min_length=1)
    evidence_ref: str=Field(min_length=1)
    learner_ref: str=Field(min_length=1)
    source_kind: Literal["synthetic_ai_generated","real_learning_data"]
    collection_research_authorized: bool
    consent_version: str=Field(min_length=1)
    model_version: str=Field(min_length=1)
    parameter_set_id: str=Field(min_length=1)
    predicted_passed: bool
    reference_passed: bool
    reference_source: Literal["authorized_human","model_generated","unreviewed"]
    reference_ref: str=Field(min_length=1)
    group: str=Field(default="ungrouped",min_length=1,max_length=100)
    occurred_at: AwareDatetime
    hint_level: int=Field(default=0,ge=0,le=3)
    assistance_mode: Literal["none","hint","answer"]="none"
    answer_exposed: bool=False


class EvaluationConfig(Contract):
    config_version: str="calibration-governance-v1"
    min_real_samples: int=Field(default=50,ge=20)
    min_group_samples: int=Field(default=10,ge=5)
    max_error_upper_bound: float=Field(default=.15,gt=0,lt=1)
    wilson_z: Literal[1.959963984540054]=1.959963984540054


class GroupMetric(Contract):
    group: str
    sample_count: int
    error_count: int
    error_rate: float
    error_interval: tuple[float,float]
    interval_method: str="wilson-binomial-95-v1"


class CalibrationReport(Contract):
    report_id: str
    as_of: AwareDatetime
    report_version: str="calibration-report-v1"
    config: EvaluationConfig
    real_sample_count: int
    synthetic_sample_count: int
    error_count: int
    error_rate: float | None
    error_interval: tuple[float,float] | None
    interval_method: str="wilson-binomial-95-v1"
    groups: tuple[GroupMetric,...]
    suppressed_group_count: int
    rejected_counts: dict[str,int]
    model_versions: tuple[str,...]
    parameter_set_ids: tuple[str,...]
    independent_learner_count: int
    sampling_unit: Literal["one_fixed_observation_per_learner"]="one_fixed_observation_per_learner"
    educational_effect_claim: Literal[False]=False
    automatic_promotion_enabled: Literal[False]=False
    limitations: tuple[str,...]=("verifier_error_not_learning_effect","group_metrics_not_causal",
                                 "wilson_requires_binomial_assumptions","human_reference_quality_requires_audit")


class SamplingPlan(Contract):
    seed: int
    as_of: AwareDatetime
    observation_refs: tuple[str,...]
    error_samples: int
    sampling_version: str="human-error-stratified-v1"
    publicly_publishable: Literal[False]=False
    purpose: Literal["research"]="research"


class ParameterCandidate(Contract):
    candidate_id: str=Field(min_length=1)
    model_name: str=Field(min_length=1)
    algorithm_version: str=Field(min_length=1)
    candidate_version: str=Field(min_length=1)
    baseline_version: str=Field(min_length=1)
    parameters: dict[str,float]=Field(min_length=1)
    evaluation_model_version: str=Field(min_length=1)
    evaluation_parameter_set_id: str=Field(min_length=1)

    @model_validator(mode="after")
    def finite_parameters(self):
        if any(not key.strip() or not math.isfinite(value) for key,value in self.parameters.items()):
            raise ValueError("Candidate parameters must be named finite values")
        if self.candidate_version==self.baseline_version:
            raise ValueError("Candidate must use a new parameter version")
        return self


class ManualReview(Contract):
    review_id: str=Field(min_length=1)
    reviewer_ref: str=Field(min_length=1)
    reviewed_at: AwareDatetime
    decision: Literal["approve_candidate","reject","request_changes"]
    report_ref: str=Field(min_length=1)
    rationale: str=Field(min_length=1)


class PromotionGateResult(Contract):
    candidate_id: str
    report_ref: str
    review_ref: str | None
    status: Literal["blocked","manual_review_required","candidate_ready_for_manual_activation"]
    reasons: tuple[str,...]
    automatic_promotion_enabled: Literal[False]=False
    changes_applied: Literal[False]=False
    educational_effect_claim: Literal[False]=False


def wilson_interval(errors: int,total: int,*,z: float=1.959963984540054) -> tuple[float,float] | None:
    if total<0 or errors<0 or errors>total or not math.isfinite(z) or z<=0:
        raise ValueError("Invalid binomial interval inputs")
    if total==0:
        return None
    probability=errors/total
    denominator=1+z*z/total
    center=(probability+z*z/(2*total))/denominator
    half=z*math.sqrt(probability*(1-probability)/total+z*z/(4*total*total))/denominator
    return (0.0 if errors==0 else max(0.0,center-half),1.0 if errors==total else min(1.0,center+half))


def _aware(as_of: datetime) -> None:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("Explicit timezone-aware as_of required")


def _eligible(history,as_of,authorize):
    _aware(as_of)
    by_ref={}
    for item in history:
        key=(item.source_kind,item.evidence_ref)
        if key in by_ref and by_ref[key]!=item:
            raise ValueError("conflict in calibration evidence identity")
        by_ref[key]=item
    valid,rejected,synthetic=[],Counter(),0
    learners=set()
    for item in sorted(by_ref.values(),key=lambda record:(record.evidence_ref,record.model_version,record.parameter_set_id)):
        if item.source_kind=="synthetic_ai_generated":
            synthetic+=1
            continue
        if item.occurred_at>as_of:
            rejected["future_observation"]+=1
        elif not item.collection_research_authorized:
            rejected["collection_research_unauthorized"]+=1
        elif not authorize(item):
            rejected["current_research_unauthorized"]+=1
        elif item.hint_level or item.assistance_mode!="none" or item.answer_exposed:
            rejected["assisted_observation"]+=1
        elif item.reference_source!="authorized_human":
            rejected["unreviewed_reference"]+=1
        elif item.learner_ref in learners:
            rejected["same_learner_cluster"]+=1
        else:
            valid.append(item)
            learners.add(item.learner_ref)
    return valid,rejected,synthetic


def evaluate_calibration(history: list[CalibrationObservation],*,report_id: str,as_of: datetime,
                         authorize: Callable[[CalibrationObservation],bool],
                         config: EvaluationConfig | None=None) -> CalibrationReport:
    config=config or EvaluationConfig()
    valid,rejected,synthetic=_eligible(history,as_of,authorize)
    grouped=defaultdict(list)
    for item in valid:
        grouped[item.group].append(item)
    groups=[]
    suppressed=0
    for group,items in sorted(grouped.items()):
        if len(items)<config.min_group_samples:
            suppressed+=1
            continue
        errors=sum(item.predicted_passed!=item.reference_passed for item in items)
        groups.append(GroupMetric(group=group,sample_count=len(items),error_count=errors,error_rate=errors/len(items),
                                 error_interval=wilson_interval(errors,len(items),z=config.wilson_z)))
    errors=sum(item.predicted_passed!=item.reference_passed for item in valid)
    return CalibrationReport(report_id=report_id,as_of=as_of,config=config,real_sample_count=len(valid),
        synthetic_sample_count=synthetic,error_count=errors,error_rate=errors/len(valid) if valid else None,
        error_interval=wilson_interval(errors,len(valid),z=config.wilson_z),groups=tuple(groups),
        suppressed_group_count=suppressed,rejected_counts=dict(rejected),
        model_versions=tuple(sorted({item.model_version for item in valid})),
        parameter_set_ids=tuple(sorted({item.parameter_set_id for item in valid})),
        independent_learner_count=len({item.learner_ref for item in valid}))


def human_sampling_plan(history: list[CalibrationObservation],*,seed: int,sample_size: int,as_of: datetime,
                        authorize: Callable[[CalibrationObservation],bool]) -> SamplingPlan:
    if sample_size<1 or sample_size>1000 or seed<0:
        raise ValueError("Invalid manual sampling configuration")
    valid,_,_=_eligible(history,as_of,authorize)
    rng=random.Random(seed)
    errors=[item for item in valid if item.predicted_passed!=item.reference_passed]
    correct=[item for item in valid if item.predicted_passed==item.reference_passed]
    rng.shuffle(errors)
    rng.shuffle(correct)
    error_target=min(len(errors),max(1,sample_size//2))
    selected=errors[:error_target]+correct[:sample_size-error_target]
    remaining=[item for item in errors[error_target:]+correct[sample_size-error_target:] if item not in selected]
    selected.extend(remaining[:sample_size-len(selected)])
    return SamplingPlan(seed=seed,as_of=as_of,observation_refs=tuple(item.observation_id for item in selected),
                        error_samples=sum(item.predicted_passed!=item.reference_passed for item in selected))


def promotion_gate(candidate: ParameterCandidate,report: CalibrationReport,*,review: ManualReview | None,
                   verify_review: Callable[[ManualReview],bool],
                   verify_report: Callable[[CalibrationReport],bool] | None=None) -> PromotionGateResult:
    reasons=[]
    if verify_report is None or not verify_report(report):
        reasons.append("report_provenance_or_current_research_authorization_unverified")
    if report.real_sample_count<report.config.min_real_samples:
        reasons.append("insufficient_real_independent_samples")
    if report.error_interval is None or report.error_interval[1]>report.config.max_error_upper_bound:
        reasons.append("error_upper_bound_exceeds_limit")
    if report.model_versions!=(candidate.evaluation_model_version,) or report.parameter_set_ids!=(candidate.evaluation_parameter_set_id,):
        reasons.append("evaluation_version_mismatch")
    if any(group.error_interval[1]>report.config.max_error_upper_bound for group in report.groups):
        reasons.append("group_error_upper_bound_exceeds_limit")
    if report.suppressed_group_count:
        reasons.append("subgroup_validation_incomplete")
    if review is None:
        status="blocked" if reasons else "manual_review_required"
    else:
        if not verify_review(review):
            reasons.append("review_authority_unverified")
        if review.report_ref!=report.report_id:
            reasons.append("review_report_mismatch")
        if review.reviewed_at<report.as_of:
            reasons.append("review_precedes_evaluation")
        if review.decision!="approve_candidate":
            reasons.append("human_approval_missing")
        status="blocked" if reasons else "candidate_ready_for_manual_activation"
    return PromotionGateResult(candidate_id=candidate.candidate_id,report_ref=report.report_id,
        review_ref=review.review_id if review else None,status=status,reasons=tuple(reasons))
