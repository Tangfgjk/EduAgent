"""Owner/workspace-scoped evidence catalogue and immutable training snapshots."""

from collections import Counter
import json
import time
import uuid

from deeptutor.learning.bkt import diagnostic_estimate
from deeptutor.learning.diagnosis import _digest, project_facts

RULE_VERSION = "independent-binary-v2-provenance"


def catalogue(store, path_id):
    for _ in range(3):
        p = store.load(path_id)
        if p is None:
            raise LookupError("Learning project not found")
        events = store.list_events(path_id)
        facts = project_facts(p, events)
        points = {k.id: k for m in p.modules for k in m.knowledge_points}
        eligible_refs = {
            ref
            for k in points.values()
            for s in diagnostic_estimate(p, k)["streams"]
            for ref in s["source_refs"]
        }
        help_requests = {
            e.payload.get("question_id")
            for e in events
            if e.event_type == "scaffold.requested" and e.payload.get("question_id")
        }
        rows = []
        synthetic = p.data_origin == "synthetic"

        for o in facts.observations:
            reasons = []
            if not any(ref in eligible_refs for ref in o.source_refs):
                reasons.append("不满足独立二元作答及任务快照规则")
            if not o.session_id or not o.turn_id:
                reasons.append("缺少对话来源")
            source_receipts = [
                p.learning_evidence[int(ref.split(":")[1])]
                for ref in o.source_refs
                if ref.startswith("learning_evidence:")
            ]
            unknown_origin = not source_receipts or any(
                e.data_origin == "legacy_unknown" for e in source_receipts
            )
            if synthetic or any(e.data_origin == "synthetic" for e in source_receipts):
                reasons.append("模拟数据，不用于个人训练")
            if unknown_origin:
                reasons.append("历史记录来源尚未核验，不用于个人训练")
            if o.question_id in help_requests:
                reasons.append("该题曾请求帮助，需另用独立任务核验后纳入训练")
            if o.context and o.context.grading_method == "semantic":
                reasons.append("模型评分待核验")
            row = o.model_dump(mode="json")
            row.update(
                objective_name=points[o.objective_id].name
                if o.objective_id in points
                else o.objective_id,
                eligibility="excluded" if reasons else "eligible",
                data_origins=sorted({e.data_origin for e in source_receipts}),
                exclusion_reasons=reasons,
                feedback=next(
                    (
                        e.feedback
                        for e in p.learning_evidence
                        if "receipt:" + e.evidence_id == o.observation_id
                    ),
                    "",
                ),
            )
            origin = next(
                (
                    e.visual_context
                    for e in p.learning_evidence
                    if "receipt:" + e.evidence_id == o.observation_id and e.source == "classroom"
                ),
                None,
            )
            if origin:
                row["classroom_origin"] = origin
            row["events"] = [
                {"id": e.id, "type": e.event_type, "at": e.created_at, "payload": e.payload}
                for e in events
                if e.payload.get("question_id") == o.question_id and o.question_id
            ]
            rows.append(row)
        latest = store.load(path_id)
        if latest and latest.version == p.version:
            return {
                "project_id": path_id,
                "name": p.name,
                "source_revision": p.version,
                "rule_version": RULE_VERSION,
                "rows": sorted(rows, key=lambda x: (x["occurred_at"], x["observation_id"])),
                "self_reports": facts.self_reports,
                "teaching_activity": [
                    {
                        "id": s["id"],
                        "objective_name": s["objective_name"],
                        "state": s["state"],
                        "assisted": s["help_delivered"],
                        "human_turn_count": sum(m["role"] == "human" for m in s["messages"]),
                        "updated_at": s["updated_at"],
                    }
                    for s in sorted(
                        p.teach_ai_sessions.values(), key=lambda s: s["updated_at"], reverse=True
                    )[:40]
                ],
                "support_activity": [
                    {
                        "id": e.id,
                        "type": e.event_type,
                        "session_id": e.session_id,
                        "turn_id": e.turn_id,
                        "at": e.created_at,
                        "kind": e.payload.get("kind"),
                        "question_id": e.payload.get("question_id"),
                        "level": e.payload.get("level"),
                    }
                    for e in events
                    if e.event_type in {"scaffold.requested", "interaction.hint_delivered"}
                ][-100:],
                "quality": quality(rows),
                "limitations": [
                    "自由交流和自述不直接生成训练正误标签。",
                    "模型评分暂列待核验；确定性判分也不代表题目质量已获人工验证。",
                    "当前数据集限定本人、当前项目，不按知识名称跨项目合并。",
                ],
            }
    raise RuntimeError("Records changed; refresh and retry")


def quality(rows):
    included = [r for r in rows if r["eligibility"] == "eligible"]
    return {
        "total": len(rows),
        "eligible": len(included),
        "excluded": len(rows) - len(included),
        "results": dict(Counter(r["result"] for r in rows)),
        "assistance": dict(Counter(r["assistance"] for r in rows)),
        "exclusions": dict(Counter(reason for r in rows for reason in r["exclusion_reasons"])),
        "skills": dict(Counter(r["objective_name"] for r in included)),
        "sequences": len(
            {(r["objective_id"], r["ability"], r["context"]["question_type"]) for r in included}
        ),
    }


def _table(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS diagnostic_datasets (id TEXT PRIMARY KEY, path_id TEXT NOT NULL, created_at REAL NOT NULL, digest TEXT NOT NULL, body TEXT NOT NULL)"
    )


def create_snapshot(store, path_id, revision, excluded_ids=(), synthetic=False, allow_empty=False):
    data = catalogue(store, path_id)
    if revision != data["source_revision"]:
        raise RuntimeError("记录已变化，请刷新后重新创建")
    known = {r["observation_id"] for r in data["rows"]}
    if not set(excluded_ids) <= known:
        raise ValueError("Unknown observation selection")
    rows = []
    audit = []
    for row in data["rows"]:
        row = dict(row)
        if row["observation_id"] in excluded_ids or synthetic:
            row["eligibility"] = "excluded"
            row["exclusion_reasons"] = [
                *row["exclusion_reasons"],
                "用户排除" if not synthetic else "模拟数据",
            ]
        audit.append(row)
        if row["eligibility"] == "eligible":
            ctx = row["context"]
            rows.append(
                {
                    "student_id": "learner-1",
                    "sequence_id": _digest(
                        [
                            path_id,
                            row["objective_id"],
                            ctx["objective_scope"],
                            row["ability"],
                            ctx["question_type"],
                            ctx["grading_method"],
                            ctx["grading_version"],
                        ]
                    ),
                    "skill_id": _digest(
                        [row["objective_id"], ctx["objective_scope"], row["ability"]]
                    ),
                    "observation_id": row["observation_id"],
                    "question_id": row["question_id"],
                    "task_version": ctx["task_version"],
                    "correct": int(row["result"] == "correct"),
                    "timestamp": row["occurred_at"],
                    "grading_method": ctx["grading_method"],
                }
            )
    if not rows and not allow_empty:
        raise ValueError("没有可纳入的训练记录；请先完成有来源的独立任务")
    snapshot = {
        "schema_version": 1,
        "id": uuid.uuid4().hex,
        "created_at": time.time(),
        "project_id": path_id,
        "source_revision": revision,
        "rule_version": RULE_VERSION,
        "quality": quality(audit),
        "rows": rows,
        "provenance": audit,
        "self_reports": data["self_reports"],
        "split": "not_assigned",
        "status": "unvalidated",
        "limitations": data["limitations"],
    }
    snapshot["digest"] = _digest(snapshot)
    with store._connect() as conn:
        _table(conn)
        conn.execute("BEGIN IMMEDIATE")
        latest = conn.execute(
            "SELECT revision FROM mastery_paths WHERE path_id=?", (path_id,)
        ).fetchone()
        if latest is None or latest[0] != revision:
            raise RuntimeError("记录已变化，请刷新后重新创建")
        conn.execute(
            "INSERT INTO diagnostic_datasets VALUES (?,?,?,?,?)",
            (
                snapshot["id"],
                path_id,
                snapshot["created_at"],
                snapshot["digest"],
                json.dumps(snapshot, ensure_ascii=False),
            ),
        )
        conn.execute("COMMIT")
    return snapshot


def snapshots(store, path_id, snapshot_id=None):
    if store.load(path_id) is None:
        raise LookupError("Learning project not found")
    with store._connect() as conn:
        _table(conn)
        if snapshot_id:
            row = conn.execute(
                "SELECT body FROM diagnostic_datasets WHERE path_id=? AND id=?",
                (path_id, snapshot_id),
            ).fetchone()
            if not row:
                raise LookupError("Dataset not found")
            return json.loads(row[0])
        return [
            {
                k: s[k]
                for k in (
                    "id",
                    "created_at",
                    "source_revision",
                    "rule_version",
                    "quality",
                    "digest",
                    "status",
                )
            }
            for row in conn.execute(
                "SELECT body FROM diagnostic_datasets WHERE path_id=? ORDER BY created_at DESC",
                (path_id,),
            )
            for s in [json.loads(row[0])]
        ]
