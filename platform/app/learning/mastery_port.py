"""Pure BKT consumer. Only independent binary observations affect mastery."""
from __future__ import annotations

from app.learning.schema import LearningEvidence, MasteryState

BKT_VERSION = "bkt-v1"
BKT_PARAMETERS = "bkt-default-v1"
CONFIDENCE_METHOD = "heuristic-count-v1"
P_LEARN, P_GUESS, P_SLIP = 0.15, 0.2, 0.1


def observation_skip_reason(state, evidence: LearningEvidence) -> str | None:
    """Shared eligibility, independent of the consumer's algorithm or storage."""
    if state.learner_id != evidence.learner_id:
        return "learner_mismatch"
    if state.kc_id not in evidence.kc_refs:
        return "kc_mismatch"
    if "teaching" not in evidence.consent_scope:
        return "teaching_not_authorized"
    if evidence.evidence_id in state.evidence_refs:
        return "already_consumed"
    if evidence.verdict_status in ("partial", "unverifiable"):
        return evidence.verdict_status
    if evidence.verifier_details.get("self_report", evidence.verifier_details.get("diagnostic_response")) in {"guessed", "dont_know", "skipped"}:
        return "not_independent_recall"
    if evidence.observation_policy != "binary-observation-v1":
        return "unsupported_observation_policy"
    if evidence.hint_level or evidence.assistance_mode != "none" or evidence.answer_exposed:
        return "assisted_observation"
    return None


def update_mastery(state: MasteryState, evidence: LearningEvidence) -> tuple[MasteryState, str | None]:
    """Return a new state or the unchanged state with a machine-readable reason.

    Confidence is a conservative evidence-count heuristic, never a calibrated
    statistical probability: n/(n+3) times the lowest observed verifier quality.
    A missing confidence poisons this estimate until corrected evidence is replayed.
    """
    if (state.algorithm_version, state.parameter_set_id, state.confidence_method) != (
            BKT_VERSION, BKT_PARAMETERS, CONFIDENCE_METHOD):
        raise ValueError("Unsupported mastery algorithm, parameters, or confidence method")
    reason = observation_skip_reason(state, evidence)
    if reason:
        return state, reason
    p = state.p_mastery
    likelihood = (1 - P_SLIP, P_GUESS) if evidence.verdict_status == "passed" else (P_SLIP, 1 - P_GUESS)
    posterior = p * likelihood[0] / (p * likelihood[0] + (1 - p) * likelihood[1])
    probability = min(1.0, max(0.0, posterior + (1 - posterior) * P_LEARN))
    n = state.effective_evidence_count
    previous_quality = state.confidence / (n / (n + 3)) if n else 1.0
    quality = min(previous_quality, evidence.confidence if evidence.confidence is not None else 0.0)
    count = n + 1
    return state.model_copy(update={
        "state_version": state.state_version + 1,
        "p_mastery": probability,
        "confidence": min(1.0, max(0.0, quality * count / (count + 3))),
        "effective_evidence_count": count,
        "evidence_refs": [*state.evidence_refs, evidence.evidence_id],
    }), None
