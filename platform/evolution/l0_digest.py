"""L0 授权证据汇总：只消费采集和当前均允许 evolution 的学习证据。

按学习者和 KC 描述验证记录；不通过旧原始事件聚合绕过用途授权。
运行：uv run python -m evolution.l0_digest [db_path]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.schema import new_id, utcnow  # noqa: E402
from app.storage.db import Store  # noqa: E402


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else "./data/rsi.sqlite3"
    store = Store(db_path)
    from app.learning.service import LearningService
    service = LearningService(store)
    learners = store.conn.execute("SELECT DISTINCT learner_id FROM consent_records").fetchall()
    digests = []
    for learner in learners:
        try:
            digests.append(build_learning_digest(service,learner[0]))
        except PermissionError:
            continue
    if not digests:
        raise PermissionError("No learner authorized evolution; no data was consumed")
    digest = {"learners":digests}
    store.save_digest(digest)
    print(json.dumps(digest, ensure_ascii=False, indent=2))


def build_learning_digest(service, learner_id):
    """L0 reads only collection/current evolution-authorized evidence."""
    evidence = service.evidences(learner_id,purpose="evolution")
    replaced = {e.supersedes for e in evidence if e.supersedes}
    buckets = defaultdict(lambda:{"attempts":0,"passed":0,"unverifiable":0})
    for e in evidence:
        if e.evidence_id in replaced:
            continue
        for kc in e.kc_refs:
            bucket = buckets[kc]
            if e.verdict_status == "unverifiable":
                bucket["unverifiable"] += 1
            else:
                bucket["attempts"] += 1
                bucket["passed"] += int(e.verdict_status=="passed")
    return dict(digest_id=new_id(),learner_id=learner_id,created_at=utcnow().isoformat(),purpose="evolution",by_kc=[dict(kc_id=k,**v) for k,v in sorted(buckets.items())],kpis=dict(total_attempts=sum(v["attempts"] for v in buckets.values()),total_unverifiable=sum(v["unverifiable"] for v in buckets.values())),suggestions=["当前只生成授权证据的描述性汇总，自动RSI未启动"],evidence_refs=[e.evidence_id for e in evidence if e.evidence_id not in replaced])


if __name__ == "__main__":
    main()
