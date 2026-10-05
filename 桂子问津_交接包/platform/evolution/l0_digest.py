"""L0 内容外环最小演示（docs/04 §7 的 Stage1-3 极简版）。

夜间批处理雏形：读事件流 + 验证结果 → 过滤 unverifiable → 按 KC 聚合正确率与
高频误区 → 产出沉淀建议 digest（真实知识库回填留给 M1）。
运行：uv run python -m evolution.l0_digest [db_path]
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.schema import QuestionItem, new_id, utcnow  # noqa: E402
from app.orchestration.session import load_bank  # noqa: E402
from app.storage.db import Store  # noqa: E402


def build_digest(store: Store, bank: list[QuestionItem] | None = None) -> dict:
    bank = bank or load_bank()
    kc_by_item = {q.item_id: q for q in bank}

    by_kc: dict[str, dict] = defaultdict(lambda: {"attempts": 0, "passed": 0, "unverifiable": 0})
    misconception_counter: Counter = Counter()
    denial_counter: Counter = Counter()

    for v in store.raw_verdict_payloads():
        item = kc_by_item.get(v.get("item_id") or "")
        if item is None:
            continue
        bucket = by_kc[item.kc_id]
        if v["status"] == "unverifiable":
            bucket["unverifiable"] += 1
            continue                      # 宁缺勿脏：不可验证样本不入统计（docs/04 §2）
        bucket["attempts"] += 1
        bucket["passed"] += 1 if v["status"] == "passed" else 0
        for mc in v.get("misconception_hits", []):
            misconception_counter[mc] += 1

    for e in store.raw_event_payloads():
        payload = e.get("payload") or {}
        if payload.get("audit") and payload.get("denial"):
            denial_counter[payload["denial"].get("rule", "?")] += 1

    kc_stats = []
    for kc_id, b in sorted(by_kc.items()):
        rate = round(b["passed"] / b["attempts"], 3) if b["attempts"] else None
        kc_stats.append({"kc_id": kc_id, "attempts": b["attempts"],
                         "pass_rate": rate, "unverifiable": b["unverifiable"]})

    suggestions: list[str] = []
    for stat in kc_stats:
        if stat["pass_rate"] is not None and stat["pass_rate"] < 0.6:
            suggestions.append(
                f"『{stat['kc_id']}』正确率仅 {stat['pass_rate']:.0%}：沉淀 3 道带阶梯提示的对比练习进知识库")
    for mc_id, count in misconception_counter.most_common(2):
        suggestions.append(f"误区『{mc_id}』高频出现 {count} 次：生成 1 组对照例题 + 1 张概念图")
    for rule_id, count in denial_counter.most_common(2):
        suggestions.append(f"硬约束『{rule_id}』拦截 {count} 次：复核策略话术，把拦截转化为引导")
    if not suggestions:
        suggestions.append("当前数据量不足以形成沉淀建议（L0 需要更多真实交互）")

    return {
        "digest_id": new_id(),
        "created_at": utcnow().isoformat(),
        "stage_note": "L0 内容外环演示：过滤→聚合→沉淀建议（真实回填在 M1 接入）",
        "hard_constraint_hash_note": "本作业不触碰软参数之外的任何配置（docs/01 P3）",
        "kpis": {"total_attempts": sum(b["attempts"] for b in by_kc.values()),
                 "total_unverifiable": sum(b["unverifiable"] for b in by_kc.values())},
        "by_kc": kc_stats,
        "misconception_top": misconception_counter.most_common(5),
        "denials": denial_counter.most_common(5),
        "suggestions": suggestions,
    }


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else "./data/rsi.sqlite3"
    store = Store(db_path)
    digest = build_digest(store)
    store.save_digest(digest)
    print(json.dumps(digest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
