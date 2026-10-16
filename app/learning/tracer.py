"""BKT 知识追踪（docs/02：认知诊断 = 符号-统计状态估计器）。

能力是状态不是特质：p_mastery 随证据升降；ci95 宽度随观测数收缩。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.schema import KCMastery, MentalStateSnapshot, utcnow

DEFAULT_BKT = {"p_learn": 0.15, "p_guess": 0.2, "p_slip": 0.1}
PRIOR_MASTERY = 0.1


@dataclass
class BKTTracer:
    """会话内持有每个 KC 的观测计数，用于置信区间收缩。"""

    params: dict = field(default_factory=lambda: dict(DEFAULT_BKT))
    observations: dict[str, int] = field(default_factory=dict)

    def update(self, snapshot: MentalStateSnapshot, kc_id: str, correct: bool,
               evidence_ref: str | None = None) -> KCMastery:
        """对快照原地更新指定 KC 的掌握估计，返回该 KCMastery。"""
        mastery = snapshot.mastery_of(kc_id)
        if mastery is None:
            mastery = KCMastery(
                kc_id=kc_id, p_mastery=PRIOR_MASTERY, ci95=(0.0, 1.0),
            )
            snapshot.knowledge_state.kc_masteries.append(mastery)
        n = self.observations.get(kc_id, 0)
        p = mastery.p_mastery
        if correct:
            denom = p * (1 - self.params["p_slip"]) + (1 - p) * self.params["p_guess"]
            post = p * (1 - self.params["p_slip"]) / denom if denom > 0 else p
        else:
            denom = p * self.params["p_slip"] + (1 - p) * (1 - self.params["p_guess"])
            post = p * self.params["p_slip"] / denom if denom > 0 else p
        p_new = post + (1 - post) * self.params["p_learn"]
        n += 1
        self.observations[kc_id] = n
        mastery.p_mastery = round(min(1.0, max(0.0, p_new)), 4)
        half = min(0.25, 0.98 / n**0.5)
        mastery.ci95 = (
            round(max(0.0, mastery.p_mastery - half), 4),
            round(min(1.0, mastery.p_mastery + half), 4),
        )
        mastery.last_practiced_at = utcnow()
        if evidence_ref:
            mastery.evidence_refs.append(evidence_ref)
        return mastery
