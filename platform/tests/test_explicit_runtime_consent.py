"""Legacy runtime boundaries require explicit teaching consent and respect withdrawal."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.schema import QuestionItem, Verdict, utcnow
from app.gateway.routes import create_app
from app.learning.bridge import record_attempt
from app.learning.service import ConsentDenied, LearningService
from app.storage.db import Store
from app.cli import ensure_teaching_consent


def test_legacy_endpoints_reject_missing_consent_without_creating_records():
    store = Store()
    client = TestClient(create_app(Settings(learner_id="student"), store=store))
    assert client.post("/api/contracts", json={"goal_text": "学会解方程"}).status_code == 403
    assert client.post("/api/sessions", json={}).status_code == 403
    assert client.get("/api/contracts/latest").status_code == 403
    assert LearningService(store).consent("student") is None
    assert store.conn.execute("SELECT COUNT(*) FROM goal_contracts").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_contract_creation_never_regrants_withdrawn_teaching_consent():
    store = Store()
    service = LearningService(store)
    client = TestClient(create_app(Settings(learner_id="student"), store=store))
    client.post("/api/learning/consent/student", json={"scopes": ["teaching"], "version": "c1", "source": "learner"})
    assert client.post("/api/contracts", json={"goal_text": "学会解方程"}).status_code == 200
    assert client.delete("/api/learning/consent/student").status_code == 200
    withdrawn = service.consent("student")
    assert client.post("/api/contracts", json={"goal_text": "新的目标"}).status_code == 403
    assert client.post("/api/sessions", json={}).status_code == 403
    assert service.consent("student") == withdrawn
    assert store.conn.execute("SELECT COUNT(*) FROM goal_contracts").fetchone()[0] == 1


@pytest.mark.parametrize("withdrawn", [False, True])
def test_bridge_cannot_create_or_regrant_teaching_consent(withdrawn):
    store = Store()
    service = LearningService(store)
    if withdrawn:
        service.set_consent("student", ["teaching"], "c1", "learner", utcnow())
        service.set_consent("student", [], "c2", "learner", utcnow())
    prior = service.consent("student")
    item = QuestionItem(item_id="q", kc_id="MATH.G7.EQ.SOLVE", stem="x=3", difficulty=.2,
                        answer={"var": "x", "value": "3"})
    verdict = Verdict(artifact_id="artifact", verifier_id="sympy-v1", status="passed", score=1)
    with pytest.raises(ConsentDenied):
        record_attempt(store, "student", "session", item, verdict, "a1")
    assert service.consent("student") == prior
    assert store.conn.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0] == 0


def test_cli_consent_requires_exact_explicit_response_and_never_adds_other_purposes():
    store = Store()
    assert ensure_teaching_consent(store, "student", input_fn=lambda prompt: "确认") is False
    assert LearningService(store).consent("student") is None
    assert ensure_teaching_consent(store, "student", input_fn=lambda prompt: "同意授权") is True
    consent = LearningService(store).consent("student")
    assert consent["scopes"] == ["teaching"] and consent["source"] == "learner:cli-explicit"
    LearningService(store).set_consent("student", [], "withdrawn", "learner", utcnow())
    assert ensure_teaching_consent(store, "student", input_fn=lambda prompt: "不") is False
    assert LearningService(store).consent("student")["scopes"] == []
