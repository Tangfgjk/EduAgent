from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.gateway.routes import create_app
from app.learning.decisions import recommend
from app.learning.service import LearningService
from app.llm.client import FakeLLM
from app.storage.db import Store
from tests.test_learning_storage import evidence, service, NOW


@pytest.fixture
def client():
    store = Store()
    with TestClient(create_app(Settings(learner_id="s1"),llm=FakeLLM(),store=store)) as client:
        assert client.post("/api/learning/consent/s1", json={"scopes": ["teaching"],
                           "version": "test-teaching-v1", "source": "learner:test"}).status_code == 200
        yield client,store
    store.close()


def test_plan_accept_draft_sign_modify_and_foreign_learner(client):
    c,store = client
    initial = c.post("/api/contracts",json={"goal_text":"独立解方程"}).json()
    assert initial["plan_version"]["status"] == "draft"
    assert initial["plan_version"]["confirmed_at"] is None
    recommendation = c.get("/api/path/recommend").json()
    assert recommendation["status"] == "proposed"
    draft = c.post("/api/path/accept",json={"learner_id":"s1","path":{"status":"confirmed"}}).json()
    assert draft["status"] == "draft"
    id1 = draft["version_id"]
    assert c.post(f"/api/plans/{id1}/sign",json={"learner_id":"foreign"}).status_code == 403
    signed = c.post(f"/api/plans/{id1}/sign",json={"learner_id":"s1"}).json()
    assert signed["status"] == "confirmed" and signed["signed_by"] == "s1"
    draft2 = c.post("/api/path/accept",json={"learner_id":"s1","path":{}}).json()
    modified = c.post(f'/api/plans/{draft2["version_id"]}/modify',json={"learner_id":"s1","content":{"daily_count":3}}).json()
    assert modified["status"] == "draft" and modified["prior_version_id"] == id1
    assert c.get(f"/api/plans/{id1}").json()["status"] == "confirmed"
    rejected = c.post(f'/api/plans/{modified["version_id"]}/reject',json={"learner_id":"s1"}).json()
    assert rejected["status"] == "rejected"
    assert c.get(f"/api/plans/{id1}").json()["status"] == "confirmed"


def test_session_stable_attempt_receipt_and_revoked_old_views(client):
    c,store = client
    c.post("/api/contracts",json={"goal_text":"独立解方程"})
    sid = c.post("/api/sessions",json={}).json()["session_id"]
    body = dict(answer="3",attempt_id="answer1")
    first = c.post(f"/api/sessions/{sid}/messages",json=body)
    assert first.status_code == 200
    assert c.post(f"/api/sessions/{sid}/messages",json=body).json() == first.json()
    assert c.post(f"/api/sessions/{sid}/messages",json=body|{"answer":"4"}).status_code == 409
    assert len(LearningService(store).evidences("s1")) == 1
    c.delete("/api/learning/consent/s1")
    for path in ["/api/mirror/s1","/api/evidence/s1","/api/events","/api/path/recommend"]:
        assert c.get(path).status_code == 403
    assert c.post(f"/api/sessions/{sid}/messages",json={"answer":"3","attempt_id":"answer2"}).status_code == 403
    assert c.post(f"/api/sessions/{sid}/messages",json=body).status_code == 403
    assert c.post(f"/api/sessions/{sid}/reflection",json={}).status_code == 403
    assert c.post("/api/evolution/digest").status_code == 403


def test_golden_path_failure_hint_independent_mastery_due_review_new_path():
    s = service()
    graph = {"MATH.G7.EQ.SOLVE": []}
    s.consume(evidence("fail",verdict_status="failed",score=0))
    s.consume(evidence("assisted",hint_level=1,assistance_mode="hint"))
    assert s.masteries("s1")[0].effective_evidence_count == 1
    for i in range(7):
        s.consume(evidence(f"independent{i}",assessment_id=f"task{i}",occurred_at=NOW+timedelta(minutes=i)))
    mastered = s.masteries("s1")[0]
    first = recommend(s,"s1",NOW+timedelta(minutes=7),prerequisites=graph)
    assert first["nodes"][0]["gate"]["status"] == "mastered"
    assert first["nodes"][0]["decision"] == "ADVANCE"
    later = NOW+timedelta(days=20)
    before_review = recommend(s,"s1",later,prerequisites=graph)
    assert before_review["nodes"][0]["decision"] == "REVIEW"
    old_retention = s.retentions("s1")[0]
    s.consume(evidence("due-review",occurred_at=later))
    new_retention = s.retentions("s1")[0]
    after_review = recommend(s,"s1",later,prerequisites=graph)
    assert new_retention.state_version == old_retention.state_version+1
    assert new_retention.next_review_at > later
    assert after_review["nodes"][0]["decision"] == "ADVANCE"
    assert after_review["nodes"][0]["state_version"] > mastered.state_version
    replayed = s.rebuild("s1",as_of=later)
    assert replayed["mastery"][0]["p_mastery"] == s.masteries("s1")[0].p_mastery
    assert replayed["retention"][0]["stability"] == new_retention.stability


def test_old_event_migration_full_order(tmp_path):
    import sqlite3
    from app.core.schema import InteractionEvent, Observation
    db = str(tmp_path/"legacy.sqlite3")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE events(event_id TEXT PRIMARY KEY,learner_pseudo_id TEXT,session_id TEXT,ts TEXT,payload TEXT)")
    for i in range(2):
        e = InteractionEvent(event_id=f"old{i}",learner_pseudo_id="s1",ts=NOW,observation=Observation(kind="answer"))
        conn.execute("INSERT INTO events VALUES (?,?,?,?,?)",(e.event_id,"s1","session",NOW.isoformat(),e.model_dump_json()))
    conn.commit()
    conn.close()
    store = Store(db)
    assert [e.event_id for e in store.events_for_learner("s1")] == ["old0","old1"]
    assert [r[0] for r in store.conn.execute("SELECT event_seq FROM events ORDER BY rowid")] == [1,2]
    store.close()


def test_historical_recommendation_does_not_consume_future_state():
    s = service()
    s.consume(evidence("future",occurred_at=NOW+timedelta(minutes=2)))
    result = recommend(s,"s1",NOW,prerequisites={"MATH.G7.EQ.SOLVE":[]})
    assert result["nodes"][0]["decision"] == "DIAGNOSE"
    assert result["nodes"][0]["evidence_refs"] == []
    assert result["nodes"][0]["retention_version"] is None


def test_consent_retry_preserves_original_timestamp():
    s = service()
    original = s.consent("s1")
    repeated = s.set_consent("s1",["teaching"],"c1","student:local",NOW+timedelta(minutes=1))
    assert repeated == original


def test_repeating_one_item_cannot_satisfy_mastery_gate():
    s = service()
    for i in range(10):
        s.consume(evidence(f"repeat{i}",assessment_id="same-item"))
    result = recommend(s,"s1",NOW,prerequisites={"MATH.G7.EQ.SOLVE":[]})
    assert s.masteries("s1")[0].p_mastery > .9
    assert result["nodes"][0]["gate"]["status"] == "insufficient_evidence"
