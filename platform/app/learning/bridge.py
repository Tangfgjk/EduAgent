"""Runtime adapter: all cognition writes pass through LearningService."""
from app.core.schema import utcnow
from app.learning.schema import LearningEvidence
from app.learning.service import LearningService


def record_attempt(store, learner_id, session_id, item, verdict, attempt_id, *,
                   hint_level=0, answer_exposed=False, assessment_kind="practice",
                   action_ref=None, policy_version="policy_v1", occurred_at=None):
    service = LearningService(store)
    service._authorize(learner_id, "teaching")
    consent = service.consent(learner_id)
    evidence = LearningEvidence(
        evidence_id=f"evidence:{learner_id}:{attempt_id}", learner_id=learner_id,
        session_id=session_id, kc_refs=[item.kc_id], attempt_id=attempt_id,
        artifact_ref=verdict.artifact_id, verdict_ref=verdict.artifact_id,
        verdict_status=verdict.status, verifier_version=verdict.verifier_id,
        verifier_details={"rubric":verdict.rubric,"explainability":verdict.explainability,
                          "taxonomy_level":verdict.taxonomy_level.value,
                          "misconception_hits":verdict.misconception_hits},
        confidence=1.0 if "sympy" in verdict.verifier_id and verdict.status in {"passed","failed"} else verdict.rubric.get("confidence"),
        hint_level=hint_level,
        assistance_mode="answer" if answer_exposed else "hint" if hint_level else "none",
        answer_exposed=answer_exposed, occurred_at=occurred_at or utcnow(),
        consent_scope=consent["scopes"], consent_version=consent["version"],
        authorization_source=consent["source"], assessment_id=item.item_id,
        assessment_kind=assessment_kind, score=verdict.score,
        difficulty_band=str(item.difficulty), action_ref=action_ref,policy_version=policy_version)
    return service.consume(evidence)


def refresh_projection(store, snapshot):
    current = store.latest_snapshot(snapshot.learner_id)
    if current:
        snapshot.knowledge_state = current.knowledge_state.model_copy(deep=True)
