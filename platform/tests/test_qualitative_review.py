import pytest
from app.learning.assets import RubricAsset,VersionedRef,RubricDimension
from app.learning.qualitative import RubricReview,score_review


def rubric():
    return RubricAsset(ref=VersionedRef(asset_id="proof",version="v1"),passing_score=.8,
        assessor_policy="teacher",provenance="synthetic-test",dimensions=(
            RubricDimension(dimension_id="reason",description="valid reasoning",weight=.6),
            RubricDimension(dimension_id="check",description="self verification",weight=.4)))


def test_rubric_review_is_weighted_and_low_confidence_cannot_pass():
    r=RubricReview(dimension_scores={"reason":1,"check":.5},confidence=.9,reason="Reviewed steps",assessor_version="teacher-v1")
    result=score_review(rubric(),r,assessor_type="teacher")
    assert result["status"]=="passed" and result["score"]==.8 and result["qualitative_pass"]
    assert score_review(rubric(),r.model_copy(update={"confidence":.5}),assessor_type="reviewed_llm")["qualitative_pass"] is None


def test_missing_or_unsafe_scores_and_student_assessor_rejected():
    r=RubricReview(dimension_scores={"reason":1},confidence=.9,reason="Reviewed",assessor_version="v1")
    with pytest.raises(ValueError): score_review(rubric(),r,assessor_type="teacher")
    with pytest.raises(ValueError): score_review(rubric(),r,assessor_type="student")
