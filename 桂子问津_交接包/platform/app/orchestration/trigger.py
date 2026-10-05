"""docs/03 §3.1：介入门 TriggerEngine —— 只回答"何时值得看一眼"，不回答"做什么动作"。

冷却期与置信度阈值为软参数；介入提案被教师/学生否决时由上层记录回流（金标）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.schema import MentalStateSnapshot

THETA_HIGH = 0.75


@dataclass
class TriggerProposal:
    rule_id: str
    confidence: float
    note: str


@dataclass
class TriggerEngine:
    cooldowns: dict = field(default_factory=lambda: {
        "TR_FRUSTRATION": 120,  # 秒
        "TR_STUCK": 60,
    })
    min_confidence: dict = field(default_factory=lambda: {
        "TR_FRUSTRATION": 0.7,
        "TR_STUCK": 0.8,
    })
    _last_fired: dict = field(default_factory=dict)

    def evaluate(self, snapshot: MentalStateSnapshot, wrong_streak: int,
                 now: datetime | None = None) -> list[TriggerProposal]:
        now = now or datetime.now()
        proposals: list[TriggerProposal] = []
        candidates = [
            ("TR_FRUSTRATION", snapshot.affect_motivation.frustration >= THETA_HIGH,
             min(1.0, snapshot.affect_motivation.frustration), "挫败超阈，需要支持性反馈"),
            ("TR_STUCK", wrong_streak >= 2, min(1.0, wrong_streak / 2),
             "同一 KC 连续答错，值得介入"),
        ]
        for rule_id, fired, confidence, note in candidates:
            if not fired or confidence < self.min_confidence[rule_id]:
                continue
            last = self._last_fired.get(rule_id)
            if last and now - last < timedelta(seconds=self.cooldowns[rule_id]):
                continue
            self._last_fired[rule_id] = now
            proposals.append(TriggerProposal(rule_id, confidence, note))
        return proposals
