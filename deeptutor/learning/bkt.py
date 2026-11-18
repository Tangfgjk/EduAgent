"""Classic no-forgetting BKT replay. Experimental estimates, never mastery credit.

Reference: https://github.com/CAHLR/pyBKT
Scope is project + objective contract + ability + question/grading family.
No persisted accumulator: corrections, voids and delayed events replay from facts.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import math

from deeptutor.learning.diagnosis import _digest, project_facts


@dataclass(frozen=True)
class BKTParameters:
    prior: float = 0.2
    learn: float = 0.1
    guess: float = 0.2
    slip: float = 0.1

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"Invalid BKT parameter: {name}")
        if not 0 < self.guess < 1 - self.slip < 1:
            raise ValueError("Require 0 < guess < 1-slip < 1 for identifiable emissions")


def update(known: float, correct: bool, params: BKTParameters) -> float:
    """Condition on a binary response, then apply a learning transition."""
    if not math.isfinite(known) or not 0 <= known <= 1 or type(correct) is not bool:
        raise ValueError("Expected a valid state and a binary response")
    a = (1 - params.slip) if correct else params.slip
    b = params.guess if correct else (1 - params.guess)
    posterior = known * a / (known * a + (1 - known) * b)
    return posterior + (1 - posterior) * params.learn


def diagnostic_estimate(progress, kp, params: BKTParameters | None = None, *, store=None) -> dict:
    scope = _digest(kp.model_dump(mode="json"))
    training_id = None
    if params is None and store is not None:
        from deeptutor.learning.personal_training import active_parameters
        trained=active_parameters(store,progress.book_id,kp.id,scope)
        if trained:params,training_id=trained
    params = params or BKTParameters()
    rows = [o for o in project_facts(progress).observations if o.objective_id == kp.id]
    counts = Counter(o.observation_id for o in rows)
    groups = defaultdict(list)
    excluded = Counter()
    for row in rows:
        ctx = row.context
        reason = ""
        if row.validity != "recorded":
            reason = row.validity
        elif counts[row.observation_id] > 1:
            reason = "duplicate_observation"
        elif row.assistance != "independent" or (ctx and ctx.scaffold_level):
            reason = "assisted_or_unknown"
        elif row.result not in {"correct", "incorrect"}:
            reason = "non_binary_result"
        elif row.response_status != "answered" or row.answer is None or not str(row.answer).strip():
            reason = "missing_response"
        elif (
            row.origin != "local_assessment"
            or not ctx
            or not all(
                (
                    ctx.task_prompt,
                    ctx.task_version,
                    ctx.rubric_fingerprint,
                    ctx.grading_version,
                    ctx.question_type,
                    row.question_id,
                )
            )
            or ctx.grading_method == "legacy"
        ):
            reason = "missing_task_contract"
        elif ctx.objective_scope != scope:
            reason = "missing_or_changed_objective_scope"
        elif not math.isfinite(row.occurred_at):
            reason = "invalid_timestamp"
        if reason:
            excluded[reason] += 1
        else:
            groups[
                (row.ability, ctx.question_type, ctx.grading_method, ctx.grading_version)
            ].append(row)
    streams = []
    for family, observations in sorted(groups.items()):
        ordered = sorted(observations, key=lambda o: (o.occurred_at, o.observation_id))
        # Repeated grading of one registered task must not count as independent trials.
        question_counts = Counter(o.question_id for o in ordered)
        accepted = [o for o in ordered if question_counts[o.question_id] == 1]
        excluded["repeated_task"] += len(ordered) - len(accepted)
        if not accepted:
            continue
        known = params.prior
        for row in accepted:
            known = update(known, row.result == "correct", params)
        streams.append(
            {
                "ability": family[0],
                "question_type": family[1],
                "grading_method": family[2],
                "grading_version": family[3],
                "p_known": known,
                "p_correct_next": known * (1 - params.slip) + (1 - known) * params.guess,
                "n_eligible": len(accepted),
                "last_observation_id": accepted[-1].observation_id,
                "source_refs": [ref for o in accepted for ref in o.source_refs],
                "semantic_grading_unvalidated": family[2] == "semantic",
            }
        )
    return {
        "model_family": "BKT",
        "model_version": "classic-no-forgetting-v1",
        "parameter_set_id": ("personal-" if training_id else "experimental-") + _digest(asdict(params))[:16],
        "parameter_origin": "personal_time_holdout" if training_id else "assumed_not_fitted",
        "training_run_id": training_id,
        "parameters": asdict(params),
        "calibration_status": "personal_time_holdout" if training_id else "uncalibrated",
        "mode": "shadow",
        "project_id": progress.book_id,
        "objective_id": kp.id,
        "objective_scope": scope,
        "source_revision": progress.version,
        "measurement_status": "experimental_observations" if streams else "unmeasured",
        "n_eligible": sum(s["n_eligible"] for s in streams),
        "excluded_counts": {k: v for k, v in sorted(excluded.items()) if v},
        "streams": streams,
        "summary": (
            ("已使用本人经过时间留出检验的参数重新跟踪独立作答；由导师结合目标和实际表现判断下一步。" if training_id else "已根据独立作答生成实验性跟踪，参数尚未校准；由导师结合目标和实际表现判断下一步。")
            if streams
            else "尚无符合条件的独立作答，暂不估计掌握程度；可以先交流或尝试一个小任务。"
        ),
        "limitations": [
            "Assumed parameters; no locally validated mastery or uncertainty interval.",
            "Streams are separated by ability and task/grading family, not pooled as overall mastery.",
            "Generated tasks and semantic grades are not independently validated.",
            "No calendar forgetting, cross-project transfer or external-report initialization.",
            "Never use these probabilities alone to withdraw support, skip or certify mastery.",
        ],
    }
