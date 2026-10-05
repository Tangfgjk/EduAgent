"""docs/11 §6：成长证据中心 v1（规则聚合版）。

北极星面板的数据供给层：从事件流 + 快照聚合证据卡数据。
诚实原则：样本不足的字段返回 None 并在 notes 说明，不用假数据填充；
KT/CD 模型接入后（docs/11 §6.4）替换对应卡片数据源，接口契约不变。
防古德哈特（docs/11 §6.3）：本层永不输出 autonomy_index 原始分，
只输出行为分解证据。
"""
from __future__ import annotations

CONTRACT_VERSION = "EvidenceReport@1"


def _delta(old: float | None, new: float | None) -> float | None:
    if old is None or new is None:
        return None
    return round(new - old, 3)


def evidence_report(store, learner_id: str, window: int = 50) -> dict:
    """聚合学习者的证据面板数据（v1：掌握度轨迹 + 练习对错 + 脚手架使用）。"""
    snapshots = store.snapshots_for_learner(learner_id, limit=window)
    events = store.events_for_learner(learner_id, limit=500)

    # ---- 证据① 掌握度轨迹：最早 vs 最新快照（区间来自 ci95） ----
    mastery_current: dict[str, float] | None = None
    mastery_delta: dict[str, float] | None = None
    if snapshots:
        latest, first = snapshots[-1], snapshots[0]
        new_map = {m.kc_id: m.p_mastery for m in latest.knowledge_state.kc_masteries}
        mastery_current = {k: round(v, 3) for k, v in new_map.items()}
        if len(snapshots) >= 2:
            old_map = {m.kc_id: m.p_mastery for m in first.knowledge_state.kc_masteries}
            deltas = {k: _delta(old_map.get(k), v) for k, v in new_map.items()
                      if old_map.get(k) is not None}
            mastery_delta = {k: v for k, v in deltas.items() if v is not None}

    # ---- 证据③④ 脚手架使用与练习对错（从事件流聚合） ----
    hints_proactive = hints_solicited = 0
    correct = wrong = 0
    for e in events:
        try:
            if e.observation.kind == "help_seeking":
                hints_solicited += 1
            if e.actor.kind == "companion" and \
                    (e.observation.payload or {}).get("action_type") == "HINT":
                hints_proactive += 1
            if e.observation.kind == "answer":
                flag = (e.observation.payload or {}).get("correct")
                if flag is True:
                    correct += 1
                elif flag is False:
                    wrong += 1
        except (AttributeError, TypeError):
            continue

    notes: list[str] = []
    if len(snapshots) < 2:
        notes.append("快照数<2：掌握度轨迹暂无增量，先多学几个会话")
    if correct + wrong < 5:
        notes.append("作答样本<5：正确率证据暂不呈现（避免小样本误导）")
    scaffold_ratio = None
    if hints_proactive + hints_solicited > 0:
        scaffold_ratio = round(hints_proactive / (hints_proactive + hints_solicited), 3)

    return {
        "contract_version": CONTRACT_VERSION,
        "learner_id": learner_id,
        "mastery": {
            "current": mastery_current,
            "delta": mastery_delta,
            "n_snapshots": len(snapshots),
        },
        "practice": {
            "correct": correct,
            "wrong": wrong,
            "accuracy": round(correct / (correct + wrong), 3) if correct + wrong else None,
        },
        "scaffolding": {
            "hints_proactive": hints_proactive,
            "hints_solicited": hints_solicited,
            "proactive_ratio": scaffold_ratio,
            "note": "求助量不下降≠退步：求助是策略，不是失败",
        },
        "notes": notes,
    }
