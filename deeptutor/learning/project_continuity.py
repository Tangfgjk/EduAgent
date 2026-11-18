"""Bounded, source-backed project handoff; never grants mastery credit."""

from deeptutor.learning.wenjin import state_summary


def project_handoff(progress, store=None):
    state = state_summary(progress, store=store)
    pending = progress.pending_question
    structured_activity = []
    if store is not None:
        for event in store.list_events(progress.book_id)[-50:]:
            if event.event_type in {
                "scaffold.requested",
                "classroom.answer_recorded",
                "classroom.help_delivered",
                "planner.decision",
                "plan.rollback",
                "wenjin.reflection_recorded",
            }:
                structured_activity.append(
                    {
                        "event_id": event.id,
                        "type": event.event_type,
                        "session_id": event.session_id,
                        "turn_id": event.turn_id,
                        "at": event.created_at,
                        "help_kind": event.payload.get("kind"),
                        "objective_id": event.payload.get("objective_id"),
                        "question_id": event.payload.get("question_id"),
                        "classroom_id": event.payload.get("classroom_id"),
                        "scene_id": event.payload.get("scene_id"),
                    }
                )
    return {
        "reading_context": next(
            (
                e.payload
                for e in reversed(store.list_events(progress.book_id))
                if e.event_type == "classroom.tutor_requested"
            ),
            None,
        )
        if store is not None
        else None,
        "project_id": progress.book_id,
        "source_revision": progress.version,
        "objective": state["objective"],
        "pending_task": pending.model_dump(mode="json") if pending else None,
        "objectives": state["objectives"],
        "recent_reflections": state["reflections"],
        "structured_activity": structured_activity,
        "diagnostic_decision": state["decision"],
        "rules": [
            "Use this live project state across conversations; do not overwrite newer progress from old chat.",
            "Continue the registered pending task before proposing another task unless the learner explicitly changes it.",
            "Reflections are learner statements, not independently verified mastery.",
            "Explain briefly where learning resumes; the learner can choose another topic or restore help.",
        ],
    }
