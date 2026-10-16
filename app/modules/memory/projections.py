"""确定性、可再生的 L2 摘要与 L3 观察；没有掌握度或素养分数。"""
from collections import defaultdict

from sqlalchemy import select

from app.infrastructure.db.models import FactJudgment, LearningFact, LearningProjection, MemoryNote

DIGEST = "learning_digest.v1"
MEMORY = "learning_observations.v1"


def save_projection(session, wid, name, refs, payload):
    if name == MEMORY:
        validate_memory_sources(refs)
        for claim in payload["claims"]:
            validate_memory_sources(claim["source_refs"])
    row = session.get(LearningProjection, (wid, name))
    if row is None:
        row = LearningProjection(workspace_id=wid, name=name, revision=1, source_refs=refs, payload=payload)
        session.add(row)
    elif row.source_refs != refs or row.payload != payload:
        row.revision += 1
        row.source_refs, row.payload = refs, payload
    session.flush()
    return row


def rebuild(session, wid):
    session.flush()
    facts = list(session.scalars(select(LearningFact).where(LearningFact.workspace_id == wid)
        .order_by(LearningFact.created_at, LearningFact.id)))
    judgments = list(session.scalars(select(FactJudgment).join(LearningFact).where(LearningFact.workspace_id == wid)
        .order_by(FactJudgment.created_at, FactJudgment.id)))
    notes = list(session.scalars(select(MemoryNote).where(MemoryNote.workspace_id == wid)
        .order_by(MemoryNote.created_at, MemoryNote.id)))
    refs = [{"kind": "fact", "id": fact.id} for fact in facts]
    refs += [{"kind": "judgment", "id": row.id} for row in judgments]
    refs += [{"kind": "self_report", "id": row.id} for row in notes]
    groups = defaultdict(lambda: {"passed": 0, "failed": 0, "unverifiable": 0, "hinted_passed": 0})
    for fact in facts:
        if fact.kind not in {"answer", "review_answer"}:
            continue
        counts = groups[(fact.source_kind, fact.payload["stage"])]
        counts[fact.payload["status"]] += 1
        if fact.payload["status"] == "passed" and fact.payload["hints_used"]:
            counts["hinted_passed"] += 1
    rows = [{"source_kind": source, "stage": stage, **counts} for (source, stage), counts in sorted(groups.items())]
    digest = save_projection(session, wid, DIGEST, refs, {"rows": rows,
        "hint_requests": sum(fact.kind == "hint" for fact in facts),
        "reflections": sum(fact.kind == "reflection" for fact in facts),
        "self_reports": len(notes), "manual_judgments": len(judgments)})
    # 受控学习引擎 v1：只给观察，不套用未经校准的 BKT 参数。
    claims = []
    for source, stage in sorted(groups):
        matching = [fact for fact in facts if fact.source_kind == source and fact.payload["stage"] == stage
                    and fact.kind in {"answer", "review_answer"}]
        counts = groups[(source, stage)]
        labels = {"diagnostic": "诊断", "practice": "练习", "checkpoint": "独立检测", "review": "原题无提示复做"}
        claims.append({"source_kind": source, "text": f"{labels[stage]}记录：数值通过 {counts['passed']} 次，数值未通过 {counts['failed']} 次，不可验证 {counts['unverifiable']} 次。",
            "source_refs": [{"kind": "fact", "id": fact.id} for fact in matching]})
    for kind, label in [("roundtable", "圆桌讨论"), ("teach", "我教 AI")]:
        contributions = [fact for fact in facts if fact.kind == "activity_contribution" and fact.payload["stage"] == kind]
        for source in sorted({fact.source_kind for fact in contributions}):
            matching = [fact for fact in contributions if fact.source_kind == source]
            claims.append({"source_kind": source, "text": f"{label}留下 {len(matching)} 次学生贡献；角色模板不作为证据，解释质量尚未评定。",
                           "source_refs": [{"kind": "fact", "id": fact.id} for fact in matching]})
    for row in judgments:
        claims.append({"source_kind": "human_judgment", "text": "人工复核：" + {"correct": "数值正确", "incorrect": "数值错误", "unresolved": "仍无法判断"}[row.conclusion] + "。" + row.note,
                       "source_refs": [{"kind": "fact", "id": row.fact_id}, {"kind": "judgment", "id": row.id}]})
    for note in notes:
        claims.append({"source_kind": "student_self_report", "text": note.text,
                       "source_refs": [{"kind": "fact", "id": note.fact_id}, {"kind": "self_report", "id": note.id}]})
    memory_refs = [{"kind": "digest", "name": DIGEST, "revision": digest.revision}]
    save_projection(session, wid, MEMORY, memory_refs, {"engine": "observations.v1", "claims": claims,
        "limitation": "仅描述记录，不推断误区成因、推理质量、掌握度或素养；学生说明与人工判断分开标注。"})


def validate_memory_sources(refs):
    if any(ref.get("kind") not in {"fact", "digest", "judgment", "self_report"}
           or (ref.get("kind") == "digest" and ref.get("name") != DIGEST) for ref in refs):
        raise ValueError("L3 只能引用 L1 或 L2，不能引用其他 L3 结论")
