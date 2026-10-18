from typing import Literal

from pydantic import Field, field_validator

from app.modules.learning.contracts import Contract
from app.modules.learning.tutor_contracts import ContentOut, GateInput, TaskOut, TutorRunOut, CoachingOut


class StartActivity(Contract):
    unit_id: str = Field(min_length=1, max_length=100)


class ActivityInput(GateInput):
    action: Literal["contribute", "refine", "conclude", "verify"]
    answer: str = Field(min_length=1, max_length=128)
    reasoning: str = Field(min_length=2, max_length=1500)
    request_model: bool = Field(default=False, strict=True)

    @field_validator("answer", "reasoning")
    @classmethod
    def clean(cls, value):
        if not value.strip():
            raise ValueError("请填写自己的想法")
        return value.strip()

    @field_validator("reasoning")
    @classmethod
    def enough(cls, value):
        if len(value) < 2:
            raise ValueError("请补充说明")
        return value


class ActivityMessage(Contract):
    speaker: str
    text: str
    category: Literal["role_opinion", "preset_misconception", "student_contribution"]
    answer: str | None = None


class ActivityCheck(Contract):
    status: Literal["passed", "failed", "unverifiable"]
    message: str
    reasoning_reviewed: Literal[False] = False
    independent: Literal[False] = False


class IndependentCheck(Contract):
    status: Literal['passed', 'failed', 'unverifiable']
    message: str
    reasoning_reviewed: Literal[False] = False
    independent: Literal[True] = True


class ActivityView(Contract):
    kind: Literal["roundtable", "teach"]
    phase: Literal["opening", "challenge", "verification", "summary"]
    task: TaskOut
    messages: list[ActivityMessage]
    check: ActivityCheck | None
    report: list[str]
    source: Literal["development_template"] = "development_template"
    activity_version: str
    flow: str = 'legacy'
    verification_task: TaskOut | None = None
    verification_check: IndependentCheck | None = None
    model_requested: bool = False
    model_rounds: int = Field(default=0, ge=0, le=3)


class ActivityRunOut(TutorRunOut):
    kind: Literal["roundtable", "teach"]
    view: ActivityView | None
    coaching_history: list[CoachingOut] = Field(default_factory=list)


class ActivityRunsOut(Contract):
    items: list[ActivityRunOut]


class ActivityContentOut(ContentOut):
    parent_unit_id: str
    flow: Literal['legacy', 'teach_cycle_v1', 'roundtable_cycle_v1'] = 'legacy'


class ActivityCatalogOut(Contract):
    items: list[ActivityContentOut]
