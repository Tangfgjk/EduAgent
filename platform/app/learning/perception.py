"""LLM 感知器（docs/03 §5 step1-2）：过程数据 → 状态候选更新（宁缺勿脏）。

所有候选必须通过 02 Schema 校验才写入快照；解析/校验失败返回空候选。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.schema import MentalStateSnapshot, new_id
from app.llm.client import BaseLLM, LLMError
from app.llm.prompts import perception_messages


class PerceptionCandidate(BaseModel):
    """感知器输出的结构化候选（docs/09：动作=载体×语义，含解读估计）。"""

    answer_correctness: Literal["correct", "incorrect", "unknown"] = "unknown"
    misconception_hits: list[str] = Field(default_factory=list)
    frustration_delta: float = Field(default=0.0, ge=-0.5, le=0.5)
    engagement_delta: float = Field(default=0.0, ge=-0.5, le=0.5)
    attention_focus: str | None = None
    current_intention: (
        Literal["want_answer", "try_again", "ask_help", "reflect", "other"] | None
    ) = None
    interpreted: Literal[
        "encourage", "pressure", "condescending", "neutral", "unclear"
    ] = "neutral"
    confidence: float = Field(default=0.0, ge=0, le=1)


EMPTY_CANDIDATE = PerceptionCandidate()


def perceive(llm: BaseLLM, snapshot: MentalStateSnapshot, item: dict | None,
             student_text: str, answer: str | None,
             misconception_spaces: list[dict]) -> PerceptionCandidate:
    summary = {
        "当前掌握": {m.kc_id: m.p_mastery for m in snapshot.knowledge_state.kc_masteries},
        "挫败": snapshot.affect_motivation.frustration,
        "自主性": snapshot.autonomy_index.composite,
        "当前题目作答状态": "已提交" if answer else "未提交",
    }
    try:
        return llm.complete_json(
            perception_messages(summary, item, student_text, answer, misconception_spaces),
            PerceptionCandidate,
        )
    except LLMError:
        return EMPTY_CANDIDATE


def apply_candidate(snapshot: MentalStateSnapshot, cand: PerceptionCandidate,
                    action_ref: str | None = None) -> None:
    """把候选写入快照（原地，幂等安全：全部为有界增量/替换）。"""
    affect = snapshot.affect_motivation
    affect.frustration = round(min(1.0, max(0.0, affect.frustration + cand.frustration_delta)), 4)
    affect.engagement = round(min(1.0, max(0.0, affect.engagement + cand.engagement_delta)), 4)
    if cand.attention_focus:
        snapshot.in_session_state.attention_focus = cand.attention_focus
    if cand.current_intention:
        snapshot.in_session_state.current_intention = cand.current_intention
    if action_ref and cand.interpreted != "unclear":
        from app.core.schema import LastActionInterpretation

        snapshot.in_session_state.last_action_interpretation = LastActionInterpretation(
            action_ref=action_ref, perceived=cand.interpreted,
            confidence=cand.confidence,
        )
    snapshot.provenance.models = sorted(set(snapshot.provenance.models) | {"perception.llm.v1"})


def apply_misconception_hits(snapshot: MentalStateSnapshot, space_id: str,
                             hit_ids: list[str], lift: float = 1.6) -> None:
    """误区后验的朴素更新：命中的假设按 lift 提升后归一化（贝叶斯的极简近似）。"""
    space = snapshot.misconception_space(space_id)
    if space is None or not hit_ids:
        return
    for hyp in space.posterior:
        if hyp.hypothesis_id in hit_ids:
            hyp.p = min(1.0, hyp.p * lift)
    total = sum(h.p for h in space.posterior) or 1.0
    for hyp in space.posterior:
        hyp.p = round(hyp.p / total, 4)


def seed_misconception_space(snapshot: MentalStateSnapshot, space_id: str,
                             hypotheses: dict[str, str]) -> None:
    """为快照播种一个误区假设空间（均匀先验），题库驱动的误区诊断起点。"""
    if snapshot.misconception_space(space_id) is not None:
        return
    from app.core.schema import MisconceptionHypothesis, MisconceptionSpace

    n = max(1, len(hypotheses))
    uniform = round(1.0 / n, 4)
    space = MisconceptionSpace(
        space_id=space_id,
        posterior=[
            MisconceptionHypothesis(hypothesis_id=hid, p=uniform, description=desc)
            for hid, desc in hypotheses.items()
        ],
    )
    snapshot.misconception_hypotheses.append(space)


_ = new_id  # 保留导入占位（未来事件引用）
