"""Versioned project organization and explicit sharing, separate from learner state."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.schema import utcnow
from app.learning.service import EvidenceConflict, LearningService


class ProjectTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1, max_length=100)
    project_id: str | None = None
    title: str = Field(min_length=1, max_length=300)
    type: Literal["manual", "practice", "assessment", "review", "reflection"] = "manual"
    status: Literal["todo", "doing", "done", "skipped"] = "todo"
    due_at: datetime | None = None
    assessment_ref: str | None = None
    milestone_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    completion_rule: Literal["manual", "verified_submission", "assessment_finalized", "teacher_review"] = "manual"
    created_from: Literal["learner", "plan", "system"] = "learner"


class ProjectMilestone(BaseModel):
    """A project-owned checkpoint; task progress may roll up to it later."""
    model_config = ConfigDict(extra="forbid")
    milestone_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    status: Literal["todo", "doing", "done"] = "todo"


class ProjectContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    tasks: list[ProjectTask] = Field(default_factory=list, max_length=100)
    milestones: list[ProjectMilestone] = Field(default_factory=list, max_length=30)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    session_refs: list[str] = Field(default_factory=list, max_length=100)
    source_refs: list[str] = Field(default_factory=list, max_length=100)
    archived: bool = False

    @model_validator(mode="after")
    def unique_refs(self):
        for values in (self.evidence_refs, self.session_refs, self.source_refs,
                       [t.task_id for t in self.tasks], [m.milestone_id for m in self.milestones]):
            if len(values) != len(set(values)) or any(not value.strip() for value in values):
                raise ValueError("Project references and task IDs must be unique and nonempty")
        milestone_ids = {milestone.milestone_id for milestone in self.milestones}
        if any(task.milestone_id and task.milestone_id not in milestone_ids for task in self.tasks):
            raise ValueError("Project task references an unknown milestone")
        if any(len(task.evidence_refs) != len(set(task.evidence_refs)) for task in self.tasks):
            raise ValueError("Project task evidence references must be unique")
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

    def _save(self, learner_id, record_id, kind, content, expected_revision, *, automatic=False):
        with self.store.lock:
            self.store.conn.execute("BEGIN IMMEDIATE")
            try:
                self.learning._authorize(learner_id)
                row = self.store.conn.execute(
                    "SELECT revision,payload FROM workspace_records WHERE record_id=? ORDER BY revision DESC LIMIT 1",
                    (record_id,),
                ).fetchone()
                revision = row[0] if row else 0
                if revision != expected_revision:
                    raise EvidenceConflict("Workspace revision CAS conflict")
                if revision:
                    self.latest(learner_id, kind, record_id)
                if kind == "project": self._validate_project(learner_id, content)
                if revision and kind == "project":
                    prior = json.loads(row["payload"])["content"]
                    if prior.get("archived"):
                        # Restoring a project is allowed, but changing its plan/tasks while
                        # archived would make its supposedly read-only history mutable.
                        restored = dict(content)
                        previous = dict(prior)
                        restored["archived"] = False
                        previous["archived"] = False
                        if content.get("archived") or restored != previous:
                            raise ValueError("Archived learning project is read-only; restore it before editing")
                    previous_tasks = {task["task_id"]: task for task in prior.get("tasks", [])}
                    for task in content.get("tasks", []):
                        prior_task = previous_tasks.get(task["task_id"])
                        prior_rule = (prior_task or task).get("completion_rule", "manual")
                        became_done = task.get("status") == "done" and (not prior_task or prior_task.get("status") != "done")
                        if became_done and prior_rule != "manual" and not automatic:
                            raise ValueError("This task can only be completed by its declared evidence rule")
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

    def project(self, learner_id, content, *, record_id=None, expected_revision=0, automatic=False):
        project_id = record_id or "project:"+uuid4().hex
        normalized = content.model_dump(mode="json")
        for task in normalized.get("tasks", []):
            if task.get("project_id") not in (None, project_id):
                raise ValueError("Project task belongs to another project")
            task["project_id"] = project_id
        return self._save(learner_id, project_id, "project", normalized, expected_revision, automatic=automatic)

    def delete_empty_project(self, learner_id: str, project_id: str, expected_revision: int) -> dict:
        """Permanently remove only a project that has never accumulated learning data.

        Learning evidence and goal/plan history are append-only.  A project that
        contains any of those records remains recoverable through archiving;
        only an empty, accidental project may be permanently removed.
        """
        with self.store.lock:
            self.store.conn.execute("BEGIN IMMEDIATE")
            try:
                self.learning._authorize(learner_id)
                row = self.store.conn.execute(
                    "SELECT revision,payload FROM workspace_records "
                    "WHERE record_id=? AND learner_id=? AND kind='project' "
                    "ORDER BY revision DESC LIMIT 1",
                    (project_id, learner_id),
                ).fetchone()
                if row is None:
                    raise KeyError("Unknown learning project")
                if row["revision"] != expected_revision:
                    raise ValueError("Workspace revision CAS conflict")
                content = ProjectContent.model_validate(json.loads(row["payload"])["content"])
                has_project_content = any((content.tasks, content.milestones, content.evidence_refs,
                                           content.session_refs, content.source_refs))
                has_contract = self.store.conn.execute(
                    "SELECT 1 FROM goal_contracts WHERE learner_id=? "
                    "AND json_extract(payload,'$.project_id')=? LIMIT 1",
                    (learner_id, project_id),
                ).fetchone() is not None
                has_evidence = self.store.conn.execute(
                    "SELECT 1 FROM learning_evidence WHERE learner_id=? "
                    "AND json_extract(payload,'$.project_id')=? LIMIT 1",
                    (learner_id, project_id),
                ).fetchone() is not None
                runtime_table = self.store.conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_sessions'"
                ).fetchone()
                has_session = runtime_table is not None and self.store.conn.execute(
                    "SELECT 1 FROM runtime_sessions WHERE learner_id=? "
                    "AND json_extract(payload,'$.project_id')=? LIMIT 1",
                    (learner_id, project_id),
                ).fetchone() is not None
                if has_project_content or has_contract or has_evidence or has_session:
                    raise ValueError("项目已有学习内容，不能永久删除；请改为归档项目")
                self.store.conn.execute(
                    "DELETE FROM workspace_records WHERE record_id=? AND learner_id=? AND kind='project'",
                    (project_id, learner_id),
                )
                self.learning._audit(learner_id, "workspace_project_deleted", {
                    "record_id": project_id, "revision": expected_revision,
                    "reason": "learner_confirmed_empty_project_delete",
                })
                self.store.conn.commit()
                return {"record_id": project_id, "deleted": True}
            except Exception:
                self.store.conn.rollback()
                raise

    def plan_snapshot(self, learner_id: str, project_id: str) -> dict:
        """Return the project arrangement a learner may revise in a plan draft."""
        value = self._project_value(learner_id, project_id)
        return ProjectContent.model_validate(value["content"]).model_dump(mode="json")

    def validate_plan_snapshot(self, learner_id: str, project_id: str, snapshot: dict) -> ProjectContent:
        """Validate a draft's project arrangement without making it live yet."""
        self._project_value(learner_id, project_id)
        candidate = ProjectContent.model_validate(snapshot)
        self._validate_project(learner_id, candidate.model_dump(mode="json"))
        return candidate

    def apply_plan_snapshot(self, learner_id: str, project_id: str, snapshot: dict):
        """Apply scheduling choices only after the learner signs their draft.

        Evidence, completion state, and system/plan-owned task rules remain live
        facts.  A schedule draft may change learner-facing arrangement (task
        title, due date, milestone, and learner-created task configuration), but
        can never erase evidence earned while the draft was being reviewed.
        """
        planned = self.validate_plan_snapshot(learner_id, project_id, snapshot)
        for _ in range(2):
            try:
                current_value = self._project_value(learner_id, project_id)
                current = ProjectContent.model_validate(current_value["content"])
                live_tasks = {task.task_id: task for task in current.tasks}
                merged_tasks = []
                for planned_task in planned.tasks:
                    live = live_tasks.pop(planned_task.task_id, None)
                    if live is None:
                        # Only a learner may add a new task through their plan.
                        if planned_task.created_from == "learner":
                            merged_tasks.append(planned_task)
                        continue
                    changes = {
                        "title": planned_task.title,
                        "due_at": planned_task.due_at,
                        "milestone_id": planned_task.milestone_id,
                        "status": live.status,
                        "evidence_refs": live.evidence_refs,
                        "project_id": project_id,
                    }
                    if live.created_from == "learner":
                        changes.update(type=planned_task.type, assessment_ref=planned_task.assessment_ref,
                                       completion_rule=planned_task.completion_rule)
                    merged_tasks.append(live.model_copy(update=changes))
                # Do not silently delete a task that appeared after the draft
                # was created (for example, a new system review reminder).
                merged_tasks.extend(live_tasks.values())
                milestones = list(planned.milestones)
                milestone_ids = {milestone.milestone_id for milestone in milestones}
                for milestone in current.milestones:
                    if milestone.milestone_id not in milestone_ids:
                        milestones.append(milestone)
                merged = current.model_copy(update={
                    "title": planned.title, "description": planned.description,
                    "tasks": merged_tasks, "milestones": milestones,
                    "source_refs": planned.source_refs, "session_refs": planned.session_refs,
                    "evidence_refs": planned.evidence_refs,
                })
                return self.project(learner_id, merged, record_id=project_id,
                                    expected_revision=current_value["revision"], automatic=True)
            except EvidenceConflict:
                continue
        raise EvidenceConflict("Workspace revision CAS conflict while applying signed plan")

    def _project_value(self, learner_id, project_id):
        value = self.latest(learner_id, "project", project_id)
        if value["content"].get("archived"):
            raise ValueError("Archived learning project is read-only")
        return value

    @staticmethod
    def _assessment_matches(task: ProjectTask, evidence) -> bool:
        if not task.assessment_ref:
            return True
        return task.assessment_ref in {
            evidence.assessment_id,
            f"{evidence.assessment_id}@{evidence.assessment_version}",
        }

    def task_for_project(self, learner_id: str, project_id: str, task_id: str) -> ProjectTask:
        """Return one executable task, never allowing a cross-project task id."""
        content = ProjectContent.model_validate(self._project_value(learner_id, project_id)["content"])
        task = next((item for item in content.tasks if item.task_id == task_id), None)
        if task is None:
            raise ValueError("Project task is not in the selected project")
        return task

    def complete_manual_task(self, learner_id: str, project_id: str, task_id: str) -> list[str]:
        """Student-confirmed completion for an open/manual task such as reflection."""
        for _ in range(2):
            try:
                value = self._project_value(learner_id, project_id)
                content = ProjectContent.model_validate(value["content"])
                changed = False
                tasks = []
                for task in content.tasks:
                    if task.task_id != task_id:
                        tasks.append(task)
                        continue
                    if task.completion_rule != "manual":
                        raise ValueError("This task must be completed by its declared evidence rule")
                    if task.status not in {"done", "skipped"}:
                        task = task.model_copy(update={"status": "done"})
                        changed = True
                    tasks.append(task)
                if not changed:
                    return []
                self.project(learner_id, content.model_copy(update={"tasks": tasks}),
                             record_id=project_id, expected_revision=value["revision"], automatic=True)
                return [task_id]
            except EvidenceConflict:
                continue
        raise EvidenceConflict("Workspace revision CAS conflict while completing task")

    def complete_tasks_for_evidence(self, learner_id, evidence, *, review_task_id: str | None = None,
                                    project_task_id: str | None = None):
        """Idempotently close only tasks whose declared rule is satisfied by evidence."""
        if not evidence.project_id:
            return []
        for _ in range(2):
            try:
                value = self._project_value(learner_id, evidence.project_id)
                content = ProjectContent.model_validate(value["content"])
                completed = []
                updated_tasks = []
                for task in content.tasks:
                    # A task-originated answer may only close the precise task
                    # that launched it.  Historical evidence without that link
                    # keeps the previous compatibility behaviour.
                    if project_task_id and task.task_id != project_task_id:
                        updated_tasks.append(task)
                        continue
                    match = self._assessment_matches(task, evidence)
                    verified = (task.completion_rule == "verified_submission" and match
                                and evidence.verdict_status == "passed")
                    finalized = (task.completion_rule == "assessment_finalized" and match
                                 and evidence.assessment_kind in {"diagnostic", "pre", "post", "transfer", "review"}
                                 and evidence.verdict_status != "unverifiable")
                    review_done = (task.type == "review" and review_task_id == task.task_id
                                   and evidence.verdict_status != "unverifiable")
                    if task.status not in {"done", "skipped"} and (verified or finalized or review_done):
                        refs = [*task.evidence_refs, evidence.evidence_id]
                        updated_tasks.append(task.model_copy(update={"status": "done", "evidence_refs": refs}))
                        completed.append(task.task_id)
                    else:
                        updated_tasks.append(task)
                if not completed:
                    return []
                updated = content.model_copy(update={"tasks": updated_tasks})
                self.project(learner_id, updated, record_id=evidence.project_id,
                             expected_revision=value["revision"], automatic=True)
                return completed
            except EvidenceConflict:
                continue
        return []

    def add_plan_tasks(self, learner_id, project_id: str, path: dict):
        """Materialize accepted path nodes as project-owned executable tasks."""
        value = self._project_value(learner_id, project_id)
        content = ProjectContent.model_validate(value["content"])
        existing = {task.task_id for task in content.tasks}
        generated = []
        for index, node in enumerate(path.get("nodes", []), 1):
            kc_id, decision = node.get("kc_id", ""), node.get("decision", "REMEDIATE")
            if not kc_id:
                continue
            task_id = f"plan:{index}:{kc_id}:{decision}"[:100]
            if task_id in existing:
                continue
            task_type = "assessment" if decision == "DIAGNOSE" else "review" if decision == "REVIEW" else "practice"
            rule = "assessment_finalized" if task_type == "assessment" else "verified_submission"
            title = {"DIAGNOSE": "完成初诊", "REVIEW": "进行无提示复习"}.get(decision, "完成练习") + f" · {kc_id}"
            generated.append(ProjectTask(task_id=task_id, project_id=project_id, title=title,
                                         type=task_type, completion_rule=rule, created_from="plan"))
        if not generated:
            return []
        self.project(learner_id, content.model_copy(update={"tasks": [*content.tasks, *generated]}),
                     record_id=project_id, expected_revision=value["revision"])
        return [task.task_id for task in generated]

    def sync_review_tasks(self, learner_id, project_id: str, review_tasks):
        """Create deterministic system reminder tasks for currently due reviews."""
        value = self._project_value(learner_id, project_id)
        content = ProjectContent.model_validate(value["content"])
        existing = {task.task_id for task in content.tasks}
        added = []
        for review in review_tasks:
            if review.task_id in existing:
                continue
            added.append(ProjectTask(task_id=review.task_id, project_id=project_id,
                                     title=f"无提示复习 · {review.kc_id}", type="review", due_at=review.due_at,
                                     evidence_refs=list(review.evidence_refs), completion_rule="verified_submission",
                                     created_from="system"))
        if not added:
            return []
        self.project(learner_id, content.model_copy(update={"tasks": [*content.tasks, *added]}),
                     record_id=project_id, expected_revision=value["revision"])
        return [task.task_id for task in added]

    def share(self, learner_id, audiences, expected_revision=0):
        if not set(audiences) <= {"teacher", "parent"}: raise ValueError("Unknown sharing audience")
        return self._save(learner_id, "sharing:"+learner_id, "sharing", dict(audiences=sorted(set(audiences))), expected_revision)

    def require_sharing(self, learner_id, audience):
        value = self.latest(learner_id, "sharing", "sharing:"+learner_id)
        if value["consent_version"] != self.learning.consent(learner_id)["version"] or audience not in value["content"]["audiences"]:
            raise PermissionError("Explicit current audience sharing required")
