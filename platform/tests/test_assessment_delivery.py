from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.gateway.learning_routes import install_learning_routes
from app.learning.assessment_delivery import AssessmentDelivery
from app.learning.service import LearningService
from app.storage.db import Store

BASE = datetime(2026, 10, 6, 8, tzinfo=timezone.utc)


@pytest.fixture
def api():
    store = Store(":memory:")
    app = FastAPI()
    install_learning_routes(app, store, Settings(learner_id="s", allow_simulated_time=True, assessment_require_ticket=True))
    with TestClient(app) as client:
        assert client.post("/api/learning/consent/s", json=dict(scopes=["teaching"], version="v1", source="learner")).status_code == 200
        yield client, store
    store.close()


def issue(client, identity="i", item="PRE-SOLVE-1", at=BASE):
    return client.post("/api/learning/assessment/s/issue", json=dict(
        assessment_id=item, assessment_version="1.0.0", issuance_id=identity, occurred_at=at.isoformat()))


def answer(ticket=None, attempt="a", at=BASE, **kwargs):
    return dict(assessment_id="PRE-SOLVE-1", assessment_version="1.0.0", attempt_id=attempt,
        answer="4", occurred_at=at.isoformat(), delivery_ref=ticket, **kwargs)


def test_strict_delivery_hidden_stem_and_atomic_stable_receipt(api):
    client, store = api
    assert all("stem" not in a and "answer" not in a for a in client.get("/api/learning/assets").json()["assessments"])
    assert client.post("/api/learning/assessment/s/submit", json=answer()).status_code == 409
    delivered = issue(client).json()
    assert "assessment_sha256" not in delivered
    assert delivered["assessment"]["stem"] and "answer" not in delivered["assessment"]
    assert issue(client).json() == delivered
    assert issue(client, item="PRE-SOLVE-2").status_code == 409
    body = answer(delivered["delivery_ref"])
    first = client.post("/api/learning/assessment/s/submit", json=body)
    assert first.status_code == 200
    assert client.post("/api/learning/assessment/s/submit", json=body).json() == first.json()
    assert client.post("/api/learning/assessment/s/submit", json=answer(delivered["delivery_ref"], attempt="b")).status_code == 409
    record = client.get("/api/learning/evidence/s").json()[0]
    assert record["verifier_details"]["independence_verified"] is True
    assert store.conn.execute("SELECT COUNT(*) FROM learning_api_receipts").fetchone()[0] == 1


def test_hidden_hint_and_repeated_exposure_cannot_be_independent(api):
    client, _ = api
    ticket = issue(client).json()["delivery_ref"]
    hint = dict(delivery_ref=ticket, assessment_id="PRE-SOLVE-1", level=2, occurred_at=BASE.isoformat())
    assert client.post("/api/learning/assessment/s/hint", json=hint).status_code == 200
    hint["level"] = 1
    assert client.post("/api/learning/assessment/s/hint", json=hint).json()["hint_level"] == 2
    assert client.post("/api/learning/assessment/s/submit", json=answer(ticket)).status_code == 200
    evidence = client.get("/api/learning/evidence/s").json()[0]
    assert evidence["hint_level"] == 2 and evidence["assistance_mode"] == "hint"
    assert not evidence["verifier_details"]["independence_verified"]
    again = issue(client, "repeat").json()
    assert not again["first_exposure"] and again["attempt_number"] == 2
    assert client.post("/api/learning/assessment/s/submit", json=answer(again["delivery_ref"], attempt="b")).status_code == 200
    assert not client.get("/api/learning/evidence/s").json()[-1]["verifier_details"]["independence_verified"]


def test_binding_expiry_backdating_and_consent_change(api):
    client, store = api
    ticket = issue(client).json()["delivery_ref"]
    for changed in (answer("unknown"), answer(ticket, at=BASE-timedelta(seconds=1)),
                    answer(ticket, at=BASE+timedelta(days=2))):
        assert client.post("/api/learning/assessment/s/submit", json=changed).status_code == 409
    assert client.post("/api/learning/assessment/other/issue", json=dict(assessment_id="PRE-SOLVE-1", issuance_id="x")).status_code == 403
    assert client.post("/api/learning/consent/s", json=dict(scopes=["teaching"], version="v2", source="learner")).status_code == 200
    assert client.post("/api/learning/assessment/s/submit", json=answer(ticket)).status_code == 409
    assert store.conn.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0] == 0


def test_rollback_does_not_consume_ticket(api, monkeypatch):
    client, store = api
    ticket = issue(client).json()["delivery_ref"]
    original = AssessmentDelivery.consume_in_transaction
    def fail(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("injected failure")
    monkeypatch.setattr(AssessmentDelivery, "consume_in_transaction", fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        client.post("/api/learning/assessment/s/submit", json=answer(ticket))
    assert store.conn.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM learning_api_receipts").fetchone()[0] == 0
    monkeypatch.setattr(AssessmentDelivery, "consume_in_transaction", original)
    assert client.post("/api/learning/assessment/s/submit", json=answer(ticket)).status_code == 200


def test_legacy_client_reports_are_not_measurements():
    store = Store(":memory:")
    app = FastAPI()
    install_learning_routes(app, store, Settings(learner_id="s", allow_simulated_time=True))
    with TestClient(app) as client:
        client.post("/api/learning/consent/s", json=dict(scopes=["teaching"], version="v1", source="learner"))
        assert client.post("/api/learning/assessment/s/submit", json=answer()).status_code == 200
        assert LearningService(store).evidences("s")[0].verifier_details["independence_verified"] is False
        assert client.get("/api/learning/metrics/s").json()["status"] == "insufficient_data"
    store.close()


def test_loaded_main_settings_default_to_strict(tmp_path, monkeypatch):
    monkeypatch.delenv("RSI_ASSESSMENT_REQUIRE_TICKET", raising=False)
    assert Settings.load(tmp_path / ".env").assessment_require_ticket


def test_new_hint_between_verification_and_commit_rolls_back_evidence(api, monkeypatch):
    client, store = api
    ticket = issue(client).json()["delivery_ref"]
    original = LearningService.consume
    def help_then_consume(self, evidence, **kwargs):
        from app.learning.assets import load_catalog
        asset = next(a for a in load_catalog().assessments if a.ref.asset_id == "PRE-SOLVE-1")
        AssessmentDelivery(store).hint("s", ticket, asset, BASE, "v1", 1)
        return original(self, evidence, **kwargs)
    monkeypatch.setattr(LearningService, "consume", help_then_consume)
    assert client.post("/api/learning/assessment/s/submit", json=answer(ticket)).status_code == 409
    assert store.conn.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM learning_api_receipts").fetchone()[0] == 0
    monkeypatch.setattr(LearningService, "consume", original)
    assert client.post("/api/learning/assessment/s/submit", json=answer(ticket)).status_code == 200
    assert client.get("/api/learning/evidence/s").json()[0]["hint_level"] == 1


def test_concurrent_file_connections_cannot_both_issue_first_exposure(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from app.learning.assets import load_catalog
    filename = str(tmp_path / "race.sqlite3")
    initial = Store(filename)
    LearningService(initial).set_consent("s", ["teaching"], "v1", "learner", BASE)
    AssessmentDelivery(initial)
    initial.close()
    asset = next(a for a in load_catalog().assessments if a.ref.asset_id == "PRE-SOLVE-1")
    def deliver(index):
        store = Store(filename)
        try:
            return AssessmentDelivery(store).issue("s", asset, str(index), BASE, BASE, "v1")
        finally:
            store.close()
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(deliver, range(2)))
    assert sum(r["first_exposure"] for r in results) == 1
    assert sorted(r["attempt_number"] for r in results) == [1, 2]


def test_same_version_changed_content_rejects_old_ticket(api):
    from app.learning.assets import load_catalog
    from app.learning.service import EvidenceConflict
    client, store = api
    ticket = issue(client).json()["delivery_ref"]
    asset = next(a for a in load_catalog().assessments if a.ref.asset_id == "PRE-SOLVE-1")
    tampered = asset.model_copy(update={"stem": "Different task without version increment"})
    with pytest.raises(EvidenceConflict, match="mismatch"):
        AssessmentDelivery(store).get("s", ticket, tampered, BASE, "v1")
