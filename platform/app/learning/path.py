"""docs/11 §6.5：学习路径推荐 v1（规则版）。

PathRecommendation 契约的确定性实现——v1 信号：知识状态掌握度（BKT tracer）+
契约截止；v2 可整体替换为独立推荐模型（KT/CD 输出），契约与前端零改动。
铁律（docs/03 R-02）：推荐只是提案（status="proposed"），采纳/修改须经
计划签署关口，推荐引擎永远不能直接改计划。
"""
from __future__ import annotations

CONTRACT_VERSION = "PathRecommendation@1"
MASTERY_GATE = 0.9          # 掌握学习门槛（与 deeptutor.learning 对齐）


def recommend_path(snapshot, contract=None, max_nodes: int = 5) -> dict:
    """从心智状态快照（+可选契约）生成路径提案。

    v1 排序原则：薄弱优先（p_mastery 升序）；已过 0.9 门槛标记 done。
    """
    masteries = sorted(snapshot.knowledge_state.kc_masteries, key=lambda m: m.p_mastery)
    nodes: list[dict] = []
    for i, m in enumerate(masteries[:max_nodes]):
        if m.p_mastery >= MASTERY_GATE:
            status = "done"
        elif i == 0 or (nodes and nodes[-1]["status"] == "done"):
            status = "current"
        else:
            status = "recommended"
        nodes.append({
            "seq": i + 1,
            "kc_id": m.kc_id,
            "p_mastery": round(m.p_mastery, 2),
            "ci95": [round(m.ci95[0], 2), round(m.ci95[1], 2)],
            "est_sessions": max(1, min(4, int((MASTERY_GATE - m.p_mastery) * 5) + 1)),
            "difficulty": round(min(0.9, 0.4 + (1 - m.p_mastery) * 0.4), 2),
            "depends_on": [nodes[-1]["kc_id"]] if nodes else [],
            "status": status,
        })

    signals: list[dict] = [{
        "kind": "kt", "ref": "knowledge_state",
        "note": f"薄弱优先：{len(masteries)} 个知识点按 p_mastery 升序",
    }]
    deadline_note = None
    if contract is not None:
        signals.append({
            "kind": "contract", "ref": contract.goal_contract_id,
            "note": contract.goal_statement.text if hasattr(contract.goal_statement, "text")
                    else "契约目标",
        })
        for d in (contract.external_deadline_refs or []):
            deadline_note = getattr(d, "title", None) or str(d)
            break

    branch_rules = [
        {"when": "节点连续2次验证通过率<0.6",
         "then": "在该节点后插入其前置变式练习×3"},
    ]
    if deadline_note:
        branch_rules.append({
            "when": "距契约截止<3天且有节点未开始",
            "then": "未开始节点降难度至契约下限并优先排程",
        })

    return {
        "contract_version": CONTRACT_VERSION,
        "path_id": f"path_{getattr(snapshot, 'learner_id', 'unknown')}",
        "learner_id": getattr(snapshot, "learner_id", ""),
        "nodes": nodes,
        "source_signals": signals,
        "deadline": deadline_note,
        "branch_rules": branch_rules,
        "rationale": "薄弱优先：按 p_mastery 升序排程；已过 0.9 门槛的节点标记 done（掌握学习）。",
        "status": "proposed",
    }
