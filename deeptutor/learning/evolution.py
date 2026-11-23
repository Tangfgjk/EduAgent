"""Bounded RSI for tutoring strategies, driven by consented independent outcomes.

Only teaching-action templates can evolve. Assessment, evidence, source access,
learner choice and authentication rules are outside the editable policy.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import time

# Versioned host-enforced invariants, not labels for an editable prompt.
CONSTRAINTS = {
    "version": 2,
    "independent_evidence": {
        "assistance": "independent",
        "grading": "deterministic",
        "validity": "recorded",
    },
    "source_scope": "owner/workspace/project/objective-version",
    "learner_choice": "never override explicit assistance choice",
    "fading": "one step; current goal; fresh independent evidence; restorable",
    "strategy_surface": "sealed teaching templates only; no assessment or access changes",
    "comparison": "equal-stratum-weight; minimum 12 per arm per stratum; simultaneous intervals",
}


def _hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


# Bind each saved candidate to the deployed implementations of host guards.
# Changing these rules between deployments requires re-admission, even if the
# human-readable registry label was accidentally left unchanged.
_GUARD_FILES = (
    "learning/learning_support.py",
    "learning/goal_contract.py",
    "learning/wenjin.py",
    "learning/diagnosis.py",
    "learning/check_cycles.py",
    "learning/strategy_effects.py",
    "multi_user/tool_access.py",
)
CONSTRAINTS["implementation_digests"] = {
    name: hashlib.sha256((Path(__file__).parents[1] / name).read_bytes()).hexdigest()
    for name in _GUARD_FILES
}
CONSTRAINT_HASH = _hash(CONSTRAINTS)


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
TEMPLATES.update(
    {
        "self_explain": {
            "name": "自己解释一步",
            "error_prompt": "Ask the learner to explain one chosen step in their own words without supplying the solution.",
            "instruction": "Ask for one step's rationale, then Wait for the learner.",
        },
        "plan_first": {
            "name": "先列计划",
            "error_prompt": "Ask the learner to state a short plan before calculating; do not solve the task.",
            "instruction": "Ask for the first planned step, then Wait for the learner.",
        },
        "boundary_check": {
            "name": "辨析适用条件",
            "error_prompt": "Ask which condition permits the method and request a counterexample without giving the answer.",
            "instruction": "Ask one boundary question, then Wait for the learner.",
        },
    }
)
TEMPLATES["reason_first"]["name"] = "先说明理由"
TEMPLATES["contrast_first"]["name"] = "对照不同例子"


APPROVED_TEMPLATE_HASHES = {
    "reason_first": "5751a667f954a8788e80e7cb3add78537426fda435c30d8162c4cdcb22715558",
    "contrast_first": "e2285e81d0d45f92e7b28657c595d2e7ca599a89f8d06b03fd03188471988059",
    "self_explain": "879ed0607711e4101fc2e8bd04d1ca3b4511c44e1e0ee5ecbfa5773c988a9e93",
    "plan_first": "37764b5d63f5b2e3328aa9ef6b6b53f20cc44099ee6e8bb4cef30af9cf41b138",
    "boundary_check": "fa7355e33d1ec62ef2aa161824c290c7033a29dbd603e0118145d9cf44254dde",
}


def regression_check(template: str) -> dict:
    """Static artifact admission; behavioral regressions run against real host tools in CI.

    This result does not claim to validate arbitrary model-generated prose.
    """
    body = TEMPLATES.get(template)
    digest = _hash(body) if body else ""
    cases = {
        "known_template": template in APPROVED_TEMPLATE_HASHES,
        "reviewed_body": bool(body) and digest == APPROVED_TEMPLATE_HASHES.get(template),
        "immutable_constraints": _hash(CONSTRAINTS) == CONSTRAINT_HASH,
    }
    return {
        "passed": all(cases.values()),
        "cases": cases,
        "constraint_hash": _hash(CONSTRAINTS),
        "template_hash": digest,
        "scope": "static_artifact_admission",
    }


def policy_for_next(progress) -> dict:
    state = progress.evolution
    active = state.get("active", {"version": "baseline", "template": "reason_first"})
    candidate = state.get("candidate")
    if (
        state.get("consent")
        and state.get("protocol") == "observed-delivery-v2"
        and candidate
        and candidate["status"] == "trial"
    ):
        # Stable deterministic allocation survives restart and does not use identity traits.
        allocation_key = f"{progress.book_id}:{sum(not a.voided for a in progress.quiz_attempts)}"
        slot = hashlib.sha256(allocation_key.encode()).digest()[0] % 5
        if slot == 4:
            active = candidate
    current = regression_check(active.get("template", ""))
    if active["version"] != "baseline":
        saved = (
            active.get("regression", {})
            if not candidate or active["version"] != candidate["version"]
            else candidate.get("regression", {})
        )
        if not saved.get("passed") or any(
            saved.get(key) != current.get(key) for key in ("constraint_hash", "template_hash")
        ):
            active = {"version": "baseline", "template": "reason_first"}

    if not regression_check(active.get("template", ""))["passed"]:
        active = {"version": "baseline", "template": "reason_first"}
    if not regression_check(active["template"])["passed"]:
        raise RuntimeError("No admitted teaching template available")
    template = TEMPLATES[active["template"]]
    return {
        "version": active["version"],
        "template": active.get("template", "reason_first"),
        **template,
    }


def _interval(successes: int, total: int, *, z: float = 1.96) -> tuple[float, float]:
    if not total:
        return 0.0, 1.0
    mean = successes / total
    divisor = 1 + z * z / total
    center = (mean + z * z / (2 * total)) / divisor
    radius = z * math.sqrt((mean * (1 - mean) + z * z / (4 * total)) / total) / divisor
    return max(0.0, center - radius), min(1.0, center + radius)


def upgrade_protocol(progress, *, now=None):
    state = progress.evolution
    if not state.get("consent") or state.get("protocol") == "observed-delivery-v2":
        return
    moment = time.time() if now is None else now
    if state.get("candidate") or state.get("evaluation"):
        state.setdefault("history", []).append(
            {
                "event": "protocol_upgraded",
                "at": moment,
                "archived_candidate": state.get("candidate"),
                "archived_evaluation": state.get("evaluation"),
                "reason": "历史无交付证明的结果仅作回顾，不作为新版效果依据",
            }
        )
    for key in ("candidate", "evaluation", "promotion_evaluation", "previous"):
        state.pop(key, None)
    state.update(
        protocol="observed-delivery-v2",
        protocol_at=moment,
        exposures=[],
        status="collecting_evidence",
    )


def metrics(progress, version: str, *, since: float = 0):
    from deeptutor.learning.strategy_effects import metrics as observed_metrics

    return observed_metrics(progress, version, since=since)


def update_evolution(progress, *, now=None):
    from deeptutor.learning.strategy_effects import update

    upgrade_protocol(progress, now=now)
    update(progress, now=now)


def monitor_evolution(progress):
    update_evolution(progress)


def set_consent(store, path_id: str, enabled: bool):
    def change(tx):
        state = tx.progress.evolution
        before = bool(state.get("consent"))
        state["consent"] = enabled
        upgrade_protocol(tx.progress)
        if enabled and not before:
            state["consent_at"] = time.time()
            state["status"] = "collecting_evidence"
            state.pop("candidate", None)
            state.pop("evaluation", None)
        if not enabled:
            state["status"] = "paused"
        tx.emit("wenjin.evolution_consent", {"enabled": enabled, "protocol": state.get("protocol")})

    return store.mutate(path_id, change)[0]


def rollback(store, path_id: str):
    def change(tx):
        state = tx.progress.evolution
        if "previous" not in state:
            raise ValueError("尚无已启用的候选策略可恢复")
        state["active"] = state.pop("previous")
        state["status"] = "rolled_back"
        if state.get("candidate"):
            state["candidate"]["status"] = "rolled_back"
        state.setdefault("history", []).append({"event": "learner_rollback", "at": time.time()})
        tx.emit("wenjin.policy_rolled_back", {"active": state["active"]})

    return store.mutate(path_id, change)[0]


def revalidate_after_repair(progress):
    from deeptutor.learning.strategy_effects import update

    upgrade_protocol(progress)
    update(progress, repair=True)
