"""Versioned project organization and explicit sharing, separate from learner state."""
from __future__ import annotations

import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.schema import utcnow
from app.learning.service import EvidenceConflict, LearningService


class ProjectTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    status: Literal["todo", "doing", "done"] = "todo"
    assessment_ref: str | None = None


class ProjectContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    tasks: list[ProjectTask] = Field(default_factory=list, max_length=100)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    session_refs: list[str] = Field(default_factory=list, max_length=100)
    source_refs: list[str] = Field(default_factory=list, max_length=100)
    archived: bool = False

    @model_validator(mode="after")
    def unique_refs(self):
        for values in (self.evidence_refs, self.session_refs, self.source_refs, [t.task_id for t in self.tasks]):
            if len(values) != len(set(values)) or any(not value.strip() for value in values):
                raise ValueError("Project references and task IDs must be unique and nonempty")
        return self


class WorkspaceService:
    def __init__(self, store, catalog):
        self.store, self.catalog = store, catalog
        self.learning = LearningService(store)
        with store.lock:
            store.conn.execute("""CREATE TABLE IF NOT EXISTS workspace_records(
                record_id TEXT, revision INTEGER, learner_id TEXT NOT NULL, kind TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(record_id,revision))""")
            store.conn.commit()

    def latest(self, learner_id, kind, record_id=None):
        with self.store.lock:
            self.learning._authorize(learner_id)
            rows = self.store.conn.execute("SELECT payload FROM workspace_records WHERE learner_id=? AND kind=? ORDER BY revision,rowid", (learner_id, kind))
            values = {}
            for row in rows:
                value = json.loads(row[0])
                values[value["record_id"]] = value
            if record_id is not None:
                if record_id not in values: raise KeyError("Unknown workspace record")
                return values[record_id]
            return list(values.values())

    def _save(self, learner_id, record_id, kind, content, expected_revision):
        with self.store.lock:
            self.store.conn.execute("BEGIN IMMEDIATE")
            try:
                self.learning._authorize(learner_id)
                row = self.store.conn.execute("SELECT revision FROM workspace_records WHERE record_id=? ORDER BY revision DESC LIMIT 1", (record_id,)).fetchone()
                revision = row[0] if row else 0
                if revision != expected_revision:
                    raise EvidenceConflict("Workspace revision CAS conflict")
                if revision:
                    self.latest(learner_id, kind, record_id)
                if kind == "project": self._validate_project(learner_id, content)
                value = dict(record_id=record_id, learner_id=learner_id, revision=revision+1,
                    kind=kind, content=content, updated_at=utcnow().isoformat(),
                    consent_version=self.learning.consent(learner_id)["version"])
                self.store.conn.execute("INSERT INTO workspace_records VALUES (?,?,?,?,?)", (record_id, revision+1, learner_id, kind, json.dumps(value,ensure_ascii=False)))
                self.learning._audit(learner_id, "workspace_revision", dict(record_id=record_id, revision=revision+1, kind=kind))
                self.store.conn.commit()
                return value
            except Exception:
                self.store.conn.rollback()
                raise

    def _validate_project(self, learner_id, content):
        project = ProjectContent.model_validate(content)
        permitted = {e.evidence_id for e in self.learning.evidences(learner_id)}
        if not set(project.evidence_refs) <= permitted: raise ValueError("Unauthorized evidence reference")
        sessions = {row[0] for row in self.store.conn.execute("SELECT session_id FROM sessions WHERE learner_id=?", (learner_id,))}
        if not set(project.session_refs) <= sessions: raise ValueError("Unauthorized session reference")
        assessments = {asset.ref.key for asset in self.catalog.assessments}
        if any(t.assessment_ref and t.assessment_ref not in assessments for t in project.tasks):
            raise ValueError("Unknown assessment version")
        sources = set()
        exists = self.store.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_sources'").fetchone()
        if exists:
            # The retrieval module owns source publication and its schema.
            sources = {row[0] for row in self.store.conn.execute("SELECT source_id FROM knowledge_sources")}
        if not set(project.source_refs) <= sources: raise ValueError("Unindexed knowledge source")

    def project(self, learner_id, content, *, record_id=None, expected_revision=0):
        return self._save(learner_id, record_id or "project:"+uuid4().hex, "project", content.model_dump(mode="json"), expected_revision)

    def share(self, learner_id, audiences, expected_revision=0):
        if not set(audiences) <= {"teacher", "parent"}: raise ValueError("Unknown sharing audience")
        return self._save(learner_id, "sharing:"+learner_id, "sharing", dict(audiences=sorted(set(audiences))), expected_revision)

    def require_sharing(self, learner_id, audience):
        value = self.latest(learner_id, "sharing", "sharing:"+learner_id)
        if value["consent_version"] != self.learning.consent(learner_id)["version"] or audience not in value["content"]["audiences"]:
            raise PermissionError("Explicit current audience sharing required")
