from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.gateway.learning_routes import install_learning_routes
from app.storage.db import Store


BASE = datetime(2026, 10, 6, 8, tzinfo=timezone.utc)


@pytest.fixture
def api():
    store = Store(":memory:")
    app = FastAPI()
    install_learning_routes(app, store, Settings(learner_id="s", local_teacher_token="test-token", allow_simulated_time=True,
        learning_catalog_path=str(Path(__file__).parents[1] / "seeds" / "learning_assets_v1.json")))
    with TestClient(app) as client:
        yield client, store
    store.close()


def authorize(client):
    return client.post("/api/learning/consent/s", json={"scopes": ["teaching"], "version": "v1", "source": "learner"})


def attempt(item="PRE-SOLVE-1", answer="4", attempt_id="attempt1", at=BASE, **kwargs):
    return dict(assessment_id=item, assessment_version="1.0.0", answer=answer, attempt_id=attempt_id,
                occurred_at=at.isoformat(), **kwargs)


def test_asset_payload_does_not_expose_answers_and_verdict_cannot_be_supplied(api):
    client, _ = api
    assets = client.get("/api/learning/assets").json()
    assert all("answer" not in asset for asset in assets["assessments"])
    authorize(client)
    body = attempt()
    body["verdict_status"] = "passed"
    assert client.post("/api/learning/assessment/s/submit", json=body).status_code == 422


def test_stable_attempt_retry_content_conflict_and_withdrawal(api):
    client, _ = api
    assert client.get("/api/learning/state/s").status_code == 403
    authorize(client)
    first = client.post("/api/learning/assessment/s/submit", json=attempt())
    assert first.status_code == 200
    assert first.json()["verdict_status"] == "passed"
    assert client.post("/api/learning/assessment/s/submit", json=attempt()).json() == first.json()
    assert client.post("/api/learning/assessment/s/submit", json=attempt(answer="7")).status_code == 409
    assert len(client.get("/api/learning/evidence/s").json()) == 1
    assert client.get("/api/learning/state/other").status_code == 403
    assert client.delete("/api/learning/consent/s").status_code == 200
    assert client.get("/api/learning/evidence/s").status_code == 403
    assert client.post("/api/learning/assessment/s/submit", json=attempt()).status_code == 403


def test_assessment_evidence_preserves_exact_asset_governance_metadata(api):
    client, _ = api
    authorize(client)
    asset = next(item for item in client.get("/api/learning/assets").json()["assessments"]
                 if item["ref"]["asset_id"] == "PRE-SOLVE-1")
    assert client.post("/api/learning/assessment/s/submit", json=attempt()).status_code == 200
    details = client.get("/api/learning/evidence/s").json()[0]["verifier_details"]
    assert details["assessment_provenance"] == asset["provenance"]
    assert details["calibration_status"] == asset["calibration_status"]
    assert details["comparison_group"] == asset["comparison_group"]


def test_multiround_due_recall_updates_retention_and_retry_is_idempotent(api):
    client, _ = api
    authorize(client)
    assert client.post("/api/learning/assessment/s/submit", json=attempt()).status_code == 200
    assert client.get("/api/learning/reviews/s", params={"as_of": BASE.isoformat()}).json()["tasks"] == []
    later = BASE + timedelta(days=2)
    queue = client.get("/api/learning/reviews/s", params={"as_of": later.isoformat()}).json()["tasks"]
    assert len(queue) == 1
    body = attempt("DELAYED-SOLVE-1", "6", "review1", later, task_id=queue[0]["task_id"])
    first = client.post("/api/learning/reviews/s/submit", json=body)
    assert first.status_code == 200
    assert first.json()["verdict_status"] == "passed"
    assert client.post("/api/learning/reviews/s/submit", json=body).json() == first.json()
    assert len(client.get("/api/learning/evidence/s").json()) == 2
    state = client.get("/api/learning/state/s").json()["retention"][0]
    assert state["state_version"] == 2 and state["stability"] > 1.8
    next_time = later + timedelta(days=3)
    task2 = client.get("/api/learning/reviews/s", params={"as_of": next_time.isoformat()}).json()["tasks"][0]
    wrong = attempt("DELAYED-SOLVE-2", "0", "review2", next_time, task_id=task2["task_id"])
    assert client.post("/api/learning/reviews/s/submit", json=wrong).json()["verdict_status"] == "failed"
    assert client.get("/api/learning/state/s").json()["retention"][0]["lapse_count"] == 1


def test_teacher_correction_is_authorized_append_only_and_metrics_use_active_evidence(api):
    client, store = api
    authorize(client)
    rows = [attempt("PRE-SOLVE-1", "0", "p1"), attempt("PRE-SOLVE-2", "0", "p2"),
            attempt("POST-SOLVE-1", "5", "q1", BASE + timedelta(days=1)),
            attempt("POST-SOLVE-2", "6", "q2", BASE + timedelta(days=1))]
    evidence_id = None
    for body in rows:
        response = client.post("/api/learning/assessment/s/submit", json=body)
        assert response.status_code == 200
        evidence_id = response.json()["evidence_id"]
    query = {"as_of": (BASE + timedelta(days=2)).isoformat()}
    assert client.get("/api/learning/metrics/s", params=query).json()["gain"] == 1
    correction = dict(evidence_id=evidence_id, correction_id="c1", status="failed", score=0,
                      confidence=0.95, reason="Teacher revised rubric judgment", rubric={"correctness": 0})
    endpoint = "/api/learning/teacher/s/corrections"
    assert client.post(endpoint, json=correction).status_code == 403
    approved = client.post(endpoint, json=correction, headers={"x-teacher-token": "test-token"})
    assert approved.status_code == 200
    assert client.post(endpoint, json=correction, headers={"x-teacher-token": "test-token"}).json() == approved.json()
    assert len(client.get("/api/learning/evidence/s").json()) == 5
    assert client.get("/api/learning/metrics/s", params=query).json()["gain"] == 0.5
    assert "Teacher revised" in store.conn.execute("SELECT payload FROM learning_audit WHERE payload LIKE '%teacher_correction%'").fetchone()[0]


def test_diagnosis_and_missing_metrics_states_are_real(api):
    client, _ = api
    authorize(client)
    decision = client.post("/api/learning/diagnosis/s/next", json={}).json()
    assert decision["assessment"]["ref"]["asset_id"] == "D-BALANCE-1"
    assert "answer" not in decision["assessment"]
    assert client.get("/api/learning/metrics/s").json()["status"] == "insufficient_data"
    client.post("/api/learning/assessment/s/submit", json=attempt("D-BALANCE-1", "0"))
    assert client.post("/api/learning/diagnosis/s/next", json={}).json()["decision"]["status"] == "needs_learning"


def test_invalid_assistance_and_review_clock_return_client_errors(api):
    client, _ = api
    authorize(client)
    assert client.post("/api/learning/assessment/s/submit", json=attempt(assistance_mode="unknown")).status_code == 422
    assert client.post("/api/learning/assessment/s/submit", json=attempt()).status_code == 200
    backwards = attempt("DELAYED-SOLVE-1", "6", "past", BASE - timedelta(days=1), task_id="arbitrary")
    assert client.post("/api/learning/reviews/s/submit", json=backwards).status_code == 400


def test_receipt_failure_rolls_back_evidence_and_state_then_retry_recovers(api, monkeypatch):
    client, store = api
    authorize(client)
    from app.learning.service import LearningService
    original = LearningService.consume

    def consume_with_failure(self, evidence, **kwargs):
        def fail(checkpoint):
            if checkpoint == "receipt":
                raise RuntimeError("simulated receipt failure")
        kwargs["fault"] = fail
        return original(self, evidence, **kwargs)

    monkeypatch.setattr(LearningService, "consume", consume_with_failure)
    with pytest.raises(RuntimeError, match="simulated receipt"):
        client.post("/api/learning/assessment/s/submit", json=attempt())
    assert store.conn.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM learning_api_receipts").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM learning_states").fetchone()[0] == 0
    monkeypatch.setattr(LearningService, "consume", original)
    assert client.post("/api/learning/assessment/s/submit", json=attempt()).status_code == 200


def test_teacher_rubric_is_in_immutable_evidence_and_revision_cannot_fork(api):
    client, _ = api
    authorize(client)
    original = client.post("/api/learning/assessment/s/submit", json=attempt()).json()
    body = dict(evidence_id=original["evidence_id"], correction_id="c1", status="failed", score=0,
                confidence=0.8, reason="Reviewed response", rubric={"correctness": 0})
    endpoint = "/api/learning/teacher/s/corrections"
    result = client.post(endpoint, json=body, headers={"x-teacher-token": "test-token"})
    assert result.status_code == 200
    revised = client.get("/api/learning/evidence/s").json()[-1]
    assert revised["verifier_details"]["rubric"] == {"correctness": 0}
    assert revised["verifier_details"]["source"] == "authorized-local-teacher"
    assert revised["verifier_details"]["original_verifier_details"]["answer"] == "4"
    fork = dict(body, correction_id="fork")
    assert client.post(endpoint, json=fork, headers={"x-teacher-token": "test-token"}).status_code == 409


def test_diagnosis_remediation_retry_and_unknown_self_reports(api):
    client, _ = api
    authorize(client)
    client.post("/api/learning/assessment/s/submit", json=attempt("D-BALANCE-1", "0", "fail"))
    assert client.post("/api/learning/diagnosis/s/next", json={}).json()["decision"]["status"] == "needs_learning"
    client.post("/api/learning/assessment/s/submit", json=attempt("D-BALANCE-1", "4", "retry"))
    assert client.post("/api/learning/diagnosis/s/next", json={}).json()["assessment"]["ref"]["asset_id"] == "D-BALANCE-2"
    client.post("/api/learning/assessment/s/submit", json=attempt("D-BALANCE-2", "7", "guess", self_report="guessed"))
    assert client.post("/api/learning/diagnosis/s/next", json={}).json()["decision"]["status"] == "missing_assets"
    evidence = client.get("/api/learning/evidence/s").json()[-1]
    assert evidence["verdict_status"] == "unverifiable"
    assert evidence["verifier_details"]["diagnostic_response"] == "guessed"


def test_default_local_mode_rejects_future_evidence():
    store = Store(":memory:")
    app = FastAPI()
    install_learning_routes(app, store, Settings(learner_id="s"))
    with TestClient(app) as client:
        authorize(client)
        future = datetime.now(timezone.utc) + timedelta(days=2)
        response = client.post("/api/learning/assessment/s/submit", json=attempt(at=future))
        assert response.status_code == 400
        assert store.conn.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0] == 0
    store.close()


def test_diagnosis_unknown_target_and_invalid_budget_are_client_errors(api):
    client, _ = api
    authorize(client)
    endpoint = "/api/learning/diagnosis/s/next"
    assert client.post(endpoint, json={"target_kc_id": "unknown"}).status_code == 404
    assert client.post(endpoint, json={"max_tasks": 0}).status_code == 400
    assert client.post(endpoint, json={"max_tasks": 31}).status_code == 400


def test_assisted_or_guessed_due_review_is_not_independent_recall(api):
    client, _ = api
    authorize(client)
    client.post("/api/learning/assessment/s/submit", json=attempt())
    later = BASE + timedelta(days=2)
    task = client.get("/api/learning/reviews/s", params={"as_of": later.isoformat()}).json()["tasks"][0]
    body = attempt("DELAYED-SOLVE-1", "6", "assisted-review", later, task_id=task["task_id"], hint_level=1, assistance_mode="hint")
    assert client.post("/api/learning/reviews/s/submit", json=body).status_code == 400
    guessed = attempt("DELAYED-SOLVE-1", "6", "guessed-review", later, task_id=task["task_id"], self_report="guessed")
    response = client.post("/api/learning/reviews/s/submit", json=guessed)
    assert response.status_code == 200
    assert response.json()["verdict_status"] == "unverifiable"
    assert client.get("/api/learning/state/s").json()["retention"][0]["state_version"] == 1


def test_plan_workspace_requires_consent_and_restores_saved_lifecycle(api):
    client, store = api
    endpoint = "/api/learning/plans/s/workspace"
    assert client.get(endpoint).status_code == 403
    authorize(client)
    assert client.get(endpoint).json() == dict(contract=None, active=None, drafts=[], proposals=[])
    from app.core.schema import GoalContract, GoalStatement
    from app.learning.plans import PlanService
    contract = GoalContract(learner_id="s", goal_statement=GoalStatement(text="我的学习目标", authored_by="student"))
    store.save_contract(contract)
    plans = PlanService(store)
    active = plans.prepare_draft("s", contract.goal_contract_id, {"cadence": "每天一题"}, BASE)
    active = plans.sign("s", active.version_id, BASE)
    proposal = plans.propose("s", contract.goal_contract_id, {"cadence": "每天两题"}, BASE)
    draft = plans.prepare_draft("s", contract.goal_contract_id, {"cadence": "每周复习"}, BASE)
    result = client.get(endpoint).json()
    assert result["contract"]["goal_statement"]["text"] == "我的学习目标"
    assert result["active"]["version_id"] == active.version_id
    assert result["drafts"][0]["version_id"] == draft.version_id
    assert result["proposals"][0]["version_id"] == proposal.version_id
    accepted = client.post(f"/api/learning/plans/s/{proposal.version_id}/accept", json={})
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "draft"
    assert accepted.json()["confirmed_at"] is None
    assert client.get(endpoint).json()["active"]["version_id"] == active.version_id
    client.delete("/api/learning/consent/s")
    assert client.get(endpoint).status_code == 403
    assert client.get("/api/learning/plans/other/workspace").status_code == 403
