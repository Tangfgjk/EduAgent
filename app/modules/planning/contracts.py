from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.modules.learning.contracts import Contract
from app.modules.learning.tutor_contracts import GateInput


class SoftPolicy(Contract):
    daily_minutes: int = Field(ge=5, le=60, strict=True)
    reflection_focus: Literal["basis", "verification"]


class MaterialIn(Contract):
    title: str = Field(min_length=2, max_length=120)
    kind: Literal["textbook", "curriculum", "exam", "development_reference"]
    edition: str = Field(min_length=1, max_length=80)
    source_ref: str = Field(min_length=2, max_length=300)
    body: str = Field(min_length=2, max_length=6000)

    @field_validator("title", "edition", "source_ref", "body")
    @classmethod
    def clean(cls, value):
        if not value.strip(): raise ValueError("资料字段不能为空")
        return value.strip()


class MaterialOut(MaterialIn):
    material_id: UUID
    content_hash: str
    enabled: bool
    revision: int
    created_at: int
    professional_review: Literal[False] = False


class MaterialsOut(Contract):
    items: list[MaterialOut]


class ReleaseIn(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    enabled: bool = Field(strict=True)
    acknowledge_development_only: Literal[True]


class StartStrategy(SoftPolicy):
    learning_experience: str = Field(min_length=2, max_length=1000)
    material_ids: list[UUID] = Field(min_length=1, max_length=5)

    @field_validator("learning_experience")
    @classmethod
    def clean(cls, value):
        if len(value.strip()) < 2: raise ValueError("请补充学习体验")
        return value.strip()

    @field_validator("material_ids")
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value): raise ValueError("资料不能重复选择")
        return value


class StrategyDecision(GateInput):
    action: Literal["approve", "reject", "confirm", "decline"]
    note: str = Field(min_length=2, max_length=1000)

    @field_validator("note")
    @classmethod
    def clean(cls, value):
        if len(value.strip()) < 2: raise ValueError("请填写决定依据")
        return value.strip()


class StrategyView(Contract):
    phase: Literal["admin_review", "student_confirm", "approved", "rejected", "declined"]
    policy: SoftPolicy
    checks: list[str]
    source: Literal["development_template"] = "development_template"


class PlanningEvidence(Contract):
    fingerprint: str
    counts: dict[str, int]
    facts: list[dict]
    self_reports: list[dict]
    judgments: list[dict]
    limitation: str


class PlanningBundle(Contract):
    workspace_id: UUID
    rules_hash: str
    student_experience: str
    evidence: PlanningEvidence
    materials: list[MaterialOut]
    source_kind: str
    base_version_id: UUID | None
    created_at: int


class StrategyOut(Contract):
    run_id: UUID
    workspace_id: UUID
    workspace_title: str
    workspace_status: Literal["active", "archived"]
    status: Literal["queued", "awaiting_input", "completed", "cancelled", "failed"]
    revision: int
    gate_id: UUID | None
    error_code: str | None
    created_at: int
    view: StrategyView | None
    version_hash: str | None
    bundle: PlanningBundle | None
    receipts: list[dict]
    skill_markdown: str | None
    expires_at: int | None


class StrategiesOut(Contract):
    items: list[StrategyOut]


class ActiveOut(Contract):
    workspace_id: UUID
    revision: int
    version_id: UUID | None
    effective: bool
    unavailable_reason: str | None
    policy: SoftPolicy
    history: list[dict]


class RollbackIn(Contract):
    expected_revision: int = Field(ge=0, strict=True)
    target_version: UUID | None
    note: str = Field(min_length=2, max_length=1000)

    @field_validator("note")
    @classmethod
    def clean(cls, value):
        if len(value.strip()) < 2: raise ValueError("请填写回滚依据")
        return value.strip()
