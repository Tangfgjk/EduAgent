from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_serializer

from app.modules.learning.contracts import Contract
from app.modules.learning.tutor_contracts import GateInput


class ExecutionPolicy(Contract):
    schema_version: Literal['teaching-policy.v1'] = 'teaching-policy.v1'
    practice_order: Literal['original', 'errors_first'] = 'original'
    review_order: Literal['due_first', 'due_errors_first'] = 'due_first'
    support_mode: Literal['step_first', 'adaptive'] = 'adaptive'
    question_focus: Literal['basis', 'representation', 'verification'] = 'basis'


class SoftPolicy(Contract):
    daily_minutes: int = Field(ge=5, le=60, strict=True)
    reflection_focus: Literal["basis", "verification"]
    execution: ExecutionPolicy | None = None

    @model_serializer(mode='wrap')
    def compatible_dump(self, handler):
        value=handler(self)
        if value.get('execution') is None:value.pop('execution',None)
        return value


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
    self_service: bool = Field(default=False, strict=True)
    request_model: bool = Field(default=False, strict=True)
    learning_experience: str = Field(min_length=2, max_length=1000)
    material_ids: list[UUID] = Field(default_factory=list, max_length=5)

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
    phase: Literal["model_pending", "admin_review", "student_confirm", "approved", "rejected", "declined"]
    policy: SoftPolicy
    checks: list[str]
    source: Literal["development_template", "model_candidate"] = "development_template"


class PlanningEvidence(Contract):
    fingerprint: str
    counts: dict[str, int]
    facts: list[dict]
    self_reports: list[dict]
    judgments: list[dict]
    limitation: str


class PlanningCitation(Contract):
    source_id: str = Field(min_length=1, max_length=30)
    quote: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=2, max_length=160)


class PlanningGeneration(Contract):
    operation_id: UUID
    prompt_version: str
    requested_policy: SoftPolicy
    citations: list[PlanningCitation]
    source_refs: dict[str, dict]
    source_excerpts: dict[str, str]
    limitation: str


class GlobalPlanningSummary(Contract):
    fingerprint: str
    counts: dict[str, int]
    scope: str


from app.modules.evidence.insight_contracts import PlanningObservations


class PlanningBundle(Contract):
    learning_goal: dict | None = None
    default_reference: dict | None = None
    release_decision: dict | None = None
    learning_observations: PlanningObservations | None = None
    skill_compilation: dict | None = None
    policy_changes: list[dict] | None = None
    policy_base: dict | None = None
    evolution_reference: dict | None = None
    course_baseline: dict | None = None
    workspace_id: UUID
    rules_hash: str
    student_experience: str
    evidence: PlanningEvidence
    materials: list[MaterialOut]
    source_kind: str
    base_version_id: UUID | None
    created_at: int
    global_summary: GlobalPlanningSummary | None = None
    model_generation: PlanningGeneration | None = None


class PlanningModelOut(Contract):
    operation_id: UUID
    status: str
    vendor: str
    simulated: bool
    prompt_version: str
    error_code: str | None
    usage: dict | None
    elapsed_ms: int | None


class StrategyOut(Contract):
    compilation: dict | None = None
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
    generation: PlanningModelOut | None = None


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
