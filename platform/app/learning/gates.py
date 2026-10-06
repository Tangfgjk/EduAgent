"""Assessment-profile gates over traceable independent evidence."""
from app.learning.mastery_port import observation_skip_reason
from app.learning.schema import AssessmentProfile, LearningEvidence, MasteryGateResult, MasteryState


def evaluate_gate(state: MasteryState, evidences: list[LearningEvidence],
                  profile: AssessmentProfile) -> MasteryGateResult:
    """All modes require counts and quality; qualitative votes cannot bypass them."""
    empty = MasteryState(learner_id=state.learner_id, kc_id=state.kc_id)
    by_id = {}
    conflicting = False
    for item in evidences:
        if item.evidence_id in by_id and by_id[item.evidence_id] != item:
            conflicting = True
        by_id[item.evidence_id] = item
    valid = [by_id[ref] for ref in state.evidence_refs if ref in by_id
             and observation_skip_reason(empty, by_id[ref]) is None]
    count = len(valid)
    qualitative = [item for item in valid if item.qualitative_pass is not None]
    needs_qualitative = profile.mode in ("qualitative", "hybrid")
    distinct = {(item.assessment_id,item.assessment_version) for item in valid}
    if count < profile.min_evidence or len(distinct) < profile.min_distinct_assessments or (needs_qualitative and len(qualitative) < profile.min_qualitative):
        status, reason = "insufficient_evidence", "Insufficient independent observations or qualitative assessments"
    elif (conflicting or count != state.effective_evidence_count
          or len(set(state.evidence_refs)) != len(state.evidence_refs)
          or state.confidence_method != "heuristic-count-v1"
          or state.confidence < profile.min_confidence
          or any(item.confidence is None or item.confidence < profile.min_confidence for item in valid)):
        status, reason = "uncertain", "Missing, low-confidence, conflicting, or incomplete evidence provenance"
    elif ((profile.mode in ("quantitative", "hybrid") and state.p_mastery < profile.mastery_threshold)
          or (needs_qualitative and not all(item.qualitative_pass for item in qualitative))):
        status, reason = "not_mastered", "Quantitative threshold or qualitative assessment requirements not met"
    else:
        status, reason = "mastered", "Independent evidence, heuristic confidence, and profile requirements satisfied"
    return MasteryGateResult(status=status, probability=state.p_mastery, confidence=state.confidence,
        confidence_method=state.confidence_method, effective_evidence_count=count,
        evidence_refs=[item.evidence_id for item in valid],
        assessment_profile_ref=f"{profile.profile_id}@{profile.version}", reason=reason)
