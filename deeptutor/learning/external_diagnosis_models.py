"""Validated interchange contract for existing, self-uploaded diagnostic RESULTS."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


class ExternalEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    knowledge_id: str = Field(min_length=1, max_length=200)
    knowledge_name: str = Field(min_length=1, max_length=300)
    measurement_status: Literal["estimated", "unmeasured"] = "estimated"
    probability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def check_measurement(self):
        if self.measurement_status == "estimated" and self.probability is None:
            raise ValueError("estimated knowledge requires a probability; missing is not zero")
        if self.measurement_status == "unmeasured" and self.probability is not None:
            raise ValueError("unmeasured knowledge requires null probability")
        return self


class ExternalDiagnosisDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    schema_version: Literal[1] = 1
    source_system: str = Field(min_length=1, max_length=120)
    result_id: str = Field(min_length=1, max_length=200)
    result_version: str = Field(min_length=1, max_length=80)
    student_ref: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=120)
    scope: str = Field(min_length=1, max_length=500)
    observed_on: date
    model_version: str = Field(min_length=1, max_length=200)
    taxonomy_version: str = Field(min_length=1, max_length=200)
    mapping_version: str = Field(min_length=1, max_length=200)
    zero_encoding: Literal["valid_probability", "zero_is_untested"]
    # This is the full report's declared scope, not a measured coverage guess.
    total_knowledge_count: int = Field(ge=1, le=100000)
    estimates: list[ExternalEstimate] = Field(min_length=1, max_length=500)

    @field_validator("student_ref", mode="before")
    @classmethod
    def text_identity(cls, value):
        if not isinstance(value, str):
            raise ValueError("student_ref must be text; preserve leading zeros")
        return value

    @model_validator(mode="after")
    def check_document(self):
        ids = [e.knowledge_id for e in self.estimates]
        if len(ids) != len(set(ids)):
            raise ValueError("knowledge_id must be unique in a result")
        if len(ids) > self.total_knowledge_count:
            raise ValueError("total_knowledge_count cannot be smaller than supplied entries")
        if self.observed_on > datetime.now(timezone.utc).date() + timedelta(days=1):
            raise ValueError("observed_on cannot be in the future")
        return self


class ExternalKnowledgeLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    knowledge_id: str = Field(min_length=1, max_length=200)
    objective_id: str = Field(default="", max_length=300)
    relation: Literal["exact", "partial", "unmapped"] = "unmapped"

    @model_validator(mode="after")
    def check_relation(self):
        if (self.relation == "unmapped") != (not self.objective_id):
            raise ValueError(
                "Choose an objective for exact/partial; unmapped must have no objective"
            )
        return self


class ExternalPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: ExternalDiagnosisDocument
    links: list[ExternalKnowledgeLink] = Field(default_factory=list, max_length=500)


class ExternalCommitRequest(ExternalPreviewRequest):
    outline_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    belongs_to_me: StrictBool
    mapping_confirmed: StrictBool


class ExternalDiagnosisRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    import_id: str
    document_digest: str
    preview_digest: str
    outline_digest: str
    document: ExternalDiagnosisDocument
    links: list[ExternalKnowledgeLink]
    objective_names: dict[str, str]
    provenance: Literal["self_uploaded_unverified"] = "self_uploaded_unverified"
    imported_at: float
    revoked_at: float | None = None
