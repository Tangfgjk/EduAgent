"""Project view boundaries over a learner-wide longitudinal model."""
from __future__ import annotations

import json


class ProjectScopeError(ValueError):
    pass


def require_project(store, learner_id: str, project_id: str | None, *, writable: bool = False,
                    required: bool = False) -> None:
    if project_id is None:
        if required:
            raise ProjectScopeError("A saved learning project is required")
        return
    if not project_id or not project_id.startswith("project:"):
        raise ProjectScopeError("A saved learning project is required")
    with store.lock:
        table = store.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_records'"
        ).fetchone()
        if table is None:
            raise ProjectScopeError("Unknown learning project")
        row = store.conn.execute(
            "SELECT learner_id,kind,payload FROM workspace_records "
            "WHERE record_id=? ORDER BY revision DESC LIMIT 1", (project_id,)
        ).fetchone()
        if row is None or row["learner_id"] != learner_id or row["kind"] != "project":
            raise ProjectScopeError("Unknown or foreign learning project")
        if writable and json.loads(row["payload"])["content"].get("archived"):
            raise ProjectScopeError("Archived learning project is read-only")


def project_evidence(evidences, project_id: str | None):
    if project_id is None:
        return evidences
    return [item for item in evidences if item.project_id == project_id]
