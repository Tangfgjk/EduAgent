from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartPlan(Contract):
    goal: str = Field(min_length=1, max_length=600)
    daily_minutes: int = Field(default=15, ge=5, le=60, strict=True)

    @field_validator("goal")
    @classmethod
    def clean_goal(cls, value):
        if not value.strip():
            raise ValueError("目标不能为空")
        return value.strip()


class PlanDecision(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    gate_id: UUID
    action: Literal["confirm", "revise"]
    daily_minutes: int = Field(ge=5, le=60, strict=True)


class RunRevision(Contract):
    expected_revision: int = Field(ge=1, strict=True)


class PlanDraft(Contract):
    goal: str
    daily_minutes: int = Field(ge=5, le=60)
    generation: int = Field(ge=1)
    steps: list[str]
    source: Literal["development_template"] = "development_template"


class RunOut(Contract):
    run_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    status: Literal["queued", "awaiting_input", "completed", "cancelled", "failed"]
    revision: int
    draft: PlanDraft | None
    gate_id: UUID | None
    error_code: str | None
    event_seq: int
    created_at: int


class RunsOut(Contract):
    items: list[RunOut]


class EventOut(Contract):
    run_id: UUID
    seq: int
    kind: Literal["run.queued", "plan.proposed", "plan.confirmed", "run.cancelled", "run.failed", "tutor.started", "tutor.answer", "tutor.hint", "tutor.reflect", "tutor.next", "tutor.skip", "tutor.completed", "activity.updated", "activity.completed", "strategy.updated", "strategy.completed"]
    payload: dict
    created_at: int
