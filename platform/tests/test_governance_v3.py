"""V3 governance checks only use memory databases and temporary knowledge files."""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.actions import ActionEnvelope, ActionType
from app.core.rules import ActionGovernor, GovernorContext
from app.core.schema import MentalStateSnapshot
from app.gateway.governance_routes import install_governance_routes
from app.governance.authority import AuthorizationDenied, Principal
from app.governance.provenance import GroundingDenied, GroundingVerifier, SourceReference
from app.governance.service import DeletionPolicy, GovernanceService
from app.learning.retrieval import LocalRetrieval, RetrievalContext
from app.learning.service import ConsentDenied, LearningService
from app.storage.db import Store


LEARNER = Principal("student-a", "learner", frozenset({"a"}))
TEACHER = Principal("teacher-a", "teacher", frozenset({"a"}))
OFFICER = Principal("officer-a", "privacy_officer", frozenset({"a"}))


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "lesson.md"
    path.write_text("# Equality\nBoth sides use the same operation.\n", encoding="utf-8")
    retrieval = LocalRetrieval(tmp_path)
    imported = retrieval.import_document(path, source_id="lesson", kc_refs=["KC.EQUALITY"])
    chunk = retrieval.retrieve("same operation", RetrievalContext(kc_refs=["KC.EQUALITY"]))[0]
    ref = SourceReference(source_id=chunk.source_id, source_version=chunk.source_version, chunk_id=chunk.chunk_id)
    verifier = GroundingVerifier(retrieval, known_kcs={"KC.EQUALITY", "KC.SOLVE"},
        reviewed_source_versions={(imported.source_id, imported.source_version)},
        reviewed_source_kcs={(imported.source_id, imported.source_version): frozenset({"KC.EQUALITY"})})
    return path, retrieval, verifier, ref


def context(verifier=None, sink=None):
    return GovernorContext(snapshot=MentalStateSnapshot(learner_id="a"), ladder_pos=0,
        hints_used=0, hint_budget=3, frustration_streak=0,
        grounding_validator=verifier.validate_action if verifier else None, safety_review_sink=sink)


def action():
    return ActionEnvelope.explain("session", "KC.EQUALITY", "concept", "Use the same operation on both sides.")


def test_legacy_templates_remain_compatible_without_strict_context():
    assert ActionGovernor().decide(action(), context()).decision == "allow"


def test_caller_kg_flag_alone_is_not_strict_grounding(source):
    _, _, verifier, _ = source
    result = ActionGovernor().decide(action(), context(verifier))
    assert result.rule_id == "R-06" and result.decision == "deny"


def test_server_bound_action_verifies_source_chunk_kc_and_digest(source):
    _, _, verifier, ref = source
    bound = verifier.bind_action(action(), [ref])
    assert verifier.validate_action(bound) is None
    assert ActionGovernor().decide(bound, context(verifier)).decision == "allow"


@pytest.mark.parametrize("change", ["text", "kc", "actor", "action_id", "receipt"])
def test_receipt_cannot_be_replayed_for_changed_action(source, change):
    _, _, verifier, ref = source
    bound = verifier.bind_action(action(), [ref])
    if change == "text":
        bound.params["text"] = "Unreviewed replacement output"
    elif change == "kc":
        bound.policy_provenance["grounded_to_kg"] = ["KC.SOLVE"]
    elif change == "actor":
        bound.actor_ref = "impersonated"
    elif change == "action_id":
        bound.action_id = "replayed"
    else:
        bound.policy_provenance["grounding_receipt"] = "fake"
    assert verifier.validate_action(bound) is not None


@pytest.mark.parametrize("change", ["file", "missing", "reimport"])
def test_old_receipts_are_rejected_after_source_revision_change(source, change):
    path, retrieval, verifier, ref = source
    bound = verifier.bind_action(action(), [ref])
    if change == "missing":
        path.unlink()
    else:
        path.write_text("Changed source", encoding="utf-8")
        if change == "reimport":
            retrieval.import_document(path, source_id="lesson", kc_refs=["KC.EQUALITY"])
    assert verifier.validate_action(bound) is not None


def test_source_must_have_server_review_not_caller_claim(source):
    _, retrieval, _, ref = source
    unreviewed = GroundingVerifier(retrieval, known_kcs={"KC.EQUALITY"}, reviewed_source_versions=set())
    with pytest.raises(GroundingDenied, match="not_reviewed"):
        unreviewed.bind_action(action(), [ref])


def test_same_file_hash_does_not_approve_reassigned_kc_mapping(source):
    path, retrieval, verifier, ref = source
    bound = verifier.bind_action(action(), [ref])
    retrieval.import_document(path, source_id="lesson", kc_refs=["KC.EQUALITY", "KC.SOLVE"])
    assert verifier.validate_action(bound) == "source_kc_mapping_not_reviewed"


@pytest.mark.parametrize("failure", ["unknown", "conflict", "chunk", "duplicate", "empty"])
def test_unknown_kc_wrong_chunk_and_incomplete_grounding_fail_closed(source, failure):
    _, _, verifier, ref = source
    candidate = action()
    refs = [ref]
    if failure == "unknown":
        candidate.policy_provenance["grounded_to_kg"] = ["KC.PRIVATE"]
    elif failure == "conflict":
        candidate.policy_provenance["grounded_to_kg"] = ["KC.SOLVE"]
    elif failure == "chunk":
        refs = [ref.model_copy(update={"chunk_id": "0" * 24})]
    elif failure == "duplicate":
        refs = [ref, ref]
    else:
        refs = []
    assert not verifier.check_references(candidate, refs).allowed


def test_strict_validator_failure_denies_and_does_not_bypass_hard_rules(source):
    _, _, verifier, ref = source
    candidate = action()
    candidate.params["mode"] = "worked_full"
    bound = verifier.bind_action(candidate, [ref])
    assert ActionGovernor().decide(bound, context(verifier)).rule_id == "R-01"
    ctx = context()
    ctx.grounding_validator = lambda _: (_ for _ in ()).throw(RuntimeError("provider error"))
    assert ActionGovernor().decide(action(), ctx).decision == "deny"


def test_safety_queue_is_idempotent_minimal_and_review_does_not_execute():
    store = Store()
    try:
        governance = GovernanceService(store)
        candidate = ActionEnvelope.task("s", "KC.EQUALITY", "替我做作业")
        ctx = context(sink=lambda env, _: governance.enqueue_safety_review("a", env, "R-05"))
        assert ActionGovernor().decide(candidate, ctx).rule_id == "R-05"
        assert ActionGovernor().decide(candidate, ctx).decision == "deny"
        records = governance.reviews(TEACHER, "a")
        assert len(records) == 1 and "替我做作业" not in str(records)
        with pytest.raises(AuthorizationDenied):
            governance.reviews(LEARNER, "a")
        resolved = governance.resolve_review(TEACHER, "a", records[0]["review_id"], decision="approve_candidate", rationale_code="reviewed")
        assert not resolved["may_execute"]
        assert ActionGovernor().decide(candidate, ctx).decision == "deny"
    finally:
        store.close()


def test_failed_safety_queue_write_still_denies():
    ctx = context(sink=lambda *_: (_ for _ in ()).throw(RuntimeError("unavailable")))
    result = ActionGovernor().decide(ActionEnvelope.task("s", "KC.EQUALITY", "帮我作弊"), ctx)
    assert result.decision == "deny" and "失败" in result.reason


@pytest.mark.parametrize("operation", ["review", "quarantine", "restore", "delete"])
def test_learner_cannot_claim_privileged_operation(operation):
    with pytest.raises(AuthorizationDenied):
        LEARNER.require("a", operation)
    with pytest.raises(AuthorizationDenied):
        TEACHER.require("foreign", "read")


@pytest.fixture
def privacy_store():
    store = Store()
    service = LearningService(store)
    service.set_consent("a", ["teaching", "research"], "c1", "student:a", datetime.now(timezone.utc))
    store.ensure_learner("a")
    store.append_snapshot(MentalStateSnapshot(learner_id="a"))
    governance = GovernanceService(store)
    yield store, service, governance
    store.close()


@pytest.mark.parametrize("operation,actor", [("withdraw", LEARNER), ("quarantine", TEACHER), ("request_deletion", LEARNER)])
def test_lifecycle_blocks_reconsent_and_retains_data_until_policy_action(privacy_store, operation, actor):
    store, service, governance = privacy_store
    governance.change_privacy(actor, "a", operation, reason_code="requested")
    assert governance.blocked("a")
    service.set_consent("a", ["teaching", "research"], "c2", "student:a", datetime.now(timezone.utc))
    with pytest.raises(ConsentDenied):
        service.evidences("a")
    with pytest.raises(ConsentDenied):
        service.evidences("a", purpose="research")
    assert store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1


def test_officer_restore_does_not_restore_old_consent(privacy_store):
    _, service, governance = privacy_store
    governance.change_privacy(LEARNER, "a", "withdraw", reason_code="requested")
    governance.change_privacy(OFFICER, "a", "restore", reason_code="reviewed")
    assert not governance.blocked("a")
    with pytest.raises(ConsentDenied):
        service.evidences("a")


def test_unapproved_deletion_is_dry_run_and_fails_closed(privacy_store):
    store, _, governance = privacy_store
    governance.change_privacy(LEARNER, "a", "request_deletion", reason_code="requested")
    plan = governance.deletion_plan(LEARNER, "a")
    assert plan["dry_run"] and plan["row_counts"]["snapshots"] == 1
    with pytest.raises(AuthorizationDenied):
        governance.execute_isolated_deletion(OFFICER, "a", policy=DeletionPolicy())
    assert store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1


def test_policy_purge_only_isolated_rows_and_cannot_restore(privacy_store):
    store, _, governance = privacy_store
    governance.change_privacy(LEARNER, "a", "request_deletion", reason_code="requested")
    receipt = governance.execute_isolated_deletion(OFFICER, "a", policy=DeletionPolicy("test-policy", True, True))
    assert receipt["state"] == "deleted" and not receipt["compliance_claim"]
    assert store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM consent_records").fetchone()[0] == 0
    with pytest.raises(AuthorizationDenied):
        governance.change_privacy(OFFICER, "a", "restore", reason_code="reviewed")


def test_deletion_inventory_includes_memory_correction_annotations(privacy_store):
    store, _, governance = privacy_store
    store.conn.execute("CREATE TABLE memory_annotations(annotation_id TEXT PRIMARY KEY,learner_id TEXT NOT NULL,payload TEXT NOT NULL)")
    store.conn.execute("INSERT INTO memory_annotations VALUES ('m1','a','{}')")
    store.conn.commit()
    governance.change_privacy(LEARNER, "a", "request_deletion", reason_code="requested")
    assert governance.deletion_plan(LEARNER, "a")["row_counts"]["memory_annotations"] == 1
    governance.execute_isolated_deletion(OFFICER, "a", policy=DeletionPolicy("test-policy", True, True))
    assert store.conn.execute("SELECT COUNT(*) FROM memory_annotations").fetchone()[0] == 0


@pytest.mark.parametrize("blocker", ["shared", "foreign_rows", "foreign_event", "orphan_verdict", "new_table", "digest", "fault"])
def test_deletion_inventory_unknown_shared_or_fault_never_partial_purge(privacy_store, blocker):
    store, _, governance = privacy_store
    governance.change_privacy(LEARNER, "a", "request_deletion", reason_code="requested")
    if blocker == "shared":
        store.ensure_learner("b")
    elif blocker == "foreign_rows":
        store.append_snapshot(MentalStateSnapshot(learner_id="b"))
    elif blocker == "foreign_event":
        from app.core.schema import InteractionEvent
        from app.core.schema import Observation
        store.append_event(InteractionEvent(learner_pseudo_id="b", session_id="foreign", observation=Observation(kind="utterance")))
    elif blocker == "orphan_verdict":
        store.conn.execute("INSERT INTO verdicts VALUES ('v1','missing-session','item','failed',0,'{}')")
        store.conn.commit()
    elif blocker == "new_table":
        store.conn.execute("CREATE TABLE future_personal_data(payload TEXT)")
        store.conn.commit()
    elif blocker == "digest":
        store.conn.execute("INSERT INTO digests(created_at,payload) VALUES ('now','{}')")
        store.conn.commit()
    fault = (lambda: (_ for _ in ()).throw(RuntimeError("injected"))) if blocker == "fault" else None
    with pytest.raises((AuthorizationDenied, RuntimeError)):
        governance.execute_isolated_deletion(OFFICER, "a", policy=DeletionPolicy("test-policy", True, True), fault=fault)
    assert store.conn.execute("SELECT COUNT(*) FROM snapshots WHERE learner_id='a'").fetchone()[0] == 1
    assert governance.privacy(LEARNER, "a")["state"] == "deletion_requested"


@pytest.fixture
def api():
    store = Store()
    app = FastAPI()
    install_governance_routes(app, store, Settings(learner_id="a", local_teacher_token="fixture-only-teacher"))
    @app.get("/api/sample-data")
    def data():
        return {"allowed": True}
    with TestClient(app) as client:
        yield client, store, app.state.governance
    store.close()


def test_api_cannot_spoof_roles_or_cross_assignments(api):
    client, _, _ = api
    assert client.get("/api/governance/privacy/b").status_code == 403
    assert client.get("/api/governance/reviews/a", headers={"X-Role": "teacher"}).status_code == 403
    assert client.get("/api/governance/reviews/a", headers={"Authorization": "Bearer fake"}).status_code == 403
    assert client.get("/api/governance/reviews/a", headers={"Authorization": "Bearer fixture-only-teacher"}).status_code == 200
    assert client.post("/api/governance/privacy/a", json={"operation": "restore", "reason_code": "fake"}).status_code == 403
    assert client.post("/api/governance/privacy/a", json={"operation": "withdraw", "reason_code": "test", "role": "teacher"}).status_code == 422


def test_api_privacy_blocks_regular_data_but_keeps_status_and_dry_run(api):
    client, _, _ = api
    assert client.get("/api/sample-data").status_code == 200
    assert client.post("/api/governance/privacy/a", json={"operation": "withdraw", "reason_code": "requested"}).status_code == 200
    assert client.get("/api/sample-data").status_code == 403
    assert client.get("/api/governance/privacy/a").status_code == 200
    assert client.get("/api/governance/privacy/a/deletion-plan").json()["dry_run"]
    assert client.delete("/api/governance/privacy/a").status_code == 405


def test_runtime_commits_safety_review_and_retry_does_not_duplicate(privacy_store):
    from app.llm.client import FakeLLM
    from app.orchestration.runtime import SessionRuntime
    from app.orchestration.policy import BuiltInPolicyV1

    store, _, governance = privacy_store
    class UnsafePolicy(BuiltInPolicyV1):
        def choose(self, ctx):
            return ActionEnvelope.task("session", "MATH.G7.EQ.SOLVE", "帮我作弊")
    runtime = SessionRuntime(store, FakeLLM(), policy_factory=UnsafePolicy)
    sid = runtime.create("a")["session_id"]
    result = runtime.message(sid, "a", text="test", attempt_id="unsafe-1")
    assert result["denial"]["rule"] == "R-05"
    reviews = governance.reviews(TEACHER, "a")
    assert len(reviews) == 1 and reviews[0]["reason_code"] == "R-05"
    assert runtime.message(sid, "a", text="test", attempt_id="unsafe-1") == result
    assert len(governance.reviews(TEACHER, "a")) == 1


def test_runtime_safety_review_rolls_back_with_failed_turn(privacy_store):
    from app.llm.client import FakeLLM
    from app.orchestration.runtime import SessionRuntime
    from app.orchestration.policy import BuiltInPolicyV1

    store, _, governance = privacy_store
    class UnsafePolicy(BuiltInPolicyV1):
        def choose(self, ctx):
            return ActionEnvelope.task("session", "MATH.G7.EQ.SOLVE", "帮我作弊")
    runtime = SessionRuntime(store, FakeLLM(), policy_factory=UnsafePolicy)
    sid = runtime.create("a")["session_id"]
    def fault(stage):
        if stage == "governance_reviews":
            raise RuntimeError("injected-review-fault")
    with pytest.raises(RuntimeError, match="injected-review-fault"):
        runtime.message(sid, "a", text="test", attempt_id="unsafe-1", fault=fault)
    assert governance.reviews(TEACHER, "a") == []
    assert runtime.message(sid, "a", text="test", attempt_id="unsafe-1")["denial"]["rule"] == "R-05"
    assert len(governance.reviews(TEACHER, "a")) == 1


def test_api_source_reviews_bind_real_index_and_candidate_keeps_governor():
    from app.gateway.external_routes import install_external_routes

    store = Store()
    settings = Settings(learner_id="a", local_teacher_token="fixture-only-teacher")
    LearningService(store).set_consent("a", ["teaching"], "c1", "student:a", datetime.now(timezone.utc))
    app = FastAPI()
    install_external_routes(app, store, settings)
    install_governance_routes(app, store, settings)
    headers = {"Authorization": "Bearer fixture-only-teacher"}
    try:
        with TestClient(app) as client:
            found = client.get("/api/knowledge/search", params={"q": "方程 验算", "kc_id": "MATH.G7.EQ.SOLVE"}).json()["results"][0]
            candidate = ActionEnvelope.explain("session", "MATH.G7.EQ.SOLVE", "concept", found["text"])
            ref = {key: found[key] for key in ("source_id", "source_version", "chunk_id")}
            body = {"action": candidate.model_dump(mode="json"), "references": [ref]}
            assert not client.post("/api/governance/provenance/a/check", json=body).json()["allowed"]
            review_url = f'/api/governance/sources/a/{found["source_id"]}/review'
            review = dict(source_version=found["source_version"], decision="approve", rationale_code="reviewed")
            assert client.post(review_url, json=review).status_code == 403
            assert client.post(review_url, json=review, headers=headers).status_code == 200
            result = client.post("/api/governance/provenance/a/check", json=body).json()
            assert result["allowed"] and result["candidate_only"] and not result["executed"]
            assert "grounding_receipt" not in str(result)
            body["action"]["params"]["mode"] = "worked_full"
            assert client.post("/api/governance/provenance/a/check", json=body).json()["rule_id"] == "R-01"
            review["decision"] = "reject"
            assert client.post(review_url, json=review, headers=headers).status_code == 200
            assert not client.post("/api/governance/provenance/a/check", json=body).json()["allowed"]
            LearningService(store).set_consent("a", [], "withdrawn", "student:a", datetime.now(timezone.utc))
            assert client.post("/api/governance/provenance/a/check", json=body).status_code == 403
    finally:
        store.close()


def test_real_gateway_governance_integrates_with_regular_sessions_and_auth(privacy_store):
    from app.gateway.routes import create_app
    from app.llm.client import FakeLLM
    from app.gateway.security import COOKIE, hash_password

    store, service, _ = privacy_store
    settings = Settings(learner_id="a", local_auth_enabled=True,
        local_auth_password_hash=hash_password("test-only-secret"), local_teacher_token="test-only-token")
    app = create_app(settings, llm=FakeLLM(), store=store)
    with TestClient(app) as client:
        assert client.get("/api/governance/privacy/a").status_code == 401
        assert client.post("/api/auth/login", json={"password": "test-only-secret"}).status_code == 200
        assert client.cookies.get(COOKIE)
        initial = client.post("/api/sessions", json={"session_type": "explore"})
        assert initial.status_code == 200
        sid = initial.json()["session_id"]
        assert client.get(f"/api/sessions/{sid}").status_code == 200
        # A teacher token does not bypass the original login boundary.
        assert client.get("/api/governance/reviews/a", headers={"Authorization": "Bearer test-only-token"}).status_code == 200
        assert client.post("/api/governance/privacy/a", json={"operation": "request_deletion", "reason_code": "requested"}).status_code == 200
        service.set_consent("a", ["teaching"], "new-consent", "student:a", datetime.now(timezone.utc))
        assert client.get(f"/api/sessions/{sid}").status_code == 403
        assert client.get("/api/learning/evidence/a").status_code == 403
        assert client.post("/api/contracts", json={"goal_text": "Do not bypass privacy"}).status_code == 403
        assert client.get("/api/governance/privacy/a/deletion-plan").status_code == 200
        assert client.get("/api/governance/privacy/b").status_code == 403
        assert client.get("/api/auth/status").status_code == 200
