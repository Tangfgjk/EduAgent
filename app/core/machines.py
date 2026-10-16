"""docs/03 §4：三套教学法状态机（提示阶梯 / 5E 会话）。

渐撤规则：新会话起始级别 = g(autonomy_index)——自主性越高，起点越低（干预越少）；
提示预算 f(autonomy_index) 单调递减（R-07 脚手架预算递减）。单调性有单测锁定。
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

# ---------- 提示阶梯（Hint Ladder） ----------

HINT_FORMS = {0: "nudge", 1: "directive", 2: "worked_partial", 3: "worked_full"}
HINT_LABELS = {0: "L0 轻推", 1: "L1 定向提示", 2: "L2 部分范例", 3: "L3 完整讲解"}


def start_level(autonomy_index: float) -> int:
    """起始级别 = g(autonomy)：单调递减（自主性高 → 起点干预最少）。"""
    if autonomy_index < 0.2:
        return 2
    if autonomy_index < 0.5:
        return 1
    return 0


def hint_budget(autonomy_index: float) -> int:
    """会话内主动提示预算 = f(autonomy)：单调递减，下限 1（R-07）。"""
    return max(1, round(6 * (1 - autonomy_index)))


class HintLadder:
    def __init__(self, autonomy_index: float = 0.4):
        self.pos = start_level(autonomy_index)

    def advance(self) -> int:
        """仍卡壳/再次出错 → 升一级；已在顶端则保持（R-01 门控在规则引擎）。"""
        self.pos = min(3, self.pos + 1)
        return self.pos

    def exhausted(self) -> bool:
        return self.pos >= 3

    @property
    def form(self) -> str:
        return HINT_FORMS[self.pos]

    @property
    def label(self) -> str:
        return HINT_LABELS[self.pos]


# ---------- 5E 会话主流程 ----------

class Phase5E(str, Enum):
    ENGAGE = "Engage"
    EXPLORE = "Explore"
    EXPLAIN = "Explain"
    ELABORATE = "Elaborate"
    EVALUATE = "Evaluate"


_TRANSITIONS: dict[Phase5E, set[Phase5E]] = {
    Phase5E.ENGAGE: {Phase5E.EXPLORE},
    Phase5E.EXPLORE: {Phase5E.EXPLAIN},
    Phase5E.EXPLAIN: {Phase5E.EXPLORE, Phase5E.ELABORATE},
    Phase5E.ELABORATE: {Phase5E.EVALUATE},
    Phase5E.EVALUATE: {Phase5E.ENGAGE},  # 契约修订（双环学习）
}


class Session5E(BaseModel):
    phase: Phase5E = Phase5E.ENGAGE

    def can(self, target: Phase5E) -> bool:
        return target in _TRANSITIONS[self.phase]

    def transition(self, target: Phase5E) -> Phase5E:
        if not self.can(target):
            raise ValueError(f"5E 非法转移: {self.phase} → {target}")
        self.phase = target
        return self.phase

    @property
    def pos(self) -> dict:
        return {"hint_ladder": "", "session_5e": self.phase.value}
