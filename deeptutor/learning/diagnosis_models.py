"""Versioned facts for diagnosis; these types grant no mastery credit."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DiagnosticContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    task_prompt: str = ""
    question_type: str = ""
    task_version: str = ""
    objective_name: str = ""
    objective_scope: str = ""  # Versioned objective contract, not a name-based mapping.
    grading_method: Literal["deterministic", "semantic", "legacy"] = "legacy"
    grading_version: str = ""
    rubric_fingerprint: str = ""
    assistance: Literal["independent", "assisted", "unknown"] = "unknown"
    scaffold_level: int = Field(default=0, ge=0, le=3)
    source_refs: list[str] = Field(default_factory=list)


class DiagnosticObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: str
    objective_id: str
    question_id: str = ""
    origin: Literal["local_assessment", "legacy_record"]
    source_refs: list[str]
    session_id: str = ""
    turn_id: str = ""
    occurred_at: float
    ability: str
    answer: Any = None
    response_status: Literal["answered", "unknown"] = "unknown"
    result: Literal["correct", "partial", "incorrect"]
    score: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_score: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    assistance: Literal["independent", "assisted", "unknown"] = "unknown"
    validity: Literal["recorded", "voided", "conflicting"] = "recorded"
    limitations: list[str] = Field(default_factory=list)
    context: DiagnosticContext | None = None

    @model_validator(mode="after")
    def validate_score(self):
        if self.score is not None and self.max_score is not None and self.score > self.max_score:
            raise ValueError("score must not exceed max_score")
        return self


class DiagnosticFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    project_id: str
    source_revision: int
    # Deterministic projection over durable facts; not another editable mastery store.
    observations: list[DiagnosticObservation] = Field(default_factory=list)
    external_references: list[dict[str, Any]] = Field(default_factory=list)
    self_reports: list[dict[str, Any]] = Field(default_factory=list)
    objectives: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
