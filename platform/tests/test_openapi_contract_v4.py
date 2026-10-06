"""API contract boundaries: published bodies, strict evidence input and receipts."""
from fastapi.testclient import TestClient

from app.config import Settings
from app.gateway.routes import create_app
from app.llm.client import FakeLLM
from app.storage.db import Store


def client():
    return TestClient(create_app(Settings(learner_id="contract-test"), FakeLLM(), Store()))


def body_schema(document, path):
    operation = document["paths"][path]["post"]
    assert operation["requestBody"]["required"]
    reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    return document["components"]["schemas"][reference.rsplit("/", 1)[-1]]


def test_published_json_contract_and_aware_assessment_input():
    document = client().get("/openapi.json").json()
    assert document["info"]["title"] == "桂子问津 Wenjin"
    assessment = body_schema(document, "/api/learning/assessment/{learner_id}/submit")
    assert assessment["additionalProperties"] is False
    assert {"assessment_id", "attempt_id", "answer", "occurred_at"} <= set(assessment["required"])
    assert assessment["properties"]["occurred_at"]["format"] == "date-time"
    assert "delivery_ref" in assessment["properties"]
    message = body_schema(document, "/api/sessions/{sid}/messages")
    assert {"text", "answer", "attempt_id"} <= message["properties"].keys()
    session = body_schema(document, "/api/sessions")
    assert "session_type" in session["properties"]


def test_contract_validation_authorization_and_idempotent_receipt():
    api = client()
    assert api.post("/api/sessions", json={}).status_code == 403
    consent = {"scopes": ["teaching"], "version": "contract-v4", "source": "learner:test"}
    assert api.post("/api/learning/consent/contract-test", json=consent).status_code == 200
    payload = {"assessment_id": "D-SOLVE-1", "attempt_id": "a1", "answer": "3", "occurred_at": "2026-01-01T00:00:00"}
    assert api.post("/api/learning/assessment/contract-test/submit", json=payload).status_code == 422
    payload["occurred_at"] += "Z"
    payload["untrusted_score"] = 1
    assert api.post("/api/learning/assessment/contract-test/submit", json=payload).status_code == 422
    initial = api.post("/api/sessions", json={"session_type": "explore"})
    assert initial.status_code == 200
    assert {"session_id", "reply", "ui", "denial"} == initial.json().keys()
    path = "/api/sessions/" + initial.json()["session_id"] + "/messages"
    request = {"answer": "8", "attempt_id": "receipt-v4"}
    first, replay = api.post(path, json=request), api.post(path, json=request)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert {"reply", "ui", "denial", "events", "attempt_id"} == first.json().keys()
    assert api.post(path, json={**request, "answer": "9"}).status_code == 409
