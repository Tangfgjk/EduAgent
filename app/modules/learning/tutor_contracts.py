from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator
from app.modules.learning.contracts import Contract
from app.llm.provider import Usage


class StartTutor(Contract):
    unit_id: str = Field(min_length=1, max_length=100)


class GateInput(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    gate_id: UUID


class AnswerInput(GateInput):
    action: Literal["answer"]
    answer: str = Field(min_length=1, max_length=128)
    reasoning: str = Field(default="", max_length=1500)


class HintInput(GateInput):
    action: Literal["hint"]
    coaching_text: str = Field(default="", max_length=1500)


class ReflectInput(GateInput):
    action: Literal["reflect"]
    reflection: str = Field(min_length=2, max_length=1000)

    @field_validator("reflection")
    @classmethod
    def meaningful(cls, value):
        if len(value.strip()) < 2:
            raise ValueError("请写下自己的反思")
        return value.strip()


class NextInput(GateInput):
    action: Literal["next", "skip"]


TutorInput = Annotated[AnswerInput | HintInput | ReflectInput | NextInput, Field(discriminator="action")]


class TaskOut(Contract):
    task_id: str
    stem: str
    answer_format: str


class FeedbackOut(Contract):
    status: Literal["passed", "failed", "unverifiable"]
    message: str
    reasoning_reviewed: Literal[False] = False


class TaskRecord(Contract):
    task_id: str
    stage: Literal["diagnostic", "practice", "checkpoint"]
    status: Literal["passed", "failed", "unverifiable", "skipped"]
    attempts: int
    hints_used: int
    reflection: str = ""


class TutorView(Contract):
    strategy_version_id: str | None = None
    reflection_prompt: str = "这一步用到了什么依据？下次准备怎么检查？"
    unit_id: str
    title: str
    content_version: str
    phase: Literal["diagnostic", "practice", "reflection", "checkpoint", "summary"]
    task: TaskOut | None
    task_index: int
    task_count: int
    attempts: int
    hints: list[str]
    feedback: FeedbackOut | None
    can_answer: bool
    can_hint: bool
    can_continue: bool
    can_skip: bool
    can_reflect: bool
    pending_reviews: int
    records: list[TaskRecord]


class RoundtableVoiceOut(Contract):
    role: Literal['proposer', 'critic', 'organizer']
    quote: str
    text: str


class CoachingResultOut(Contract):
    quote: str
    focus: str
    question: str
    judgment: str
    voices: list[RoundtableVoiceOut] = Field(default_factory=list)


class CoachingContextOut(Contract):
    student_text: str
    focus: str
    question: str


class CoachingOut(Contract):
    operation_id: UUID
    configuration_id: UUID
    status: Literal["waiting", "pending", "running", "succeeded", "failed", "cancelled", "discarded", "unknown"]
    task_id: str
    hint_level: int
    vendor: str
    model: str
    simulated: bool
    current: bool
    result: CoachingResultOut | None
    error_code: str | None
    usage: Usage | None
    elapsed_ms: int | None
    billing_unknown: bool
    prompt_version: str = "probability.followup.v1"
    context_turns: int = Field(default=1, ge=1, le=4)
    context: list[CoachingContextOut] = Field(default_factory=list)


class TutorQueueOut(Contract):
    state: Literal['waiting', 'processing', 'retrying', 'recovering', 'failed']
    wait_seconds: int
    attempts: int
    retry_after_seconds: int | None
    error_code: str | None


class TutorRunOut(Contract):
    run_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    status: Literal["queued", "awaiting_input", "completed", "cancelled", "failed"]
    revision: int
    gate_id: UUID | None
    error_code: str | None
    event_seq: int
    created_at: int
    view: TutorView | None
    coaching: CoachingOut | None = None
    coaching_available: bool = False
    coaching_context_limit: Literal[1, 4] = 1
    coaching_remaining_calls: int | None = Field(default=None, ge=0)
    queue: TutorQueueOut | None = None


class HistoryModelOut(Contract):
    operation_id: UUID
    status: Literal['waiting', 'pending', 'running', 'succeeded', 'failed', 'cancelled', 'discarded', 'unknown']
    vendor: str
    model: str
    prompt_version: str
    result: CoachingResultOut | None
    error_code: str | None


class TutorHistoryItem(Contract):
    sequence: int
    created_at: int
    source_kind: str
    action: Literal['start', 'answer', 'hint', 'reflect', 'next', 'skip']
    task_id: str | None
    task_stem: str | None
    stage: Literal['diagnostic', 'practice', 'checkpoint'] | None
    answer: str | None
    reasoning: str | None
    reflection: str | None
    thinking: str | None
    preset_hint: str | None
    numeric_status: Literal['passed', 'failed', 'unverifiable'] | None
    processed: bool
    model: HistoryModelOut | None


class TutorHistoryOut(Contract):
    items: list[TutorHistoryItem]
    cursor: int
    has_more: bool


class TutorRunsOut(Contract):
    items: list[TutorRunOut]


class ContentOut(Contract):
    unit_id: str
    title: str
    content_version: str
    enabled: bool
    revision: int
    source: Literal["self_authored_development"] = "self_authored_development"
    professional_review: Literal[False] = False
    allowed_sources: list[str]
    tasks: list[dict] = Field(default_factory=list)


class ContentToggle(Contract):
    content_version: str = Field(min_length=64, max_length=64)
    expected_revision: int = Field(ge=0, strict=True)
    enabled: bool = Field(strict=True)
    acknowledge_development_only: Literal[True]


class ReviewOut(Contract):
    review_id: UUID
    run_id: UUID
    workspace_id: UUID
    source_kind: str
    task_id: str
    content_version: str
    task: dict
    answer: str
    reasoning: str
    status: Literal["pending", "resolved"]
    revision: int
    conclusion: Literal["correct", "incorrect", "unresolved"] | None
    note: str | None


class ReviewsOut(Contract):
    items: list[ReviewOut]


class ReviewDecision(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    conclusion: Literal["correct", "incorrect", "unresolved"]
    note: str = Field(min_length=2, max_length=1000)

    @field_validator("note")
    @classmethod
    def meaningful(cls, value):
        if len(value.strip()) < 2:
            raise ValueError("请填写复核依据")
        return value.strip()


class CatalogUnit(ContentOut):
    grade: str
    domain: str
    knowledge_points: list[str]
    prerequisites: list[str]
    task_count: int
    boundary: str


class CatalogOut(Contract):
    items: list[CatalogUnit]
    coverage_note: str
