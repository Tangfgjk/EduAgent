from datetime import timedelta
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.gateway.extended_routes import install_extended_routes
from app.learning.service import LearningService
from app.storage.db import Store
from tests.test_learning_storage import NOW


@pytest.fixture
def api():
    store=Store(); service=LearningService(store)
    service.set_consent("s",["teaching"],"v1","learner",NOW)
    app=FastAPI(); install_extended_routes(app,store,Settings(learner_id="s"))
    with TestClient(app) as client: yield client,store,service
    store.close()


def test_memory_and_development_metrics_require_auth_and_source_refs(api):
    client,store,service=api
    assert client.post("/api/learning/memory/other/rebuild").status_code==403
    response=client.post("/api/learning/memory/s/rebuild",params={"as_of":NOW.isoformat()})
    assert response.status_code==200 and response.json()["l1"]=={"evidence_refs":[],"event_refs":[]}
    view=response.json()["view_id"]
    fetched=client.get(f"/api/learning/memory/s/{view}")
    assert fetched.status_code==200 and fetched.json()["version"]=="source-linked-memory-v1"
    metrics=client.get("/api/learning/metrics/development/s",params={"split_at":(NOW-timedelta(days=1)).isoformat(),"as_of":NOW.isoformat()})
    assert metrics.status_code==200 and metrics.json()["not_an_effect_claim"]
    service.set_consent("s",[],"withdraw","learner",NOW)
    assert client.get(f"/api/learning/memory/s/{view}").status_code==403


def test_appeal_and_recovery_unknown_are_authorized(api):
    client,_,_=api
    assert client.post("/api/learning/appeals/s",json={"evidence_id":"missing","reason":"please review"}).status_code==404
    assert client.post("/api/learning/recovery/s",json={"correction_id":"missing"}).status_code==404
    assert client.post("/api/learning/appeals/other",json={"evidence_id":"x","reason":"x"}).status_code==403
