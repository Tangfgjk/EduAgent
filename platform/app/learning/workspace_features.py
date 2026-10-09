"""Project context, competency radar and planning harness projections.

These are deliberately small, local-first contracts.  They keep temporary
conversations out of the learner's formal project/evidence graph and keep
generated Skill.md files in a reviewable, versioned candidate table.
"""
from __future__ import annotations

import json
from uuid import uuid4

from app.core.schema import utcnow

DIMENSIONS = (
    ("planning", "自主规划"),
    ("monitoring", "自我监控"),
    ("reasoning", "证据推理"),
    ("problem_solving", "问题解决"),
    ("collaboration", "合作交流"),
    ("reflection", "反思迁移"),
)


def install_feature_tables(store) -> None:
    with store.lock:
        store.conn.execute("""CREATE TABLE IF NOT EXISTS workspace_contexts(
            context_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL,
            context_type TEXT NOT NULL, title TEXT NOT NULL,
            project_id TEXT, formal INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, closed_at TEXT)""")
        store.conn.execute("""CREATE TABLE IF NOT EXISTS planner_harness_versions(
            version_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL,
            project_id TEXT, status TEXT NOT NULL, inputs TEXT NOT NULL,
            skill_md TEXT NOT NULL, created_at TEXT NOT NULL,
            reviewed_at TEXT, prior_version_id TEXT)""")
        store.conn.execute("""CREATE TABLE IF NOT EXISTS temporary_messages(
            context_id TEXT NOT NULL, message_id TEXT NOT NULL, learner_id TEXT NOT NULL,
            payload TEXT NOT NULL, PRIMARY KEY(context_id,message_id))""")
        store.conn.commit()


def contexts(store, learner_id: str) -> list[dict]:
    rows = store.conn.execute(
        "SELECT * FROM workspace_contexts WHERE learner_id=? ORDER BY created_at DESC", (learner_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def create_context(store, learner_id: str, context_type: str, title: str, project_id: str | None = None) -> dict:
    if context_type not in {"project", "temporary"}:
        raise ValueError("context_type must be project or temporary")
    if not title.strip():
        raise ValueError("context title is required")
    value = dict(context_id="ctx:" + uuid4().hex, learner_id=learner_id,
                 context_type=context_type, title=title.strip(), project_id=project_id,
                 formal=int(context_type == "project"), created_at=utcnow().isoformat(), closed_at=None)
    store.conn.execute("INSERT INTO workspace_contexts VALUES (?,?,?,?,?,?,?,?)", tuple(value.values()))
    store.conn.commit()
    return value


def close_context(store, learner_id: str, context_id: str) -> dict:
    row = store.conn.execute("SELECT * FROM workspace_contexts WHERE context_id=? AND learner_id=?", (context_id, learner_id)).fetchone()
    if row is None:
        raise KeyError("Unknown workspace context")
    closed = utcnow().isoformat()
    store.conn.execute("UPDATE workspace_contexts SET closed_at=? WHERE context_id=? AND learner_id=?", (closed, context_id, learner_id))
    store.conn.commit()
    value = dict(row)
    value["closed_at"] = closed
    return value


def competency_radar(store, learner_id: str, project_evidence: set[str] | None = None) -> dict:
    """Return six descriptive dimensions, leaving unknown values empty.

    Snapshot competency values win.  With no explicit competency assessment,
    this endpoint reports ``None`` rather than inventing a personal score.
    """
    rows = store.conn.execute("SELECT payload FROM snapshots WHERE learner_id=? ORDER BY ts DESC LIMIT 1", (learner_id,)).fetchall()
    values = {}
    provenance = "no_competency_assessment"
    if rows:
        payload = json.loads(rows[0][0])
        for item in payload.get("competency_state", []):
            key = str(item.get("competency_id", "")).lower()
            for slug, _ in DIMENSIONS:
                if slug in key:
                    item_refs = set(item.get("evidence_refs", []))
                    if project_evidence is not None and (not item_refs or not item_refs <= project_evidence):
                        continue
                    values[slug] = {"value": round((int(item.get("level", 1)) - 1) / 4, 3),
                                    "level": item.get("level"), "evidence_refs": item.get("evidence_refs", [])}
                    provenance = "latest_mental_state_snapshot"
    return {"dimensions": [dict(id=slug, label=label, **values.get(slug, {"value": None, "level": None, "evidence_refs": []})) for slug, label in DIMENSIONS],
            "provenance": provenance, "project_evidence_count": len(project_evidence or set()),
            "note": "素养维度只展示已有评估；空值表示证据不足，不是零分。"}


def _skill_md(inputs: dict, version_id: str) -> str:
    subjective = inputs.get("subjective_feedback") or "暂无主观反馈"
    objective = inputs.get("objective_evidence_refs") or []
    materials = inputs.get("materials") or []
    standards = inputs.get("standards") or []
    return "\n".join([
        "# Project Learning Skill (candidate)", "", f"- version: `{version_id}`", "- status: candidate",
        "- approval: requires learner/teacher review", "", "## Inputs",
        f"- Subjective feedback: {subjective}",
        f"- Objective evidence refs: {', '.join(objective) if objective else 'none'}",
        f"- Materials: {', '.join(materials) if materials else 'none'}",
        f"- Standards / exam outline: {', '.join(standards) if standards else 'none'}", "",
        "## Guardrails", "- Use project evidence only when a project scope is active.",
        "- Treat this file as a candidate harness; never auto-promote or claim learning effects.",
    ])


def create_harness(store, learner_id: str, project_id: str | None, inputs: dict) -> dict:
    version_id = "skill:" + uuid4().hex
    prior = store.conn.execute("SELECT version_id FROM planner_harness_versions WHERE learner_id=? AND project_id IS ? ORDER BY created_at DESC LIMIT 1", (learner_id, project_id)).fetchone()
    value = dict(version_id=version_id, learner_id=learner_id, project_id=project_id,
                 status="candidate", inputs=inputs, skill_md=_skill_md(inputs, version_id),
                 created_at=utcnow().isoformat(), reviewed_at=None,
                 prior_version_id=prior[0] if prior else None)
    store.conn.execute("INSERT INTO planner_harness_versions VALUES (?,?,?,?,?,?,?,?,?)", (version_id, learner_id, project_id, value["status"], json.dumps(inputs, ensure_ascii=False), value["skill_md"], value["created_at"], None, value["prior_version_id"]))
    store.conn.commit()
    return value


def harnesses(store, learner_id: str, project_id: str | None = None) -> list[dict]:
    rows = store.conn.execute("SELECT * FROM planner_harness_versions WHERE learner_id=? AND project_id IS ? ORDER BY created_at DESC", (learner_id, project_id)).fetchall()
    return [dict(version_id=r[0], learner_id=r[1], project_id=r[2], status=r[3], inputs=json.loads(r[4]), skill_md=r[5], created_at=r[6], reviewed_at=r[7], prior_version_id=r[8]) for r in rows]


def review_harness(store, learner_id: str, version_id: str, status: str) -> dict:
    if status not in {"approved", "rejected", "rolled_back"}:
        raise ValueError("invalid harness review status")
    row = store.conn.execute("SELECT * FROM planner_harness_versions WHERE version_id=? AND learner_id=?", (version_id, learner_id)).fetchone()
    if row is None:
        raise KeyError("Unknown harness version")
    if status == "rolled_back" and row[3] != "approved":
        raise ValueError("Only an approved version can be rolled back")
    if status in {"approved", "rejected"} and row[3] != "candidate":
        raise ValueError("Only a candidate can be reviewed")
    reviewed = utcnow().isoformat()
    store.conn.execute("UPDATE planner_harness_versions SET status=?, reviewed_at=? WHERE version_id=? AND learner_id=?", (status, reviewed, version_id, learner_id))
    store.conn.commit()
    value = dict(version_id=row[0], learner_id=row[1], project_id=row[2], status=status, inputs=json.loads(row[4]), skill_md=row[5], created_at=row[6], reviewed_at=reviewed, prior_version_id=row[8])
    return value
