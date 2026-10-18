from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field, field_validator

from app.modules.learning.contracts import Contract
from app.modules.governance import reviews, workbench
from app.modules.learning.tutor_contracts import RoundtableVoiceOut
from app.modules.planning.contracts import PlanningCitation, SoftPolicy


class ReviewRecordOut(Contract):
    revision: int
    conclusion: str
    note: str
    created_at: int
    review_kind: Literal['development_only']


class ReviewStateOut(Contract):
    revision: int
    reviews: list[ReviewRecordOut]
    required: bool = False
    professional_review: Literal[False] = False


class EvaluationAssessmentOut(Contract):
    state: Literal['simulation_only', 'blocked', 'needs_review', 'development_candidate']
    engineering_failures: int
    reference_mismatches: int
    pending_reviews: int
    review_issues: int
    automatic_publish: Literal[False]
    notice: str


class EvaluationSummaryOut(Contract):
    report_id: str
    title: str
    report_sha256: str
    mode: Literal['fake', 'live']
    prompt_version: str
    created_at: str
    cases: int
    needs_review: int
    reviewed_cases: int
    report_kind: Literal['tutoring', 'teach', 'roundtable', 'planning']
    corpus_sha256: str
    model: str
    assessment: EvaluationAssessmentOut


class EvaluationIndexOut(Contract):
    items: list[EvaluationSummaryOut]
    notice: str


class EvaluationCaseOut(Contract):
    text: str
    rationale: str
    expected_focus: list[str]
    selected_focus: str | None
    status: str
    quote: str
    question: str | None
    direction_match: bool
    contract_pass: bool
    citation_pass: bool
    policy_pass: bool
    elapsed_ms: int | None
    usage: dict | None
    context_turns: int | None
    error_code: str | None
    category: str | None = None
    answer: str | None = None
    repeat_of: str | None = None
    semantic_review_required: bool = False
    outbound_identity_canary_absent: bool | None = None
    voices: list[RoundtableVoiceOut] = Field(default_factory=list)
    policy: SoftPolicy | None = None
    citations: list[PlanningCitation] = Field(default_factory=list)
    source_excerpts: dict[str, str] = Field(default_factory=dict)


class EvaluationRowOut(Contract):
    target_id: str
    revision: int
    reviews: list[ReviewRecordOut]
    case: EvaluationCaseOut


class EvaluationDetailOut(Contract):
    report_id: str
    title: str
    report_sha256: str
    mode: Literal['fake', 'live']
    prompt_version: str
    cases: list[EvaluationRowOut]
    summary: dict
    report_kind: Literal['tutoring', 'teach', 'roundtable', 'planning']
    human_review_rubric: list[str]
    assessment: EvaluationAssessmentOut


class ComparisonCaseOut(Contract):
    case_id: str
    left_match: bool
    right_match: bool
    left_focus: str | None
    right_focus: str | None
    left_status: str
    right_status: str
    left_question: str | None
    right_question: str | None


class EvaluationComparisonOut(Contract):
    left_id: str
    right_id: str
    left_sha256: str
    right_sha256: str
    left_prompt: str
    right_prompt: str
    cases: list[ComparisonCaseOut]
    improved: int
    regressed: int
    unchanged: int
    left_summary: dict
    right_summary: dict
    notice: str


class ReviewInput(Contract):
    kind: Literal['content', 'evaluation']
    target_id: str = Field(min_length=1, max_length=160)
    version: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_revision: int = Field(ge=0, strict=True)
    conclusion: Literal['development_ready', 'needs_revision', 'acceptable', 'model_issue', 'reference_ambiguous', 'runtime_issue']
    note: str = Field(min_length=5, max_length=2000)

    @field_validator('note')
    @classmethod
    def meaningful(cls, text):
        if len(text.strip()) < 5:
            raise ValueError('请填写至少五个字的复核依据')
        return text.strip()


def development_router(admin):
    router = APIRouter(prefix='/api/v1/admin')
    auth = Depends(admin, scope='function')

    @router.get('/evaluation-reports', response_model=EvaluationIndexOut)
    def reports(ctx=auth):
        return workbench.index(ctx.session)

    @router.get('/evaluation-comparison', response_model=EvaluationComparisonOut)
    def comparison(left_id: str, right_id: str, ctx=auth):
        return workbench.compare(left_id, right_id)

    @router.get('/evaluation-reports/{report_id}', response_model=EvaluationDetailOut)
    def report(report_id: str, ctx=auth):
        return workbench.detail(ctx.session, report_id)

    @router.get('/development-reviews', response_model=ReviewStateOut)
    def inspect(target_id: str, version: str, ctx=auth):
        item = reviews.content_target(target_id, ctx.session)
        if item.version != version:
            from app.modules.errors import DomainError
            raise DomainError(409, 'version_conflict', '当前内容版本已变化')
        rows = reviews.records(ctx.session, 'content', target_id, version)
        return {'revision': rows[0]['revision'] if rows else 0, 'reviews': rows,
            'required': target_id in reviews.REQUIRED_CONTENT or target_id.startswith('dynamic.'), 'professional_review': False}

    @router.post('/development-reviews', response_model=ReviewStateOut)
    def review(body: ReviewInput, ctx=auth):
        return reviews.save(ctx.session, ctx.actor, body.model_dump())

    return router
