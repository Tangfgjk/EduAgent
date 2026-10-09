"""Versioned learning contracts; evidence is separate from derived state."""
from typing import Literal, Annotated

from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator
from app.core.schema import KC_ID_PATTERN

KCId = Annotated[str, Field(pattern=KC_ID_PATTERN)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LearningEvidence(Contract):
    schema_version: Literal["1.0"] = "1.0"
    evidence_id: str = Field(min_length=1)
    learner_id: str = Field(min_length=1)
    session_id: str
    kc_refs: list[KCId] = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    artifact_ref: str
    verdict_ref: str
    verdict_status: Literal["passed", "failed", "partial", "unverifiable"]
    verifier_version: str
    verifier_details: dict = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)
    hint_level: int = Field(default=0, ge=0, le=3)
    assistance_mode: Literal["none", "hint", "answer"] = "none"
    answer_exposed: bool = False
    response_time_ms: int | None = Field(default=None, ge=0)
    occurred_at: AwareDatetime
    ingested_at: AwareDatetime | None = None
    event_seq: int | None = Field(default=None, ge=1)
    consent_scope: list[Literal["teaching", "research", "evolution"]]
    consent_version: str = Field(min_length=1)
    authorization_source: str = Field(min_length=1)
    assessment_id: str = "practice"
    assessment_version: str = "1.0"
    assessment_kind: Literal["practice", "pre", "post", "transfer", "review", "diagnostic"] = "practice"
    rubric_version: str = "1.0"
    difficulty_band: str = "unknown"
    score: float = Field(default=0, ge=0, le=1)
    action_ref: str | None = None
    policy_version: str | None = None
    supersedes: str | None = None
    qualitative_pass: bool | None = None
    observation_policy: str = "binary-observation-v1"

    @model_validator(mode="after")
    def distinct_kcs(self):
        if len(self.kc_refs) != len(set(self.kc_refs)):
            raise ValueError("Evidence KC references must be unique")
        return self


class MasteryState(Contract):
    learner_id: str
    kc_id: KCId
    state_version: int = 0
    p_mastery: float = Field(default=0.1, ge=0, le=1)
    confidence: float = Field(default=0, ge=0, le=1)
    confidence_method: str = "heuristic-count-v1"
    effective_evidence_count: int = 0
    evidence_refs: list[str] = Field(default_factory=list)
    model_name: str = "BKT"
    algorithm_version: str = "bkt-v1"
    parameter_set_id: str = "bkt-default-v1"


class RetentionState(Contract):
    learner_id: str
    kc_id: KCId
    state_version: int = 0
    difficulty: float = 0.5
    stability: float = 1.0
    lapse_count: int = 0
    last_review_at: AwareDatetime | None = None
    next_review_at: AwareDatetime | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    scheduler_version: str = "exponential-v1"
    parameter_set_id: str = "retention-default-v1"


class AssessmentProfile(Contract):
    profile_id: str = "quantitative-default"
    version: str = "1.0"
    mode: Literal["quantitative", "qualitative", "hybrid"] = "quantitative"
    mastery_threshold: float = Field(default=0.9, ge=0, le=1)
    min_evidence: int = Field(default=5, ge=1)
    min_distinct_assessments: int = Field(default=2, ge=1)
    min_confidence: float = Field(default=0.6, ge=0, le=1)
    min_qualitative: int = Field(default=2, ge=1)


class MasteryGateResult(Contract):
    status: Literal["mastered", "not_mastered", "insufficient_evidence", "uncertain"]
    probability: float
    confidence: float
    confidence_method: str
    effective_evidence_count: int
    evidence_refs: list[str]
    provenance: dict = Field(default_factory=dict)
    assessment_profile_ref: str
    gate_version: str = "gate-v1"
    reason: str


class ReviewTask(Contract):
    task_id: str
    learner_id: str
    kc_id: KCId
    due_at: AwareDatetime
    as_of: AwareDatetime
    retrievability: float
    forgetting_risk: float
    state_version: int
    evidence_refs: list[str]
    reason: str


class LearningStateTransition(Contract):
    transition_id: str
    learner_id: str
    kc_id: KCId
    evidence_ids: list[str]
    consumer: Literal["mastery", "retention"]
    prior_state_version: int
    new_state_version: int
    algorithm_version: str
    parameter_set_id: str
    event_seq: int
    created_at: AwareDatetime
    skip_reason: str | None = None
