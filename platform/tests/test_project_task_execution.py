"""Executable project tasks must be completed only by their declared evidence rule."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.gateway.routes import create_app
from app.learning.assets import load_catalog
from app.learning.schema import ReviewTask
from app.learning.workspace import WorkspaceService
from app.storage.db import Store


def test_project_task_completion_requires_evidence_or_manual_action():
    store = Store(":memory:")
    with TestClient(create_app(Settings(learner_id="s", assessment_require_ticket=True), store=store)) as client:
        assert client.post("/api/learning/consent/s", json={
            "scopes": ["teaching"], "version": "v1", "source": "student"
        }).status_code == 200
        project_id = "project:execution"
        created = client.post("/api/learning/projects/s", json={
            "record_id": project_id,
            "content": {
                "title": "执行型计划",
                "tasks": [
                    {"task_id": "manual", "title": "写学习反思", "type": "reflection",
                     "completion_rule": "manual"},
                    {"task_id": "practice", "title": "完成练习", "type": "practice",
                     "assessment_ref": "PRE-SOLVE-1@1.0.0", "completion_rule": "verified_submission"},
                    {"task_id": "assessment", "title": "完成阶段测验", "type": "assessment",
                     "assessment_ref": "PRE-SOLVE-1@1.0.0", "completion_rule": "assessment_finalized"},
                ],
            },
        })
        assert created.status_code == 200, created.text

        # An automatic task cannot be closed by editing project JSON, while a
        # reflection/manual task remains student-controlled.
        tampered = created.json()
        tampered["content"]["tasks"][1]["status"] = "done"
        rejected = client.post("/api/learning/projects/s", json={
            "record_id": project_id, "expected_revision": tampered["revision"],
            "content": tampered["content"],
        })
        assert rejected.status_code == 409, rejected.text

        issued = client.post("/api/learning/assessment/s/issue", json={
            "assessment_id": "PRE-SOLVE-1", "issuance_id": "execution-ticket", "project_id": project_id,
        })
        assert issued.status_code == 200, issued.text
        submitted = client.post("/api/learning/assessment/s/submit", json={
            "assessment_id": "PRE-SOLVE-1", "attempt_id": "execution-answer", "answer": "4",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "delivery_ref": issued.json()["delivery_ref"], "project_id": project_id,
        })
        assert submitted.status_code == 200, submitted.text
        assert set(submitted.json()["completed_task_ids"]) == {"practice", "assessment"}

        current = next(item for item in client.get("/api/learning/projects/s").json()["projects"]
                       if item["record_id"] == project_id)
        statuses = {task["task_id"]: task for task in current["content"]["tasks"]}
        assert statuses["manual"]["status"] == "todo"
        assert statuses["practice"]["status"] == statuses["assessment"]["status"] == "done"
        assert len(statuses["practice"]["evidence_refs"]) == 1
        assert statuses["practice"]["evidence_refs"] == statuses["assessment"]["evidence_refs"]

        current["content"]["tasks"][0]["status"] = "done"
        manual = client.post("/api/learning/projects/s", json={
            "record_id": project_id, "expected_revision": current["revision"],
            "content": current["content"],
        })
        assert manual.status_code == 200, manual.text
    store.close()


def test_task_origin_is_scoped_to_one_project_session_and_evidence():
    store = Store(":memory:")
    with TestClient(create_app(Settings(learner_id="s", assessment_require_ticket=True), store=store)) as client:
        assert client.post("/api/learning/consent/s", json={
            "scopes": ["teaching"], "version": "v1", "source": "student"
        }).status_code == 200
        first, second = "project:one", "project:two"
        for project_id, task_id in ((first, "practice-one"), (second, "practice-two")):
            response = client.post("/api/learning/projects/s", json={
                "record_id": project_id,
                "content": {"title": project_id, "tasks": [{
                    "task_id": task_id, "title": "完成练习 · MATH.G7.EQ.SOLVE",
                    "type": "practice", "assessment_ref": "PRE-SOLVE-1@1.0.0",
                    "completion_rule": "verified_submission",
                }]},
            })
            assert response.status_code == 200, response.text

        task_session = client.post("/api/sessions", json={
            "learner_id": "s", "project_id": first, "task_id": "practice-one",
        })
        assert task_session.status_code == 200, task_session.text
        state = client.get(f"/api/sessions/{task_session.json()['session_id']}?project_id={first}")
        assert state.json()["task_id"] == "practice-one"
        foreign = client.post("/api/sessions", json={
            "learner_id": "s", "project_id": first, "task_id": "practice-two",
        })
        assert foreign.status_code == 404

        issued = client.post("/api/learning/assessment/s/issue", json={
            "assessment_id": "PRE-SOLVE-1", "issuance_id": "task-origin-ticket", "project_id": first,
        })
        submitted = client.post("/api/learning/assessment/s/submit", json={
            "assessment_id": "PRE-SOLVE-1", "attempt_id": "task-origin-answer", "answer": "4",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "delivery_ref": issued.json()["delivery_ref"], "project_id": first, "task_id": "practice-one",
        })
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["completed_task_ids"] == ["practice-one"]
        evidence = client.get("/api/learning/evidence/s", params={"project_id": first}).json()[0]
        assert evidence["project_id"] == first
        assert evidence["project_task_id"] == "practice-one"
        projects = {item["record_id"]: item for item in client.get("/api/learning/projects/s").json()["projects"]}
        assert projects[first]["content"]["tasks"][0]["status"] == "done"
        assert projects[second]["content"]["tasks"][0]["status"] == "todo"
    store.close()


def test_due_review_becomes_a_system_reminder_task_once():
    store = Store(":memory:")
    settings = Settings(learner_id="s", assessment_require_ticket=True)
    with TestClient(create_app(settings, store=store)) as client:
        assert client.post("/api/learning/consent/s", json={
            "scopes": ["teaching"], "version": "v1", "source": "student"
        }).status_code == 200
        project_id = "project:review-reminder"
        assert client.post("/api/learning/projects/s", json={
            "record_id": project_id, "content": {"title": "复习提醒"}
        }).status_code == 200
        now = datetime.now(timezone.utc)
        due = ReviewTask(task_id="review:s:MATH.G7.EQ.SOLVE:1", learner_id="s",
                         kc_id="MATH.G7.EQ.SOLVE", due_at=now, as_of=now,
                         retrievability=0.5, forgetting_risk=0.5, state_version=1,
                         evidence_refs=["assessment:source"], reason="scheduled")
        workspace = WorkspaceService(store, load_catalog(settings))
        assert workspace.sync_review_tasks("s", project_id, [due]) == [due.task_id]
        assert workspace.sync_review_tasks("s", project_id, [due]) == []
        project = workspace.latest("s", "project", project_id)
        task = project["content"]["tasks"][0]
        assert task["created_from"] == "system"
        assert task["type"] == "review"
        assert task["completion_rule"] == "verified_submission"
        assert task["due_at"]
    store.close()


def test_signed_revision_applies_its_task_milestone_and_deadline_snapshot():
    store = Store(":memory:")
    settings = Settings(learner_id="s", assessment_require_ticket=True)
    with TestClient(create_app(settings, store=store)) as client:
        assert client.post("/api/learning/consent/s", json={
            "scopes": ["teaching"], "version": "v1", "source": "student"
        }).status_code == 200
        project_id = "project:versioned-arrangement"
        created = client.post("/api/learning/projects/s", json={
            "record_id": project_id,
            "content": {"title": "版本化安排", "tasks": [
                {"task_id": "reflection", "title": "写反思", "type": "reflection"}
            ]},
        })
        assert created.status_code == 200, created.text
        contract = client.post("/api/contracts", json={
            "learner_id": "s", "project_id": project_id, "goal_text": "完成项目",
            "deadline_title": "旧截止", "deadline_at": "2026-10-10T00:00:00+00:00",
        }).json()
        initial = contract["plan_version"]
        confirmed = client.post(f"/api/plans/{initial['version_id']}/sign", json={
            "learner_id": "s", "project_id": project_id
        })
        assert confirmed.status_code == 200, confirmed.text

        project = next(item for item in client.get("/api/learning/projects/s").json()["projects"]
                       if item["record_id"] == project_id)
        snapshot = project["content"]
        snapshot["milestones"] = [{"milestone_id": "m1", "title": "周末复盘", "status": "todo"}]
        snapshot["tasks"][0] |= {"title": "写一段周末反思", "due_at": "2026-10-18T00:00:00+00:00",
                                 "milestone_id": "m1"}
        revision = client.post(f"/api/plans/{initial['version_id']}/modify", json={
            "learner_id": "s", "project_id": project_id, "reason": "周内时间变少",
            "content": initial["content"] | {
                "deadline_at": "2026-10-20T00:00:00+00:00", "project_plan": snapshot,
            },
        })
        assert revision.status_code == 200, revision.text
        assert client.get(f"/api/plans/{initial['version_id']}?learner_id=s&project_id={project_id}").json()["status"] == "confirmed"
        signed = client.post(f"/api/plans/{revision.json()['version_id']}/sign", json={
            "learner_id": "s", "project_id": project_id
        })
        assert signed.status_code == 200, signed.text
        updated = next(item for item in client.get("/api/learning/projects/s").json()["projects"]
                       if item["record_id"] == project_id)
        assert updated["content"]["milestones"][0]["title"] == "周末复盘"
        assert updated["content"]["tasks"][0]["title"] == "写一段周末反思"
        assert updated["content"]["tasks"][0]["milestone_id"] == "m1"
        workspace = client.get(f"/api/learning/plans/s/workspace?project_id={project_id}").json()
        assert workspace["contract"]["external_deadline_refs"][0]["due_at"].startswith("2026-10-20")
    store.close()


def test_system_proposal_is_preserved_when_learner_accepts_a_new_draft():
    store = Store(":memory:")
    settings = Settings(learner_id="s", assessment_require_ticket=True)
    with TestClient(create_app(settings, store=store)) as client:
        assert client.post("/api/learning/consent/s", json={
            "scopes": ["teaching"], "version": "v1", "source": "student"
        }).status_code == 200
        project_id = "project:proposal-history"
        assert client.post("/api/learning/projects/s", json={
            "record_id": project_id, "content": {"title": "建议历史"}
        }).status_code == 200
        assert client.post("/api/contracts", json={
            "learner_id": "s", "project_id": project_id, "goal_text": "审阅建议"
        }).status_code == 200
        proposal = client.post("/api/path/propose", json={
            "learner_id": "s", "project_id": project_id, "path": {"nodes": []}
        })
        assert proposal.status_code == 200, proposal.text
        accepted = client.post(f"/api/learning/plans/s/{proposal.json()['version_id']}/accept", json={
            "project_id": project_id
        })
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "draft"
        assert accepted.json()["version_id"] != proposal.json()["version_id"]
        history = client.get(f"/api/learning/plans/s/workspace?project_id={project_id}").json()["history"]
        states = {item["version_id"]: item["status"] for item in history}
        assert states[proposal.json()["version_id"]] == "superseded"
        assert states[accepted.json()["version_id"]] == "draft"
    store.close()
