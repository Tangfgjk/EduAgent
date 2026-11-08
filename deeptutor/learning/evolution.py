"""Bounded RSI for tutoring strategies, driven by consented independent outcomes.

Only teaching-action templates can evolve. Assessment, evidence, source access,
learner choice and authentication rules are outside the editable policy.
"""

from __future__ import annotations

import hashlib
import math
import time

CONSTRAINT_HASH = hashlib.sha256(
    b"wenjin:independent-evidence:source-isolation:learner-choice:no-answer-leak:v1"
).hexdigest()

TEMPLATES = {
    "reason_first": {
        "error_prompt": "Ask the learner to explain their reasoning before offering a direction cue.",
        "instruction": "One short question about the reasoning, then wait for the learner.",
    },
    "contrast_first": {
        "error_prompt": "Offer one contrasting example without solving the current task; ask the learner to identify the difference.",
        "instruction": "Use a contrasting case to reveal the misconception. Wait before giving any further help.",
    },
}


def regression_check(template: str) -> dict:
    """Frozen admissibility scenarios over the only editable policy surface."""
    cases = {
        "known_template": template in TEMPLATES,
        "no_answer_key": all(
            "expected_answer" not in value for value in TEMPLATES.get(template, {}).values()
        ),
        "wait_for_learner": "Wait" in TEMPLATES.get(template, {}).get("instruction", "")
        or "wait" in TEMPLATES.get(template, {}).get("instruction", ""),
        "immutable_constraints": bool(CONSTRAINT_HASH),
    }
    return {"passed": all(cases.values()), "cases": cases, "constraint_hash": CONSTRAINT_HASH}


def policy_for_next(progress) -> dict:
    state = progress.evolution
    active = state.get("active", {"version": "baseline", "template": "reason_first"})
    candidate = state.get("candidate")
    if state.get("consent") and candidate and candidate["status"] == "trial":
        # Stable deterministic allocation survives restart and does not use identity traits.
        allocation_key = f"{progress.book_id}:{sum(not a.voided for a in progress.quiz_attempts)}"
        slot = hashlib.sha256(allocation_key.encode()).digest()[0] % 5
        if slot == 4:
            active = candidate
    template = TEMPLATES.get(active.get("template"), TEMPLATES["reason_first"])
    return {
        "version": active["version"],
        "template": active.get("template", "reason_first"),
        **template,
    }


def _interval(successes: int, total: int) -> tuple[float, float]:
    if not total:
        return 0.0, 1.0
    z = 1.96
    mean = successes / total
    divisor = 1 + z * z / total
    center = (mean + z * z / (2 * total)) / divisor
    radius = z * math.sqrt((mean * (1 - mean) + z * z / (4 * total)) / total) / divisor
    return max(0.0, center - radius), min(1.0, center + radius)


def metrics(progress, version: str, *, since: float = 0) -> dict:
    attempts = [a for a in progress.quiz_attempts if not a.voided and a.timestamp >= since]
    independent = [
        a
        for a in attempts
        if a.strategy_version == version
        and a.independent
        and a.evidence_kind in ("application", "transfer", "explanation")
    ]
    successes = sum(a.result == "correct" for a in independent)
    delayed = []
    for previous in attempts:
        if previous.strategy_version != version:
            continue
        followup = next(
            (
                a
                for a in attempts
                if a.knowledge_point_id == previous.knowledge_point_id
                and a.timestamp >= previous.timestamp + 86400
                and a.independent
                and a.evidence_kind in ("application", "transfer")
            ),
            None,
        )
        if followup and followup.question_id not in {a.question_id for a in delayed}:
            delayed.append(followup)
    return {
        "independent_n": len(independent),
        "independent_successes": successes,
        "independent_interval": list(_interval(successes, len(independent))),
        "delayed_n": len(delayed),
        "delayed_successes": sum(a.result == "correct" for a in delayed),
        "delayed_interval": list(
            _interval(sum(a.result == "correct" for a in delayed), len(delayed))
        ),
    }


def update_evolution(progress, *, now: float | None = None) -> None:
    state = progress.evolution
    if not state.get("consent"):
        return
    moment = time.time() if now is None else now
    active = state.setdefault("active", {"version": "baseline", "template": "reason_first"})
    history = state.setdefault("history", [])
    since = state.get("consent_at", moment)
    candidate = state.get("candidate")
    if candidate is None:
        if sum(not a.voided and a.timestamp >= since for a in progress.quiz_attempts) < 6:
            state["status"] = "collecting_evidence"
            return
        template = "contrast_first" if active["template"] == "reason_first" else "reason_first"
        report = regression_check(template)
        generation = len(history) + 1
        candidate = {
            "version": f"candidate-{generation}",
            "template": template,
            "status": "trial" if report["passed"] else "rejected",
            "created_at": moment,
            "regression": report,
            "hypothesis": "Compare error prompts using independent explanation, application and delayed transfer.",
            "allocation": 0.2,
        }
        state["candidate"] = candidate
        state.pop("evaluation", None)
        state["status"] = candidate["status"]
        history.append(
            {
                "event": "candidate_created",
                "at": moment,
                "version": candidate["version"],
                "constraint_hash": CONSTRAINT_HASH,
            }
        )
        return
    if candidate["status"] in ("rejected", "rolled_back"):
        closed = state.setdefault("closed_attempts", len(progress.quiz_attempts))
        if len(progress.quiz_attempts) >= closed + 12:
            state.pop("candidate", None)
            state.pop("closed_attempts", None)
            state["status"] = "collecting_evidence"
        return
    if candidate["status"] != "trial":
        return
    baseline = metrics(progress, active["version"], since=since)
    treatment = metrics(progress, candidate["version"], since=since)
    state["evaluation"] = {"baseline": baseline, "candidate": treatment}
    enough = all(m["independent_n"] >= 12 and m["delayed_n"] >= 12 for m in (baseline, treatment))
    if not enough:
        state["status"] = "trial_insufficient_evidence"
        return
    improves = treatment["independent_interval"][0] > baseline["independent_interval"][1]
    retains = treatment["delayed_interval"][0] >= baseline["delayed_interval"][1]
    if candidate["regression"]["constraint_hash"] != CONSTRAINT_HASH:
        candidate["status"] = "rejected"
        state["status"] = "constraint_mismatch"
    elif improves and retains:
        state["previous"] = dict(active)
        state["active"] = {"version": candidate["version"], "template": candidate["template"]}
        state["promoted_at"] = moment
        candidate["status"] = "promoted"
        state["status"] = "monitoring"
        history.append(
            {
                "event": "promoted",
                "at": moment,
                "version": candidate["version"],
                "report": state["evaluation"],
            }
        )
    elif (
        treatment["independent_interval"][1] < baseline["independent_interval"][0]
        or treatment["delayed_interval"][1] < baseline["delayed_interval"][0]
    ):
        candidate["status"] = "rejected"
        state["status"] = "rejected"
        history.append(
            {
                "event": "candidate_rejected",
                "at": moment,
                "version": candidate["version"],
                "report": state["evaluation"],
            }
        )


def monitor_evolution(progress) -> None:
    state = progress.evolution
    if not state.get("consent") or state.get("status") != "monitoring":
        return
    current = metrics(progress, state["active"]["version"], since=state["promoted_at"])
    old = (state.get("evaluation") or {}).get("baseline", {})
    if current["independent_n"] < 12 or current["delayed_n"] < 12:
        return
    if (
        current["independent_interval"][1] < old.get("independent_interval", [0, 1])[0]
        or current["delayed_interval"][1] < old.get("delayed_interval", [0, 1])[0]
    ):
        previous = state["previous"]
        state["history"].append(
            {"event": "automatic_rollback", "at": time.time(), "report": current}
        )
        state["active"] = previous
        state["status"] = "rolled_back"
        state["candidate"]["status"] = "rolled_back"
    elif current["independent_n"] >= 24 and current["delayed_n"] >= 24:
        state["history"].append(
            {"event": "monitoring_completed", "at": time.time(), "report": current}
        )
        state.pop("candidate", None)
        state.pop("previous", None)
        state["status"] = "collecting_evidence"


def set_consent(store, path_id: str, enabled: bool):
    def change(tx):
        state = tx.progress.evolution
        previous = bool(state.get("consent"))
        state["consent"] = enabled
        if enabled and not previous:
            state["consent_at"] = time.time()
            state["status"] = "collecting_evidence"
            state.pop("candidate", None)
        if not enabled:
            state["status"] = "paused"
        tx.emit("wenjin.evolution_consent", {"enabled": enabled})

    progress, _ = store.mutate(path_id, change)
    return progress


def rollback(store, path_id: str):
    def change(tx):
        state = tx.progress.evolution
        if "previous" not in state:
            raise ValueError("No promoted strategy to roll back.")
        state["active"] = state.pop("previous")
        state["status"] = "rolled_back"
        if state.get("candidate"):
            state["candidate"]["status"] = "rolled_back"
        state.setdefault("history", []).append({"event": "learner_rollback", "at": time.time()})
        tx.emit("wenjin.policy_rolled_back", {"active": state["active"]})

    progress, _ = store.mutate(path_id, change)
    return progress


def revalidate_after_repair(progress) -> None:
    """Withdraw a promotion when corrected evidence no longer supports it."""
    state = progress.evolution
    if not state.get("consent") or state.get("status") != "monitoring" or not state.get("previous"):
        update_evolution(progress)
        return
    baseline = metrics(progress, state["previous"]["version"], since=state["consent_at"])
    treatment = metrics(progress, state["active"]["version"], since=state["consent_at"])
    enough = all(m["independent_n"] >= 12 and m["delayed_n"] >= 12 for m in (baseline, treatment))
    supported = (
        enough
        and treatment["independent_interval"][0] > baseline["independent_interval"][1]
        and treatment["delayed_interval"][0] >= baseline["delayed_interval"][1]
    )
    state["evaluation"] = {"baseline": baseline, "candidate": treatment}
    if not supported:
        state["active"] = state["previous"]
        state["candidate"]["status"] = "rolled_back"
        state["status"] = "rolled_back"
        state.setdefault("history", []).append(
            {"event": "evidence_revalidation_rollback", "at": time.time()}
        )
