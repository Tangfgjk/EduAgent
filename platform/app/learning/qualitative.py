"""Explicit rubric scoring of reviewed artifacts. A task label never proves ability."""
from pydantic import BaseModel, ConfigDict, Field
from app.learning.assets import RubricAsset


class RubricReview(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    dimension_scores: dict[str,float]
    confidence: float=Field(ge=0,le=1)
    reason: str=Field(min_length=1,max_length=4000)
    assessor_version: str=Field(min_length=1)


def score_review(rubric: RubricAsset, review: RubricReview, *, assessor_type: str):
    if assessor_type not in {"teacher","reviewed_llm"}:
        raise ValueError("Authorized reviewing assessor required")
    dimensions={d.dimension_id for d in rubric.dimensions}
    if set(review.dimension_scores)!=dimensions or any(type(s) not in {int,float} or not 0<=s<=1 for s in review.dimension_scores.values()):
        raise ValueError("Each exact rubric dimension requires a score in [0,1]")
    score=sum(review.dimension_scores[d.dimension_id]*d.weight for d in rubric.dimensions)
    status="unverifiable" if review.confidence<.7 else "passed" if score>=rubric.passing_score else "failed" if score==0 else "partial"
    return dict(status=status,score=score,qualitative_pass=score>=rubric.passing_score if status!="unverifiable" else None,
        confidence=review.confidence,rubric_ref=rubric.ref.key,rubric=review.dimension_scores,
        assessor_type=assessor_type,assessor_version=review.assessor_version,reason=review.reason)
