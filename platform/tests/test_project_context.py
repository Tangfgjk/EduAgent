"""Project selection must separate workspace records without splitting learner identity."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.gateway.routes import create_app
from app.storage.db import Store


def test_only_empty_projects_can_be_permanently_deleted():
    store = Store(":memory:")
    with TestClient(create_app(Settings(learner_id="s"), store=store)) as client:
        assert client.post("/api/learning/consent/s", json={
            "scopes": ["teaching"], "version": "v1", "source": "student"
        }).status_code == 200
        empty = client.post("/api/learning/projects/s", json={
            "record_id": "project:empty", "content": {"title": "误建项目"}
        }).json()
        stale = client.request("DELETE", "/api/learning/projects/s/project:empty", json={"expected_revision": 99})
        assert stale.status_code == 409
        removed = client.request("DELETE", "/api/learning/projects/s/project:empty", json={
            "expected_revision": empty["revision"]
        })
        assert removed.status_code == 200, removed.text
        assert removed.json() == {"record_id": "project:empty", "deleted": True}
        assert client.get("/api/learning/projects/s").json()["projects"] == []

        protected = client.post("/api/learning/projects/s", json={
            "record_id": "project:kept", "content": {
                "title": "已有任务", "tasks": [{"task_id": "t1", "title": "完成练习"}]
            }
        }).json()
        denied = client.request("DELETE", "/api/learning/projects/s/project:kept", json={
            "expected_revision": protected["revision"]
        })
        assert denied.status_code == 409
        assert "归档项目" in denied.json()["detail"]
    store.close()


def test_project_context_separates_plans_evidence_and_sessions():
    store = Store(":memory:")
    with TestClient(create_app(Settings(learner_id="s", assessment_require_ticket=True), store=store)) as client:
        assert client.post("/api/learning/consent/s", json={
            "scopes": ["teaching"], "version": "v1", "source": "student"
        }).status_code == 200
        first, second = "project:first", "project:second"
        for project_id in (first, second):
            content = {"title": project_id}
            if project_id == first:
                content |= {"milestones": [{"milestone_id": "m1", "title": "基础巩固", "status": "doing"}],
                            "tasks": [{"task_id": "t1", "title": "完成第一题", "milestone_id": "m1"}]}
            response = client.post("/api/learning/projects/s", json={
                "record_id": project_id, "content": content
            })
            assert response.status_code == 200, response.text

        contract = client.post("/api/contracts", json={
            "learner_id": "s", "project_id": first, "goal_text": "掌握方程",
            "success_criteria": ["独立作答正确率达到 80%", "能解释步骤"],
            "deadline_title": "单元测验", "deadline_at": "2026-10-20T15:00:00+00:00",
        })
        assert contract.status_code == 200, contract.text
        assert [item["description"] for item in contract.json()["contract"]["success_criteria"]] == ["独立作答正确率达到 80%", "能解释步骤"]
        changed = client.put(f"/api/contracts/{contract.json()['contract']['goal_contract_id']}", json={
            "project_id": first, "goal_text": "独立解方程", "success_criteria": ["完成一次迁移题"],
            "deadline_title": "项目截止", "deadline_at": "2026-10-21T15:00:00+00:00",
        })
        assert changed.status_code == 200, changed.text
        assert changed.json()["contract"]["goal_statement"]["text"] == "独立解方程"
        assert client.post("/api/contracts", json={
            "learner_id": "s", "project_id": first, "goal_text": "重复目标"
        }).status_code == 409
        assert client.get(f"/api/learning/plans/s/workspace?project_id={first}").json()["contract"]["project_id"] == first
        assert client.get(f"/api/learning/plans/s/workspace?project_id={second}").json()["contract"] is None
        recommendation = client.get(f"/api/path/recommend?learner_id=s&project_id={first}")
        assert recommendation.status_code == 200, recommendation.text
        accepted = client.post("/api/path/accept", json={
            "learner_id": "s", "project_id": first, "path": recommendation.json(),
        })
        assert accepted.status_code == 200, accepted.text
        assert client.post("/api/path/accept", json={
            "learner_id": "s", "project_id": second, "path": recommendation.json(),
        }).status_code == 404

        issued = client.post("/api/learning/assessment/s/issue", json={
            "assessment_id": "PRE-SOLVE-1", "issuance_id": "first-ticket", "project_id": first
        })
        assert issued.status_code == 200, issued.text
        answer = {
            "assessment_id": "PRE-SOLVE-1", "attempt_id": "first-answer",
            "answer": "4", "occurred_at": datetime.now(timezone.utc).isoformat(),
            "delivery_ref": issued.json()["delivery_ref"], "project_id": first,
        }
        wrong_project = client.post("/api/learning/assessment/s/submit", json=answer | {
            "project_id": second, "attempt_id": "wrong-project"
        })
        assert wrong_project.status_code == 409
        submitted = client.post("/api/learning/assessment/s/submit", json=answer)
        assert submitted.status_code == 200, submitted.text
        assert len(client.get(f"/api/learning/evidence/s?project_id={first}").json()) == 1
        assert client.get(f"/api/learning/evidence/s?project_id={second}").json() == []
        assert client.get(f"/api/learning/growth/s?project_id={second}").json()["effective_count"] == 0

        session = client.post("/api/sessions", json={"learner_id": "s", "project_id": first})
        assert session.status_code == 200, session.text
        sid = session.json()["session_id"]
        listed = client.get(f"/api/learning/projects/{first}/sessions")
        assert listed.status_code == 200, listed.text
        assert [item["session_id"] for item in listed.json()["sessions"]] == [sid]
        assert client.get(f"/api/learning/projects/{second}/sessions").json()["sessions"] == []
        assert client.get(f"/api/sessions/{sid}?project_id={second}").status_code == 404
        assert client.get(f"/api/sessions/{sid}?project_id={first}").json()["project_id"] == first

        current = next(project for project in client.get("/api/learning/projects/s").json()["projects"]
                       if project["record_id"] == first)
        archived = client.post("/api/learning/projects/s", json={
            "record_id": first, "expected_revision": current["revision"],
            "content": current["content"] | {"archived": True},
        })
        assert archived.status_code == 200, archived.text
        # Historic project material remains readable, but must not gain a new
        # plan mutation or a new chat event while archived.
        assert client.get(f"/api/learning/plans/s/workspace?project_id={first}").status_code == 200
        draft_id = contract.json()["plan_version"]["version_id"]
        assert client.post(f"/api/plans/{draft_id}/modify", json={
            "learner_id": "s", "project_id": first, "content": {}, "reason": "test"
        }).status_code == 409
        assert client.post(f"/api/sessions/{sid}/messages?project_id={first}", json={
            "text": "归档项目不应写入", "attempt_id": "archived-message"
        }).status_code == 409
    store.close()
