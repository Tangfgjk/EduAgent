"""Append-only teacher annotations for source-linked memory, not learner profiling."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Callable, Literal

from pydantic import AwareDatetime, Field, model_validator

from app.learning.course_governance import Contract
from app.learning.memory import MemoryService, source_snapshot
from app.learning.review_port import require_aware


class MemoryAnnotation(Contract):
    annotation_ref: str = Field(min_length=1, max_length=100)
    learner_ref: str = Field(min_length=1)
    view_ref: str = Field(min_length=1)
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_ref: str = Field(min_length=1)
    reviewed_at: AwareDatetime
    action: Literal["clarify", "retract"]
    source_evidence_refs: tuple[str, ...] = ()
    source_event_refs: tuple[str, ...] = ()
    note: str = Field(min_length=10, max_length=2000)
    supersedes: str | None = None
    purpose: Literal["teaching"] = "teaching"
    describes_personality: Literal[False] = False
    modifies_learning_state: Literal[False] = False

    @model_validator(mode="after")
    def bound_sources(self):
        if not self.source_evidence_refs and not self.source_event_refs:
            raise ValueError("memory annotation must cite source evidence or events")
        if self.action == "retract" and not self.supersedes:
            raise ValueError("retraction requires an earlier annotation")
        if self.supersedes == self.annotation_ref:
            raise ValueError("annotation cannot supersede itself")
        if len(self.source_evidence_refs) != len(set(self.source_evidence_refs)) or len(self.source_event_refs) != len(set(self.source_event_refs)):
            raise ValueError("duplicate memory annotation source reference")
        return self


class MemoryReviewService:
    """Internal application port. No learner-facing authority flags or endpoints.

    Human notes describe a particular observation and never replace evidence,
    event counts, mastery or retention. Source changes fail closed until rebuild.
    """
    version = "memory-annotation-v3.1"

    def __init__(self, store):
        self.store = store
        self.memory = MemoryService(store)
        with store.lock:
            store.conn.execute("CREATE TABLE IF NOT EXISTS memory_annotations(annotation_ref TEXT PRIMARY KEY,"
                               "learner_id TEXT NOT NULL,view_id TEXT NOT NULL,payload TEXT NOT NULL)")
            store.conn.commit()

    @source_snapshot
    def append(self, annotation: MemoryAnnotation, *, as_of: datetime,
               verify_reviewer: Callable[[MemoryAnnotation], bool] | None = None) -> dict:
        require_aware(as_of)
        if verify_reviewer is None or not verify_reviewer(annotation):
            raise PermissionError("memory reviewer authority unverified")
        if annotation.reviewed_at > as_of:
            raise ValueError("future memory annotation")
        view = self.memory.get(annotation.learner_ref, annotation.view_ref)
        if annotation.reviewed_at < datetime.fromisoformat(view["as_of"]):
            raise ValueError("memory annotation precedes source snapshot")
        if annotation.source_fingerprint != view["fingerprint"]:
            raise ValueError("memory source fingerprint mismatch")
        if not set(annotation.source_evidence_refs).issubset(view["l1"]["evidence_refs"]) or not set(annotation.source_event_refs).issubset(view["l1"]["event_refs"]):
            raise ValueError("memory annotation source is not in learner view")
        previous = self.store.conn.execute("SELECT payload FROM memory_annotations WHERE annotation_ref=?",
                                           (annotation.annotation_ref,)).fetchone()
        if previous:
            if MemoryAnnotation.model_validate_json(previous[0]) != annotation:
                raise ValueError("conflicting memory annotation identity")
            return dict(status="existing", annotation_ref=annotation.annotation_ref)
        if annotation.supersedes:
            row = self.store.conn.execute("SELECT payload FROM memory_annotations WHERE annotation_ref=? AND learner_id=? AND view_id=?",
                (annotation.supersedes, annotation.learner_ref, annotation.view_ref)).fetchone()
            if row is None:
                raise ValueError("unknown or cross-learner annotation revision")
            ancestor = MemoryAnnotation.model_validate_json(row[0])
            if ancestor.reviewed_at > annotation.reviewed_at:
                raise ValueError("annotation revision precedes original")
            children = self.store.conn.execute("SELECT payload FROM memory_annotations WHERE learner_id=? AND view_id=?",
                (annotation.learner_ref, annotation.view_ref)).fetchall()
            if any(MemoryAnnotation.model_validate_json(row[0]).supersedes == annotation.supersedes for row in children):
                raise ValueError("annotation already superseded")
        self.store.conn.execute("INSERT INTO memory_annotations VALUES (?,?,?,?)", (annotation.annotation_ref,
            annotation.learner_ref, annotation.view_ref, annotation.model_dump_json()))
        self.store.conn.execute("INSERT INTO learning_audit(learner_id,payload) VALUES (?,?)", (annotation.learner_ref,
            json.dumps(dict(kind="memory_annotation_appended", version=self.version,
                annotation_ref=annotation.annotation_ref, source_fingerprint=annotation.source_fingerprint,
                reviewer_ref=annotation.reviewer_ref, action=annotation.action, reviewed_at=annotation.reviewed_at.isoformat()))))
        return dict(status="appended", annotation_ref=annotation.annotation_ref)

    @source_snapshot
    def projection(self, learner_id: str, view_id: str, *, as_of: datetime) -> dict:
        require_aware(as_of)
        view = self.memory.get(learner_id, view_id)
        rows = self.store.conn.execute("SELECT payload FROM memory_annotations WHERE learner_id=? AND view_id=? ORDER BY annotation_ref",
            (learner_id, view_id)).fetchall()
        history = [MemoryAnnotation.model_validate_json(row[0]) for row in rows]
        history = [row for row in history if row.reviewed_at <= as_of]
        superseded = {row.supersedes for row in history if row.supersedes}
        current = [row for row in history if row.annotation_ref not in superseded and row.action == "clarify"]
        return dict(version=self.version, learner_ref=learner_id, view_ref=view_id,
            source_fingerprint=view["fingerprint"], annotations=[row.model_dump(mode="json") for row in current],
            history_refs=[row.annotation_ref for row in history],
            modifies_learning_state=False, describes_personality=False, source_memory=view)
