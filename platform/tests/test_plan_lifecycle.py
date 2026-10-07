"""Learner-owned lifecycle, audit, rollback and concurrent signing checks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json

import pytest

from app.core.schema import GoalContract, GoalStatement, PlanVersion
from app.learning.plans import PlanConflict, PlanService
from app.storage.db import Store

NOW = datetime(2026, 10, 6, 6, tzinfo=timezone.utc)


def setup(store=None):
    store = store or Store()
    contract = GoalContract(learner_id="learner", goal_statement=GoalStatement(text="学会方程"))
    store.save_contract(contract)
    return store, PlanService(store), contract


def test_accept_creates_draft_and_only_signing_activates():
    store, service, contract = setup()
    proposed = service.propose("learner", contract.goal_contract_id, {"kc_refs": ["equations"]}, NOW)
    assert proposed.status == "proposed"
    assert service.active("learner", contract.goal_contract_id) is None
    draft = service.accept("learner", proposed.version_id, NOW)
    assert draft.status == "draft" and draft.confirmed_at is None
    assert draft.diff == {"kc_refs": {"before": None, "after": ["equations"]}}
    assert service.active("learner", contract.goal_contract_id) is None
    signed = service.sign("learner", draft.version_id, NOW)
    assert signed.status == "confirmed" and signed.signed_by == "learner"
    assert service.active("learner", contract.goal_contract_id) == signed
    count = store.conn.execute("SELECT COUNT(*) FROM learning_audit").fetchone()[0]
    assert service.sign("learner", signed.version_id, NOW) == signed
    assert store.conn.execute("SELECT COUNT(*) FROM learning_audit").fetchone()[0] == count


def test_modification_is_new_draft_and_previous_active_is_superseded_only_on_signature():
    store, service, contract = setup()
    first = service.propose("learner", contract.goal_contract_id, {"days": 3, "kc_refs": ["a"]}, NOW)
    proposed_first_id = first.version_id
    first = service.accept("learner", first.version_id, NOW)
    assert first.version_id != proposed_first_id
    assert service.get("learner", proposed_first_id).status == "superseded"
    first = service.sign("learner", first.version_id, NOW)
    second = service.propose("learner", contract.goal_contract_id, {"days": 5, "kc_refs": ["a"]}, NOW)
    second = service.accept("learner", second.version_id, NOW)
    revised = service.modify("learner", second.version_id, {"days": 4, "kc_refs": ["b"]}, "学习者调整", NOW)
    assert revised.version_id != second.version_id
    assert revised.prior_version_id == first.version_id
    assert revised.diff["days"] == {"before": 3, "after": 4}
    assert service.active("learner", contract.goal_contract_id) == first
    assert service.get("learner", second.version_id).status == "superseded"
    service.sign("learner", revised.version_id, NOW)
    assert service.get("learner", first.version_id).status == "superseded"
    logs = [json.loads(row[0]) for row in store.conn.execute("SELECT payload FROM learning_audit")]
    assert {"plan_modified", "plan_signed", "plan_superseded"} <= {log["action"] for log in logs}


def test_confirmed_plan_is_immutable_and_revision_keeps_it_active_until_resigned():
    store, service, contract = setup()
    draft = service.accept("learner", service.propose(
        "learner", contract.goal_contract_id, {"cadence": "每天两题"}, NOW
    ).version_id, NOW)
    confirmed = service.sign("learner", draft.version_id, NOW)
    revision = service.modify("learner", confirmed.version_id,
                              {"cadence": "每天一题，周末复习"}, "调整学习负荷", NOW)
    assert revision.status == "draft"
    assert revision.prior_version_id == confirmed.version_id
    assert service.get("learner", confirmed.version_id).status == "confirmed"
    assert service.active("learner", contract.goal_contract_id).version_id == confirmed.version_id
    signed = service.sign("learner", revision.version_id, NOW)
    assert signed.status == "confirmed"
    assert service.get("learner", confirmed.version_id).status == "superseded"


def test_rejection_cannot_activate_and_cross_learner_access_is_denied():
    store, service, contract = setup()
    proposal = service.propose("learner", contract.goal_contract_id, {}, NOW)
    rejected = service.reject("learner", proposal.version_id, "调整目标", NOW)
    assert rejected.status == "rejected"
    with pytest.raises(PlanConflict):
        service.sign("learner", proposal.version_id, NOW)
    with pytest.raises(PermissionError):
        service.get("other", proposal.version_id)
    with pytest.raises(PermissionError):
        service.propose("other", contract.goal_contract_id, {}, NOW)
    assert service.active("learner", contract.goal_contract_id) is None


def test_proposal_cannot_be_signed_and_signature_transaction_rolls_back_on_audit_failure():
    store, service, contract = setup()
    proposal = service.propose("learner", contract.goal_contract_id, {}, NOW)
    with pytest.raises(PlanConflict):
        service.sign("learner", proposal.version_id, NOW)
    draft = service.accept("learner", proposal.version_id, NOW)
    store.conn.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON learning_audit BEGIN SELECT RAISE(ABORT, 'injected'); END")
    store.conn.commit()
    with pytest.raises(Exception, match="injected"):
        service.sign("learner", draft.version_id, NOW)
    assert service.get("learner", draft.version_id).status == "draft"
    assert service.active("learner", contract.goal_contract_id) is None


def test_two_database_connections_cannot_sign_competing_drafts(tmp_path):
    db_path = str(tmp_path / "plans.db")
    store, service, contract = setup(Store(db_path))
    second_store = Store(db_path)
    second_service = PlanService(second_store)
    drafts = []
    for _ in range(2):
        proposal = service.propose("learner", contract.goal_contract_id, {}, NOW)
        drafts.append(service.accept("learner", proposal.version_id, NOW))

    def sign(pair):
        api, draft = pair
        try:
            return api.sign("learner", draft.version_id, NOW).status
        except PlanConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(sign, [(service, drafts[0]), (second_service, drafts[1])]))
    assert sorted(results) == ["confirmed", "conflict"]
    assert store.conn.execute("SELECT COUNT(*) FROM plan_versions WHERE status='confirmed'").fetchone()[0] == 1
    second_store.close()


def test_initial_existing_draft_can_be_signed_and_naive_time_is_rejected():
    store, service, contract = setup()
    draft = PlanVersion(goal_contract_id=contract.goal_contract_id)
    store.save_plan_version(draft)
    with pytest.raises(ValueError):
        service.sign("learner", draft.version_id, NOW.replace(tzinfo=None))
    assert service.sign("learner", draft.version_id, NOW).status == "confirmed"
