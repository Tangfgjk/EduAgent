"""Versioned content contracts. Structural checks are not mathematical approval."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from app.modules.learning.contracts import Contract
from app.modules.learning.numeric_verifier import parse_number

Identifier = Annotated[str, Field(min_length=1, max_length=100)]


class TopicIntent(Contract):
    schema_version: Literal['topic-intent.v1'] = 'topic-intent.v1'
    input: str = Field(min_length=2, max_length=600)
    scope: Literal['junior_high_math'] = 'junior_high_math'
    status: Literal['needs_clarification', 'candidate', 'out_of_scope']
    canonical_knowledge_points: list[Identifier] = Field(default_factory=list, max_length=30)
    # A parser's candidate is never a curriculum approval or assessment.


class SourceReference(Contract):
    source_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    locator: str = Field(min_length=1, max_length=500)
    authorization: Literal['self_authored_development', 'licensed_local']


class TaskSpec(Contract):
    id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9._-]+$')
    stage: Literal['diagnostic', 'practice', 'checkpoint']
    stem: str = Field(min_length=2, max_length=2000)
    answer: str = Field(min_length=1, max_length=128)
    format: str = Field(default='数值、分数或四则运算', min_length=1, max_length=120)
    hints: list[str] = Field(default_factory=list, max_length=4)
    misconception: str = Field(default='', max_length=600)

    @model_validator(mode='after')
    def supported(self):
        self.answer = str(parse_number(self.answer))
        if any(not h.strip() or len(h) > 1000 for h in self.hints):
            raise ValueError('invalid hints')
        if self.stage == 'practice' and len(self.hints) != 4:
            raise ValueError('practice requires four support levels')
        if self.stage != 'practice' and self.hints:
            raise ValueError('assessment must not include hints')
        return self


class ActivitySpec(Contract):
    unit_id: str = Field(min_length=1, max_length=100, pattern=r'^dynamic\.[a-zA-Z0-9._-]+$')
    title: str = Field(min_length=2, max_length=120)
    task_id: str = Field(min_length=1, max_length=100)
    verification_task_id: str = Field(min_length=1, max_length=100)
    kind: Literal['teach', 'roundtable']
    verification_task: TaskSpec | None = None
    # No arbitrary HTML, executable instructions or answer-bearing role prose.


class GeneratedContentSpec(Contract):
    schema_version: Literal['content.v1'] = 'content.v1'
    unit_id: str = Field(min_length=1, max_length=100, pattern=r'^dynamic\.[a-zA-Z0-9._-]+$')
    title: str = Field(min_length=2, max_length=120)
    grade: Literal['七年级', '八年级', '九年级']
    domain: Literal['数与式', '方程', '函数', '几何', '统计概率']
    knowledge_points: list[str] = Field(min_length=1, max_length=20)
    prerequisites: list[Identifier] = Field(default_factory=list, max_length=20)
    sources: list[SourceReference] = Field(min_length=1, max_length=20)
    tasks: list[TaskSpec] = Field(min_length=5, max_length=30)
    activities: list[ActivitySpec] = Field(default_factory=list, max_length=10)

    @model_validator(mode='after')
    def consistent(self):
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate task')
        stages = [task.stage for task in self.tasks]
        if stages[0] != 'diagnostic' or 'practice' not in stages or stages[-1] != 'checkpoint':
            raise ValueError('diagnostic/practice/checkpoint sequence required')
        order = {'diagnostic': 0, 'practice': 1, 'checkpoint': 2}
        if stages != sorted(stages, key=order.get):
            raise ValueError('invalid stage order')
        activities = [item.unit_id for item in self.activities]
        if len(set([self.unit_id, *activities])) != len(activities) + 1:
            raise ValueError('duplicate unit')
        tasks = {task.id: task for task in self.tasks}
        for item in self.activities:
            if item.task_id not in tasks or tasks[item.task_id].stage != 'practice':
                raise ValueError('activity must use practice task')
            if item.verification_task is not None:
                if item.verification_task.stage != 'checkpoint' or item.verification_task.id != item.verification_task_id or item.verification_task.id in tasks:
                    raise ValueError('activity assessment must be private and separate')
            elif item.verification_task_id not in tasks or tasks[item.verification_task_id].stage != 'checkpoint':
                raise ValueError('verification must use separate checkpoint')
        if any(not p.strip() or len(p) > 120 for p in self.knowledge_points):
            raise ValueError('invalid knowledge point')
        return self


class ValidationReport(Contract):
    schema_version: Literal['validation.v1'] = 'validation.v1'
    status: Literal['passed', 'failed', 'unverifiable']
    verifier: str
    scope: Literal['numeric_expression_only', 'structure_only']
    professional_review: Literal[False] = False
    reasoning_reviewed: Literal[False] = False


class ResourceManifest(Contract):
    schema_version: Literal['resource.v1'] = 'resource.v1'
    resource_id: str
    content_version: str
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    media_type: Literal['text/plain', 'image/png', 'application/pdf']
    sources: list[SourceReference]
    status: Literal['pending_review'] = 'pending_review'


class SkillCandidate(Contract):
    schema_version: Literal['skill-candidate.v1'] = 'skill-candidate.v1'
    parent_version: str
    daily_minutes: int = Field(ge=5, le=60, strict=True)
    reflection_focus: Literal['basis', 'verification']
    status: Literal['pending_review'] = 'pending_review'


class CurriculumDraft(Contract):
    schema_version: Literal['curriculum.v1'] = 'curriculum.v1'
    topic: str = Field(min_length=2, max_length=120)
    title: str = Field(min_length=2, max_length=120)
    unit_ids: list[Identifier] = Field(min_length=1, max_length=30)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.unit_ids)) != len(self.unit_ids):
            raise ValueError('duplicate curriculum unit')
        return self


class RegisteredContentOut(Contract):
    unit_id: str
    version: str
    spec: GeneratedContentSpec
    validation: ValidationReport
    status: Literal['registered_requires_review', 'reviewed_not_enabled', 'enabled_development']
    created_at: int


class RegisteredContentsOut(Contract):
    items: list[RegisteredContentOut]
