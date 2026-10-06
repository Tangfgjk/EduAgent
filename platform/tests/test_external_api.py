"""External adapters integrated with local authority and auditable sources."""
import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.gateway.external_routes import install_external_routes
from app.learning.schema import LearningEvidence
from app.learning.service import LearningService
from app.storage.db import Store


@pytest.fixture
def workspace():
    store = Store()
    service = LearningService(store)
    now = datetime.now(timezone.utc)
    service.set_consent("local", ["teaching"], "c1", "student:local", now)
    evidence = LearningEvidence(evidence_id="e1", learner_id="local", session_id="session",
        kc_refs=["MATH.G7.EQ.SOLVE"], attempt_id="a1", artifact_ref="artifact", verdict_ref="verdict",
        verdict_status="failed", verifier_version="symbolic-v1", confidence=1, occurred_at=now,
        consent_scope=["teaching"], consent_version="c1", authorization_source="student:local")
    service.consume(evidence)
    app = FastAPI()
    install_external_routes(app, store, Settings(learner_id="local", llm_api_key="do-not-leak"))
    with TestClient(app) as client:
        yield client, store, service
    store.close()


def test_knowledge_search_returns_versioned_candidate_with_openable_citation(workspace):
    client, store, service = workspace
    response = client.get("/api/knowledge/search", params={"q": "方程 验算", "kc_id": "MATH.G7.EQ.SOLVE"})
    assert response.status_code == 200
    data = response.json()
    assert data["candidate_only"] and not data["verified_teaching_claim"]
    assert data["results"]
    result = data["results"][0]
    assert result["source_version"] and result["citation"]["line_start"]
    source = client.get(result["source_url"])
    assert source.status_code == 200 and result["text"] in source.text
    assert result["teaching_decision"] == "requires_verifier_and_governor"


def test_knowledge_unknown_kc_rejected(workspace):
    client, _, _ = workspace
    response = client.get("/api/knowledge/search", params={"q": "equation", "kc_id": "PRIVATE.UNKNOWN"})
    assert response.status_code == 403


def test_source_cannot_read_unindexed_path(workspace):
    client, _, _ = workspace
    assert client.get("/api/knowledge/source/private").status_code == 404
    assert client.get("/api/knowledge/source/..%2F..%2F.env").status_code in {404, 422}


@pytest.mark.parametrize("endpoint", ["/api/knowledge/search?q=方程&kc_id=MATH.G7.EQ.SOLVE", "/api/knowledge/source/equation-solve-v1", "/api/providers"])
def test_external_reads_blocked_after_withdrawal(workspace, endpoint):
    client, _, service = workspace
    service.set_consent("local", [], "c2", "student:local", datetime.now(timezone.utc))
    assert client.get(endpoint).status_code == 403


def test_local_id_cannot_be_overridden(workspace):
    client, _, _ = workspace
    assert client.get("/api/providers", params={"learner_id": "foreign"}).status_code == 403


@pytest.mark.parametrize("role", ["prompter", "skeptic", "reviewer"])
def test_role_report_uses_existing_evidence_and_persists_audit(workspace, role):
    client, store, _ = workspace
    response = client.post(f"/api/roles/{role}/review", json={"evidence_ids": ["e1"], "student_work": "x=8"})
    assert response.status_code == 200
    data = response.json()
    assert data["execution_mode"] == "deterministic_demo"
    assert data["external_model_smoke"] == "not_executed"
    assert data["report"]["evidence_refs"] == ["e1"]
    assert data["report"]["actions"] and data["report"]["reviews"]
    assert data["report"]["actions"][0]["policy_provenance"]["provider_id"] == "offline"
    assert data["provenance"]["provider_id"] == "offline"
    audit = json.loads(store.conn.execute("SELECT payload FROM learning_audit ORDER BY rowid DESC LIMIT 1").fetchone()[0])
    assert audit["report_id"] == data["report_id"] and audit["evidence_refs"] == ["e1"]
    assert audit["source_refs"] and audit["report"]["reviews"][0]["decision"] == "allow"


def test_role_does_not_modify_learner_state(workspace):
    client, _, service = workspace
    before = service.masteries("local")
    client.post("/api/roles/reviewer/review", json={"evidence_ids": ["e1"], "student_work": "x"})
    assert service.masteries("local") == before


def test_role_budget_exhaustion_audited_without_actions(workspace):
    client, store, _ = workspace
    response = client.post("/api/roles/skeptic/review", json={"evidence_ids": ["e1"], "max_tokens": 1})
    assert response.status_code == 200
    assert response.json()["report"]["exit_status"] == "budget_exhausted"
    assert response.json()["report"]["actions"] == []
    assert "budget_exhausted" in store.conn.execute("SELECT payload FROM learning_audit ORDER BY rowid DESC LIMIT 1").fetchone()[0]


def test_role_cannot_reference_unknown_evidence_or_expand_tools(workspace):
    client, _, _ = workspace
    assert client.post("/api/roles/skeptic/review", json={"evidence_ids": ["foreign"]}).status_code == 403
    assert client.post("/api/roles/skeptic/review", json={"evidence_ids": ["e1"], "tools": ["shell"]}).status_code == 422
    assert client.post("/api/roles/skeptic/review", json={"evidence_ids": ["e1"], "max_turns": 2}).status_code == 422


def test_role_withdrawal_denies_before_provider(workspace):
    client, store, service = workspace
    service.set_consent("local", [], "c2", "student:local", datetime.now(timezone.utc))
    before = store.conn.execute("SELECT COUNT(*) FROM learning_audit").fetchone()[0]
    assert client.post("/api/roles/skeptic/review", json={"evidence_ids": ["e1"]}).status_code == 403
    assert store.conn.execute("SELECT COUNT(*) FROM learning_audit").fetchone()[0] == before


def test_provider_configuration_has_no_secret_or_false_smoke_claim(workspace):
    client, _, _ = workspace
    response = client.get("/api/providers")
    assert response.status_code == 200 and "do-not-leak" not in response.text
    data = response.json()
    assert data["external_model_smoke"] == "not_executed"
    assert {provider["provider_id"] for provider in data["providers"]} == {"offline", "configured"}


def test_role_withdrawal_during_generation_discards_report(workspace, monkeypatch):
    from app.gateway.external_routes import DerivedRunner
    client, store, service = workspace
    original = DerivedRunner.run
    def withdraw_then_return(runner, *args, **kwargs):
        result = original(runner, *args, **kwargs)
        service.set_consent("local", [], "c2", "student:local", datetime.now(timezone.utc))
        return result
    monkeypatch.setattr(DerivedRunner, "run", withdraw_then_return)
    before = store.conn.execute("SELECT COUNT(*) FROM learning_audit").fetchone()[0]
    assert client.post("/api/roles/skeptic/review", json={"evidence_ids": ["e1"]}).status_code == 403
    assert store.conn.execute("SELECT COUNT(*) FROM learning_audit").fetchone()[0] == before


def test_role_rejects_evidence_kc_outside_catalog(workspace):
    client, _, service = workspace
    stored = service.evidences("local")[0]
    service.consume(stored.model_copy(update={"evidence_id": "outside", "attempt_id": "outside",
                       "kc_refs": ["SCIENCE.G7.UNKNOWN"]}))
    assert client.post("/api/roles/skeptic/review", json={"evidence_ids": ["outside"]}).status_code == 403


def test_roles_share_proactive_hint_budget_across_reports(workspace):
    client, _, _ = workspace
    reports = [client.post("/api/roles/prompter/review", json={"evidence_ids": ["e1"]}).json() for _ in range(4)]
    assert reports[0]["report"]["exit_status"] == "report_ready"
    assert reports[-1]["report"]["exit_status"] == "constraint_violation"
    assert reports[-1]["report"]["reviews"][0]["rule_id"] == "R-07"
    assert reports[-1]["governor_budget_scope"] == "learner_day"


def test_roundtable_returns_three_governed_candidate_reports(workspace):
    client, _, service = workspace
    before = service.masteries("local")
    response = client.post("/api/roundtable/review", json={"evidence_ids": ["e1"], "student_work": "x=8"})
    assert response.status_code == 200
    assert {item["report"]["role"] for item in response.json()["reports"]} == {"prompter", "skeptic", "reviewer"}
    assert response.json()["candidate_only"] and response.json()["no_learning_state_write"]
    assert service.masteries("local") == before


def test_knowledge_import_rejects_absolute_escape_and_unknown_kc(workspace):
    client, _, _ = workspace
    assert client.post("/api/knowledge/import", json={"relative_path": "../../.env", "source_id": "bad", "kc_refs": ["MATH.G7.EQ.SOLVE"]}).status_code == 403
    assert client.post("/api/knowledge/import", json={"relative_path": "lesson.md", "source_id": "bad", "kc_refs": ["PRIVATE.UNKNOWN"]}).status_code == 403


def test_hybrid_search_explicit_feature_vector_version(workspace):
    client, _, _ = workspace
    response = client.get("/api/knowledge/search", params={"q": "方程", "kc_id": "MATH.G7.EQ.SOLVE", "mode": "hybrid"})
    assert response.status_code == 200
    assert response.json()["results"][0]["retrieval_version"] == "bm25-hashed-ngram-rrf-v1"
    assert response.json()["vector_model"] == "hashed_lexical_features_not_semantic_embeddings"


def test_configured_role_uses_http_client_with_provenance(workspace, monkeypatch):
    import httpx
    client, _, _ = workspace
    calls = []
    original = httpx.Client.post
    def post(real_client, url, *, json, **kwargs):
        if url != "/chat/completions":
            return original(real_client, url, json=json, **kwargs)
        calls.append(json["model"])
        payload = '{"actions": [{"kind": "question", "text": "Can you check both sides?"}]}'
        return httpx.Response(200, json={"choices": [{"message": {"content": payload}}]}, request=httpx.Request("POST", "https://example.com"))
    monkeypatch.setattr(httpx.Client, "post", post)
    response = client.post("/api/roles/skeptic/review", json={"evidence_ids": ["e1"], "provider_id": "configured"})
    assert response.status_code == 200
    assert calls and response.json()["provenance"]["provider_id"] == "configured"
    assert response.json()["execution_mode"] == "configured_provider"
    assert response.json()["external_model_smoke"] == "not_executed"


def test_strategy_and_teach_endpoint_keep_evidence_and_zero_trust(workspace):
    client, store, service = workspace
    strategy = client.get("/api/strategies", params={"kc_id":"MATH.G7.EQ.SOLVE"})
    assert strategy.status_code == 200 and strategy.json()["cards"]
    assert strategy.json()["cards"][0]["calibrated"] is False
    before = service.masteries("local")
    response = client.post("/api/teach-student/lesson",json={"evidence_ids":["e1"],"lesson":"我解释两边同时运算"})
    assert response.status_code == 200
    data = response.json()
    assert data["report"]["trust_weight"] == 0 and data["report"]["candidate_only"]
    assert data["report"]["artifact_refs"] == ["artifact"]
    assert data["report"]["actions"][0]["policy_provenance"]["provider_id"] == "offline"
    assert service.masteries("local") == before
    assert "teach_virtual_student_report" in store.conn.execute("SELECT payload FROM learning_audit ORDER BY rowid DESC LIMIT 1").fetchone()[0]


def test_teach_requires_current_authorized_evidence_and_shared_budget(workspace):
    client, _, service = workspace
    assert client.post("/api/teach-student/lesson",json={"evidence_ids":["foreign"],"lesson":"lesson"}).status_code == 403
    response = client.post("/api/teach-student/lesson",json={"evidence_ids":["e1"],"lesson":"lesson","max_tokens":1})
    assert response.json()["report"]["exit_status"] == "budget_exhausted"
    service.set_consent("local",[],"c2","student:local",datetime.now(timezone.utc))
    assert client.get("/api/strategies",params={"kc_id":"MATH.G7.EQ.SOLVE"}).status_code == 403
    assert client.post("/api/teach-student/lesson",json={"evidence_ids":["e1"],"lesson":"lesson"}).status_code == 403
