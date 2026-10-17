from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field, field_validator

from app.modules.learning.contracts import Contract
from app.modules.governance import reviews, workbench


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

    @router.get('/evaluation-reports/{report_id}', response_model=EvaluationDetailOut)
    def report(report_id: str, ctx=auth):
        return workbench.detail(ctx.session, report_id)

    @router.get('/development-reviews', response_model=ReviewStateOut)
    def inspect(target_id: str, version: str, ctx=auth):
        item = reviews.content_target(target_id)
        if item.version != version:
            from app.modules.errors import DomainError
            raise DomainError(409, 'version_conflict', '当前内容版本已变化')
        rows = reviews.records(ctx.session, 'content', target_id, version)
        return {'revision': rows[0]['revision'] if rows else 0, 'reviews': rows,
            'required': target_id in reviews.REQUIRED_CONTENT, 'professional_review': False}

    @router.post('/development-reviews', response_model=ReviewStateOut)
    def review(body: ReviewInput, ctx=auth):
        return reviews.save(ctx.session, ctx.actor, body.model_dump())

    return router
