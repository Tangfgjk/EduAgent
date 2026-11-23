"""Read-only, owner-scoped diagnostic facts over native durable learning records.

No new mastery state, fabricated scores, raw-chat inference or external KT calls.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json

from deeptutor.learning.diagnosis_models import (
    DiagnosticContext,
    DiagnosticFacts,
    DiagnosticObservation,
)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def assessment_context(
    progress,
    question,
    expected_answer,
    question_type,
    *,
    assistance_used=False,
    scaffold_level=0,
    hints_used=0,
    semantic=False,
):
    assisted = bool(assistance_used or scaffold_level or hints_used)
    if question is None:
        return DiagnosticContext(
            assistance="assisted" if assisted else "unknown",
            grading_method="semantic" if semantic else "deterministic",
            grading_version="native-v1",
            rubric_fingerprint=_digest(expected_answer),
        )
    kp = next(
        (
            p
            for m in progress.modules
            for p in m.knowledge_points
            if p.id == question.knowledge_point_id
        ),
        None,
    )
    contract = {
        "prompt": question.prompt,
        "type": question_type,
        "options": [o.model_dump() for o in question.options],
        "answer": expected_answer,
        "accepted": question.accepted_answers,
        "source": question.source_evidence,
    }
    return DiagnosticContext(
        goal_version=getattr(question, "goal_version", 0),
        task_prompt=question.prompt,
        question_type=question_type,
        task_version=_digest(contract),
        objective_name=kp.name if kp else "",
        objective_scope=_digest(kp.model_dump(mode="json")) if kp else "",
        grading_method="semantic" if semantic else "deterministic",
        grading_version="native-v1",
        rubric_fingerprint=_digest(
            {"answer": expected_answer, "accepted": question.accepted_answers}
        ),
        assistance="assisted" if assisted or question.assistance_used else "independent",
        scaffold_level=scaffold_level,
    )


def project_facts(progress, events=()):
    """Pure deterministic projection: a quiz and its retention receipt count once.

    Legacy links are inferred only for a unique question/objective pair on BOTH sides.
    Ambiguous rows remain inspectable, explicitly excluded from trusted counts.
    """
    facts = DiagnosticFacts(
        project_id=progress.book_id,
        source_revision=progress.version,
        limitations=[
            "Facts only: no calibrated mastery probability or teaching decision.",
            "Legacy missing assistance metadata is unknown, not independent.",
            "Only structured assessment records are observations; ordinary chat is not scored.",
        ],
    )
    attempts = progress.quiz_attempts
    receipts = progress.learning_evidence

    def key(item):
        return (item.question_id, item.knowledge_point_id)

    ac = Counter(key(a) for a in attempts)
    ec = Counter(key(e) for e in receipts if e.question_id)
    id_counts = Counter(e.evidence_id for e in receipts if e.evidence_id)
    consumed = set()
    for index, e in enumerate(receipts):
        candidates = [
            (i, a)
            for i, a in enumerate(attempts)
            if e.evidence_id and a.diagnostic_evidence_id == e.evidence_id
        ]
        inferred = False
        if not candidates and e.question_id and ac[key(e)] == ec[key(e)] == 1:
            candidates = [
                (i, a)
                for i, a in enumerate(attempts)
                if key(a) == key(e) and not a.diagnostic_evidence_id
            ]
            inferred = bool(candidates)
        # Never attach another attempt by name, nearest timestamp or list position.
        paired = len(candidates) == 1 and candidates[0][0] not in consumed
        ai, a = candidates[0] if paired else (-1, None)
        if paired:
            consumed.add(ai)
        refs = [f"learning_evidence:{index}"]
        if a is not None:
            refs.append(f"quiz_attempts:{ai}")
        ambiguous = (
            len(candidates) > 1
            or bool(e.evidence_id and id_counts[e.evidence_id] > 1)
            or bool(e.question_id and not paired and ac[key(e)])
        )
        conflict = bool(
            a
            and (
                a.knowledge_point_id != e.knowledge_point_id
                or a.question_id != e.question_id
                or a.result != e.result
            )
        )
        validity = (
            "voided" if a and a.voided else "conflicting" if ambiguous or conflict else "recorded"
        )
        ctx = e.diagnostic_context
        assistance = ctx.assistance if ctx else "unknown"
        if e.hints_used or a is not None and not a.independent:
            assistance = "assisted"
        limitations = []
        if inferred:
            limitations.append("Legacy receipt linked by unique question/objective pair.")
        if validity == "conflicting":
            limitations.append(
                "Ambiguous or inconsistent source records; excluded from valid counts."
            )
        if ctx is not None and not ctx.task_version:
            limitations.append("No registered task snapshot; independence is not inferred.")
        if ctx is None:
            limitations.append("Task/grading snapshot unavailable in legacy record.")
        answer = e.learner_answer if e.learner_answer else a.user_answer if a is not None else None
        facts.observations.append(
            DiagnosticObservation(
                observation_id="receipt:"
                + (
                    e.evidence_id if e.evidence_id and id_counts[e.evidence_id] == 1 else str(index)
                ),
                objective_id=e.knowledge_point_id,
                question_id=e.question_id,
                origin="local_assessment" if ctx else "legacy_record",
                source_refs=refs,
                session_id=e.session_id,
                turn_id=e.turn_id,
                occurred_at=e.timestamp,
                ability=e.evidence_kind,
                answer=answer,
                response_status="answered" if answer is not None else "unknown",
                result=e.result,
                assistance=assistance,
                validity=validity,
                limitations=limitations,
                context=ctx,
            )
        )
    for index, a in enumerate(attempts):
        if index in consumed:
            continue
        ambiguous = bool(a.diagnostic_evidence_id or ec[key(a)] or ac[key(a)] > 1)
        facts.observations.append(
            DiagnosticObservation(
                observation_id=f"attempt:{index}",
                objective_id=a.knowledge_point_id,
                question_id=a.question_id,
                origin="legacy_record",
                source_refs=[f"quiz_attempts:{index}"],
                occurred_at=a.timestamp,
                ability=a.evidence_kind,
                answer=a.user_answer,
                response_status="answered" if a.user_answer is not None else "unknown",
                result=a.result,
                assistance="unknown" if a.independent else "assisted",
                validity="voided" if a.voided else "conflicting" if ambiguous else "recorded",
                limitations=[
                    "Legacy standalone attempt; missing task snapshot and verified independence."
                ],
            )
        )
    # Profile event values are immutable history; old events without values are not reconstructed.
    for event in events:
        if event.event_type == "path.learner_profile_recorded" and event.payload.get("values"):
            facts.self_reports.append(
                {
                    "source_ref": f"mastery_events:{event.id}",
                    "kind": "profile_update",
                    "values": event.payload["values"],
                    "learner_quote": event.payload.get("learner_quote", ""),
                    "session_id": event.session_id,
                    "turn_id": event.turn_id,
                    "occurred_at": event.created_at,
                    "verification": "self_report_not_assessment",
                }
            )
    profile = progress.learner_profile
    if profile:
        facts.self_reports.append(
            {
                "source_ref": "learner_profile",
                "kind": "current_profile",
                "values": {
                    name: getattr(profile, name)
                    for name in (
                        "prior_knowledge",
                        "target_level",
                        "time_budget",
                        "preferences",
                        "teaching_strategy",
                        "notes",
                    )
                },
                "occurred_at": profile.updated_at,
                "verification": "self_report_not_assessment",
                "limitation": "Current profile is a view, not an extra independent observation.",
            }
        )
    for module in progress.modules:
        for point in module.knowledge_points:
            rows = [
                o
                for o in facts.observations
                if o.objective_id == point.id and o.validity == "recorded"
            ]
            facts.objectives.append(
                {
                    "objective_id": point.id,
                    "name": point.name,
                    "measurement_status": "observed" if rows else "unmeasured",
                    "observation_count": len(rows),
                    "independent_count": sum(o.assistance == "independent" for o in rows),
                    "assisted_count": sum(o.assistance == "assisted" for o in rows),
                    "unknown_assistance_count": sum(o.assistance == "unknown" for o in rows),
                }
            )
    from deeptutor.learning.external_diagnoses import external_references

    facts.external_references = external_references(progress)
    return facts


def load_project_facts(store, path_id):
    # Retry if native records change between reading the aggregate and event history.
    for _ in range(3):
        progress = store.load(path_id)
        if progress is None:
            raise LookupError("Learning project not found")
        events = store.list_events(path_id)
        latest = store.load(path_id)
        if latest and latest.version == progress.version:
            return project_facts(progress, events)
    raise RuntimeError("Learning records changed. Please retry.")
