"""docs/03 §1-2：动作信封与动作类型系统。"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.schema import _Forbidden, new_id


class ActionType(str, Enum):
    GOAL_NEGOTIATE = "GOAL_NEGOTIATE"
    ENGAGE = "ENGAGE"
    QUESTION = "QUESTION"
    HINT = "HINT"
    EXPLAIN = "EXPLAIN"
    TASK = "TASK"
    FEEDBACK = "FEEDBACK"
    REFLECTION = "REFLECTION"
    PEER_SIMULATE = "PEER_SIMULATE"
    ENV_OP = "ENV_OP"
    ESCALATE = "ESCALATE"
    WAIT = "WAIT"


class ActorKind(str, Enum):
    student = "student"
    companion = "companion"
    derived_agent = "derived_agent"


class ScaffoldType(str, Enum):
    """支架功能轴（docs/11 §4 / 调研报告§四），与提示阶梯 L0-L3 强度轴正交。

    选择逻辑进 POMDP/策略层，门控在 ActionGovernor；participation 为小学段
    增补型（调研报告案例 C：参与维持）。"""

    conceptual = "conceptual"
    procedural = "procedural"
    strategic = "strategic"
    metacognitive = "metacognitive"
    epistemic = "epistemic"
    collaborative = "collaborative"
    participation = "participation"


class Semantic(_Forbidden):
    """动作 = 载体 × 心智语义（docs/01 §4.6 MWM 对接）：意图语义在此显式声明，
    心智转移使用"被学生解读的语义"（InSessionState.last_action_interpretation）。"""

    intent: str = "support"
    social_meaning: str = "neutral"


class ActionEnvelope(_Forbidden):
    action_id: str = Field(default_factory=new_id)
    session_id: str = ""
    actor_kind: ActorKind = ActorKind.companion
    actor_ref: str = "companion_v1"
    template_version: str | None = None
    type: ActionType
    params: dict[str, Any] = Field(default_factory=dict)
    semantic: Semantic = Field(default_factory=Semantic)
    state_refs: dict[str, Any] = Field(default_factory=dict)
    policy_provenance: dict[str, Any] = Field(
        default_factory=lambda: {"generated_by": "policy_v1", "grounded_to_kg": []}
    )
    budget: dict[str, int] = Field(default_factory=lambda: {"max_tokens": 500, "deadline_ms": 3000})

    model_config = ConfigDict(extra="forbid")

    # ---- 便捷构造器（对应 docs/03 §2 类型系统） ----

    @classmethod
    def question(cls, session_id: str, kc_id: str, stem: str, item_id: str = "",
                 target: str = "practice", intent: str = "diagnostic") -> "ActionEnvelope":
        return cls(
            session_id=session_id, type=ActionType.QUESTION,
            params={"target": target, "item_id": item_id, "stem": stem, "intent": intent},
            semantic=Semantic(intent=intent, social_meaning="neutral"),
            policy_provenance={"generated_by": "policy_v1", "grounded_to_kg": [kc_id]},
        )

    @classmethod
    def hint(cls, session_id: str, kc_id: str, level: int, form: str, text: str,
             proactive: bool = True,
             scaffold_type: str = ScaffoldType.strategic.value) -> "ActionEnvelope":
        return cls(
            session_id=session_id, type=ActionType.HINT,
            params={"ladder_level": level, "form": form, "text": text,
                    "target": "practice", "proactive": proactive,
                    "scaffold_type": scaffold_type},
            semantic=Semantic(intent="support", social_meaning="encourage"),
            policy_provenance={"generated_by": "policy_v1", "grounded_to_kg": [kc_id]},
        )

    @classmethod
    def explain(cls, session_id: str, kc_id: str, mode: str, text: str = "",
                target: str = "practice", rule_explanation: bool = False) -> "ActionEnvelope":
        return cls(
            session_id=session_id, type=ActionType.EXPLAIN,
            params={"mode": mode, "text": text, "target": target,
                    "rule_explanation": rule_explanation},
            semantic=Semantic(intent="explain", social_meaning="neutral"),
            policy_provenance={"generated_by": "policy_v1", "grounded_to_kg": [kc_id]},
        )

    @classmethod
    def task(cls, session_id: str, kc_id: str, stem: str, item_id: str = "",
             difficulty: float = 0.5, kind: str = "practice") -> "ActionEnvelope":
        return cls(
            session_id=session_id, type=ActionType.TASK,
            params={"kind": kind, "item_id": item_id, "stem": stem, "difficulty": difficulty},
            semantic=Semantic(intent="challenge", social_meaning="trust"),
            policy_provenance={"generated_by": "policy_v1", "grounded_to_kg": [kc_id]},
        )

    @classmethod
    def feedback(cls, session_id: str, kc_id: str, kind: str, text: str) -> "ActionEnvelope":
        return cls(
            session_id=session_id, type=ActionType.FEEDBACK,
            params={"kind": kind, "text": text, "target": "practice"},
            semantic=Semantic(intent="feedback", social_meaning="encourage"),
            policy_provenance={"generated_by": "policy_v1", "grounded_to_kg": [kc_id]},
        )
