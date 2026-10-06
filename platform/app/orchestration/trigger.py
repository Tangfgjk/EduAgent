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
    state_version: int | None = None
    evidence_refs: list[str] = field(default_factory=list)
    signal_version: str | None = None


@dataclass
class TriggerEngine:
    cooldowns: dict = field(default_factory=lambda: {
        "TR_FRUSTRATION": 120,  # 秒
        "TR_STUCK": 60,
        "TR_REVIEW_DUE": 120,
        "TR_UNKNOWN_EVIDENCE": 120,
    })
    min_confidence: dict = field(default_factory=lambda: {
        "TR_FRUSTRATION": 0.7,
        "TR_STUCK": 0.8,
    })
    _last_fired: dict = field(default_factory=dict)

    def evaluate(self, snapshot: MentalStateSnapshot, wrong_streak: int,
                 now: datetime | None = None, learning_signals: dict | None = None) -> list[TriggerProposal]:
        now = now or datetime.now()
        proposals: list[TriggerProposal] = []
        candidates = [
            ("TR_FRUSTRATION", snapshot.affect_motivation.frustration >= THETA_HIGH,
             min(1.0, snapshot.affect_motivation.frustration), "挫败超阈，需要支持性反馈"),
            ("TR_STUCK", wrong_streak >= 2, min(1.0, wrong_streak / 2),
             "同一 KC 连续答错，值得介入"),
        ]
        # Learning state is an optional, explicit input. Missing signals mean
        # no learning trigger; the engine never invents evidence or clock data.
        signals = learning_signals or {}
        due = signals.get("review_due", signals.get("due_review", False))
        unknown = signals.get("unknown_evidence", False)
        provenance = signals.get("evidence_refs") or []
        state_version = signals.get("state_version")
        signal_version = signals.get("version") or signals.get("signal_version")
        if due:
            candidates.append(("TR_REVIEW_DUE", True, 1.0,
                               "复习任务已到期，需要一次无辅助回忆"))
        if unknown:
            candidates.append(("TR_UNKNOWN_EVIDENCE", True, 0.8,
                               "当前知识点证据不足，需要先诊断"))
        for rule_id, fired, confidence, note in candidates:
            threshold = self.min_confidence.get(rule_id, 0.0)
            if not fired or confidence < threshold:
                continue
            last = self._last_fired.get(rule_id)
            if last and now - last < timedelta(seconds=self.cooldowns.get(rule_id, 120)):
                continue
            self._last_fired[rule_id] = now
            learning = rule_id in {"TR_REVIEW_DUE", "TR_UNKNOWN_EVIDENCE"}
            proposals.append(TriggerProposal(rule_id, confidence, note,
                                              state_version=state_version if learning else None,
                                              evidence_refs=list(provenance) if learning else [],
                                              signal_version=signal_version if learning else None))
        return proposals
