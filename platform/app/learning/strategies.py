"""Versioned, replaceable subject strategy cards.

Cards describe prompts and checks, not learner state. A future subject package
can register a new card without changing orchestration or the evidence schema.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrategyCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy_id: str = Field(pattern=r"^[a-z][a-z0-9_-]+$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    domain: str = Field(min_length=1)
    kc_refs: tuple[str, ...] = Field(min_length=1)
    purpose: str = Field(min_length=1)
    steps: tuple[str, ...] = Field(min_length=2, max_length=8)
    checks: tuple[str, ...] = Field(min_length=1, max_length=6)
    scaffold_map: dict[int, str] = Field(default_factory=dict)
    provenance: str = Field(min_length=1)
    calibrated: bool = False


class StrategyRegistry:
    def __init__(self, cards: tuple[StrategyCard, ...] = ()):
        self._cards: dict[str, StrategyCard] = {}
        for card in cards:
            self.register(card)

    def register(self, card: StrategyCard) -> None:
        key = f"{card.strategy_id}@{card.version}"
        if key in self._cards:
            raise ValueError(f"Duplicate strategy card: {key}")
        if not card.provenance:
            raise ValueError("Strategy provenance is required")
        self._cards[key] = card

    def get(self, strategy_id: str, version: str | None = None) -> StrategyCard:
        candidates = [card for card in self._cards.values() if card.strategy_id == strategy_id]
        if version is not None:
            candidates = [card for card in candidates if card.version == version]
        if not candidates:
            raise KeyError(f"Unknown strategy: {strategy_id}@{version or '*'}")
        return sorted(candidates, key=lambda card: tuple(int(part) for part in card.version.split(".")))[-1]

    def for_kc(self, kc_id: str) -> tuple[StrategyCard, ...]:
        return tuple(sorted((card for card in self._cards.values() if kc_id in card.kc_refs),
                            key=lambda card: (card.strategy_id, card.version)))

    def manifest(self) -> list[dict]:
        return [card.model_dump(mode="json") for card in sorted(self._cards.values(), key=lambda item: item.strategy_id)]


DEFAULT_STRATEGIES = StrategyRegistry((
    StrategyCard(strategy_id="equation-balance", version="1.0.0", domain="mathematics",
        kc_refs=("MATH.G7.EQ.BALANCE", "MATH.G7.EQ.SOLVE"), purpose="保持等式两边等价并解释每步运算",
        steps=("说清未知量与两边关系", "两边执行同一种运算", "整理未知量", "代回原式验算"),
        checks=("两边是否同时操作", "是否漏掉负号或括号", "代回后左右是否相等"),
        scaffold_map={0: "先说说你准备对两边做什么", 1: "指出需要同时执行的运算", 2: "展示第一步并留下下一步", 3: "在学生作品后完整对照并要求代回"},
        provenance="local-authored-strategy-card-20261006", calibrated=False),
    StrategyCard(strategy_id="equation-model", version="1.0.0", domain="mathematics",
        kc_refs=("MATH.G7.EQ.SETUP", "MATH.G7.EQ.APPLY"), purpose="把情境量关系映射为可检查的方程",
        steps=("定义未知量", "标记已知量和单位", "用关系词写等式", "检查单位与语义", "再求解并解释"),
        checks=("未知量定义是否完整", "每个数量是否有单位", "方程是否对应题意", "结果是否回到情境"),
        scaffold_map={0: "先指出题目要求的未知量", 1: "圈出两个数量之间的关系词", 2: "写出关系的一部分并让学生补全", 3: "对照学生方程逐项检查含义"},
        provenance="local-authored-strategy-card-20261006", calibrated=False),
))
