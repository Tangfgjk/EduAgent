from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator
from app.modules.learning.contracts import Contract
from app.modules.learning.tutor_contracts import TaskOut


class RecallIn(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    answer: str = Field(min_length=1, max_length=128)
    reasoning: str = Field(default="", max_length=1500)


class NoteIn(Contract):
    fact_id: UUID
    text: str = Field(min_length=2, max_length=1000)

    @field_validator("text")
    @classmethod
    def clean(cls, value):
        if len(value.strip()) < 2:
            raise ValueError("请填写具体说明")
        return value.strip()


class NoteOut(Contract):
    note_id: UUID
    fact_id: UUID
    text: str
    created_at: int


class JudgmentOut(Contract):
    judgment_id: UUID
    conclusion: Literal["correct", "incorrect", "unresolved"]
    note: str
    created_at: int


class FactOut(Contract):
    fact_id: UUID
    workspace_id: UUID
    run_id: UUID
    input_id: UUID
    kind: str
    source_kind: str
    content_version: str
    task: TaskOut
    stage: str
    status: Literal["passed", "failed", "unverifiable", "skipped"] | None
    hints_used: int
    answer: str | None
    reasoning: str | None
    reflection: str | None
    revealed_hint: str | None
    activity_version: str | None = None
    independent: bool | None = None
    reasoning_reviewed: bool | None = None
    created_at: int
    notes: list[NoteOut]
    judgments: list[JudgmentOut]


class RecallOut(Contract):
    attempt_id: UUID
    fact_id: UUID
    status: Literal["passed", "failed", "unverifiable"]
    counted_for_schedule: bool
    due_before: int
    due_after: int
    created_at: int


class CardOut(Contract):
    card_id: UUID
    workspace_id: UUID
    task: TaskOut
    source_kind: str
    content_version: str
    source_fact_ids: list[UUID]
    reasons: list[Literal["numeric_error", "assisted_success", "human_review"]]
    revision: int
    schedule_step: int
    next_due_at: int
    due: bool
    attempts: list[RecallOut]


class CardsOut(Contract):
    items: list[CardOut]
    due_count: int
    schedule_policy: Literal["fixed-recall.v1"] = "fixed-recall.v1"
    interval_days: list[int]


class RecallResult(Contract):
    attempt: RecallOut
    card: CardOut


class UnknownOut(Contract):
    fact_id: UUID | None
    review_id: UUID
    task_id: str
    answer: str
    status: Literal["pending", "resolved"]
    conclusion: Literal["correct", "incorrect", "unresolved"] | None
    note: str | None


class UnknownsOut(Contract):
    items: list[UnknownOut]


class SourceRef(Contract):
    kind: Literal["fact", "digest", "judgment", "self_report"]
    id: UUID | None = None
    name: str | None = None
    revision: int | None = None


class DigestRow(Contract):
    source_kind: str
    stage: str
    passed: int
    failed: int
    unverifiable: int
    hinted_passed: int


class DigestOut(Contract):
    revision: int
    source_refs: list[SourceRef]
    rows: list[DigestRow]
    hint_requests: int
    reflections: int
    self_reports: int
    manual_judgments: int


class Observation(Contract):
    source_kind: str
    text: str
    source_refs: list[SourceRef]


class ObservationsOut(Contract):
    revision: int
    source_refs: list[SourceRef]
    engine: Literal["observations.v1"]
    claims: list[Observation]
    limitation: str


class WorkspaceMemory(Contract):
    workspace_id: UUID
    title: str
    kind: Literal["project", "temporary"]
    status: Literal["active", "archived"]
    digest: DigestOut | None
    observations: ObservationsOut | None


class MemoryOut(Contract):
    scope: Literal["project", "global"]
    project_count: int
    items: list[WorkspaceMemory]
