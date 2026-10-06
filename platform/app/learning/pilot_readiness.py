"""Hash-bound pilot preregistration readiness, without recruitment or collection."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

from pydantic import AwareDatetime, Field, model_validator

from app.learning.course_governance import Contract, CourseAdmissionReport


ApprovalScope = Literal["ethics", "institution", "curriculum", "identity_isolation",
    "privacy_deletion", "research_consent", "measurement", "safety_escalation", "backup_recovery"]
REQUIRED_SCOPES = ("ethics", "institution", "curriculum", "identity_isolation",
    "privacy_deletion", "research_consent", "measurement", "safety_escalation", "backup_recovery")


class PilotProtocol(Contract):
    schema_version: Literal["pilot-preparation-v3.1"] = "pilot-preparation-v3.1"
    protocol_ref: str = Field(min_length=1)
    created_at: AwareDatetime
    course_package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["draft", "pending"] = "pending"
    research_question: str = Field(min_length=20)
    proposed_sample_min: int = Field(ge=1)
    proposed_sample_max: int = Field(ge=1)
    recruitment_authorized: Literal[False] = False
    real_collection_enabled: Literal[False] = False
    minors_in_scope: bool
    consent_requirements: tuple[str, ...] = Field(min_length=1)
    inclusion_criteria: tuple[str, ...] = Field(min_length=1)
    exclusion_criteria: tuple[str, ...] = Field(min_length=1)
    stopping_rules: tuple[str, ...] = Field(min_length=1)
    missingness_plan: str = Field(min_length=20)
    primary_measurements: tuple[str, ...] = Field(min_length=3)
    autonomy_behavior_components: tuple[str, ...] = Field(min_length=5)
    human_correction_workload: str = Field(min_length=20)
    privacy_plan: str = Field(min_length=20)
    retention_policy: str = Field(min_length=20)
    withdrawal_procedure: str = Field(min_length=20)
    incident_procedure: str = Field(min_length=20)
    analysis_plan: str = Field(min_length=20)
    causal_effect_claim: Literal[False] = False

    @model_validator(mode="after")
    def valid_scope(self):
        if self.proposed_sample_min > self.proposed_sample_max:
            raise ValueError("sample range is reversed")
        if self.minors_in_scope and "guardian_and_learner_authorization" not in self.consent_requirements:
            raise ValueError("minor protocol must specify guardian and learner authorization")
        if "independent_research_authorization" not in self.consent_requirements:
            raise ValueError("teaching consent is not research authorization")
        return self

    @property
    def content_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), ensure_ascii=False,
                             sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def load(cls, path: Path | str):
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8-sig"))


class PilotApproval(Contract):
    approval_ref: str = Field(min_length=1)
    approver_ref: str = Field(min_length=1)
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: ApprovalScope
    decision: Literal["approve", "revise", "reject"]
    approved_at: AwareDatetime
    expires_at: AwareDatetime
    rationale: str = Field(min_length=10)

    @model_validator(mode="after")
    def expiry(self):
        if self.expires_at <= self.approved_at:
            raise ValueError("approval must expire after issuance")
        return self


class PilotReadiness(Contract):
    protocol_ref: str
    protocol_sha256: str
    as_of: AwareDatetime
    preparation_ready: bool
    blockers: tuple[str, ...]
    approval_refs: tuple[str, ...]
    recruitment_enabled: Literal[False] = False
    real_collection_enabled: Literal[False] = False
    causal_effect_claim: Literal[False] = False


def pilot_readiness(protocol: PilotProtocol, *, as_of: datetime,
                    course_report: CourseAdmissionReport | None = None,
                    verify_course_report: Callable[[CourseAdmissionReport], bool] | None = None,
                    approvals: tuple[PilotApproval, ...] = (),
                    verify_approval: Callable[[PilotApproval], bool] | None = None) -> PilotReadiness:
    """Trusted approvals unlock readiness only, never recruitment or collection."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("explicit timezone-aware as_of required")
    blockers = []
    if protocol.created_at > as_of:
        blockers.append("future_protocol")
    if (course_report is None or verify_course_report is None or not verify_course_report(course_report)
            or not course_report.human_pilot_admitted
            or course_report.package_sha256 != protocol.course_package_sha256
            or course_report.as_of > as_of):
        blockers.append("currently_approved_course_unverified")
    verified = set()
    refs = []
    ids = [approval.approval_ref for approval in approvals]
    if len(ids) != len(set(ids)):
        blockers.append("duplicate_approval_identity")
    for approval in approvals:
        reasons = []
        if verify_approval is None or not verify_approval(approval):
            reasons.append("authority_unverified")
        if approval.protocol_sha256 != protocol.content_hash:
            reasons.append("protocol_hash_mismatch")
        if not protocol.created_at <= approval.approved_at <= as_of < approval.expires_at:
            reasons.append("approval_expired_or_time_invalid")
        if approval.decision != "approve":
            reasons.append("approval_not_granted")
        if reasons:
            blockers.extend(f"{approval.scope}:{reason}" for reason in reasons)
        else:
            verified.add(approval.scope)
            refs.append(approval.approval_ref)
    blockers.extend(f"{scope}:approval_missing" for scope in REQUIRED_SCOPES if scope not in verified)
    return PilotReadiness(protocol_ref=protocol.protocol_ref, protocol_sha256=protocol.content_hash,
        as_of=as_of, preparation_ready=not blockers, blockers=tuple(sorted(set(blockers))),
        approval_refs=tuple(sorted(set(refs))))
