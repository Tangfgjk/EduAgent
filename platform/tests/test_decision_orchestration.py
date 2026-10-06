"""Independent help, evidence triggers and signed-path task eligibility."""
from datetime import timedelta

from app.core.actions import ActionType
from app.core.schema import GoalContract, GoalStatement, MentalStateSnapshot, QuestionItem, utcnow
from app.learning.plans import PlanService
from app.learning.schema import LearningEvidence
from app.learning.service import LearningService
from app.llm.client import FakeLLM
from app.orchestration.session import BANK_PATH, TutorSession, load_bank
from app.learning.assets import AssetCatalog
from app.orchestration.trigger import TriggerEngine
from app.orchestration.policy import BuiltInPolicyV1, TurnContext
from app.learning.perception import PerceptionCandidate
from app.storage.db import Store

BALANCE, SOLVE = "MATH.G7.EQ.BALANCE", "MATH.G7.EQ.SOLVE"


def setup_session(*, signed=True, kcs=None):
    store = Store()
    contract = GoalContract(learner_id="decision-learner", goal_statement=GoalStatement(text="学会方程"))
    store.save_contract(contract)
    service = LearningService(store)
    service.set_consent("decision-learner", ["teaching"], "c1", "student", utcnow())
    plans = PlanService(store)
    proposal = plans.propose("decision-learner", contract.goal_contract_id,
                             {"kc_refs": kcs or [BALANCE, SOLVE]}, utcnow())
    if signed:
        plans.accept("decision-learner", proposal.version_id, utcnow())
        plans.sign("decision-learner", proposal.version_id, utcnow())
    bank = [QuestionItem(item_id="solve", kc_id=SOLVE, stem="2x=6", answer={"var": "x", "value": "3"}, difficulty=.2),
            QuestionItem(item_id="balance", kc_id=BALANCE, stem="x+1=3", answer={"var": "x", "value": "2"}, difficulty=.4)]
    session = TutorSession(store, FakeLLM(), "decision-learner", contract=contract, bank=bank)
    session._asset_catalog = AssetCatalog.load(BANK_PATH.parent / "learning_assets_v1.json")
    return store, service, session


def observe(service, kc, number, *, status="passed", kind="practice", stamp=None):
    stamp = stamp or utcnow() - timedelta(days=2)
    return service.consume(LearningEvidence(evidence_id=f"{kc}:{number}", learner_id="decision-learner",
        session_id="prior", kc_refs=[kc], attempt_id=f"{kc}-attempt-{number}", artifact_ref="artifact",
        verdict_ref="verdict", verdict_status=status, verifier_version="symbolic-v1", confidence=1,
        occurred_at=stamp, consent_scope=["teaching"], consent_version="c1", authorization_source="student",
        assessment_id=f"assessment-{kc}-{number}", assessment_kind=kind, score=1 if status == "passed" else 0))


def test_help_is_independent_of_path_recommendation(monkeypatch):
    store = Store()
    session = TutorSession(store, FakeLLM(), "student")
    session.start()
    monkeypatch.setattr("app.learning.decisions.recommend", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("help must not require a path")))
    result = session.handle_turn(text="给点提示，我不会")
    assert result.actions[0]["type"] == ActionType.HINT
    assert result.actions[0]["params"]["proactive"] is False


def test_signed_plan_help_does_not_recompute_path(monkeypatch):
    _, _, session = setup_session()
    session.start()
    monkeypatch.setattr("app.learning.decisions.recommend", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("help must not recompute a path")))
    assert session.handle_turn(text="提示一下").actions[0]["type"] == ActionType.HINT


def test_due_review_and_unknown_triggers_have_only_explicit_provenance():
    snapshot = MentalStateSnapshot(learner_id="student")
    engine = TriggerEngine()
    assert engine.evaluate(snapshot, 0, now=utcnow()) == []
    signals = {"review_due": True, "unknown_evidence": True, "state_version": 3,
               "version": "learning-signals-v1", "evidence_refs": ["e1"]}
    proposals = engine.evaluate(snapshot, 0, now=utcnow(), learning_signals=signals)
    assert {p.rule_id for p in proposals} == {"TR_REVIEW_DUE", "TR_UNKNOWN_EVIDENCE"}
    assert all(p.evidence_refs == ["e1"] and p.state_version == 3 for p in proposals)
    assert engine.evaluate(snapshot, 0, now=utcnow(), learning_signals=signals) == []


def test_unsigned_proposal_does_not_change_existing_task_order():
    _, _, session = setup_session(signed=False)
    assert session.start().actions[0]["params"]["item_id"] == "solve"


def test_signed_path_prioritizes_prerequisite_and_recomputes_after_mastery():
    _, service, session = setup_session()
    assert session._peek_next().item_id == "balance"
    for number in range(1, 7):
        observe(service, BALANCE, number, kind="review", stamp=utcnow())
    assert session._peek_next().item_id == "solve"


def test_not_mastered_prerequisite_cannot_be_bypassed_by_signed_advanced_scope():
    _, service, session = setup_session(kcs=[SOLVE])
    for number in range(1, 7):
        observe(service, BALANCE, number, status="failed")
    assert session._peek_next() is None
    result = session.start()
    assert result.actions[0]["type"] == ActionType.FEEDBACK
    assert session.current_item is None


def test_due_review_is_consulted_without_changing_signed_scope():
    _, service, session = setup_session(kcs=[BALANCE])
    for number in range(1, 7):
        observe(service, BALANCE, number, kind="review" if number == 1 else "practice")
    result = session.start()
    assert result.actions[0]["params"]["item_id"] == "balance"
    assert result.actions[0]["policy_provenance"]["learning_decision"] == "REVIEW"
    assert result.actions[0]["policy_provenance"]["evidence_refs"]
    assert session.assessment_kinds["balance"] == "review"
    signals = session._learning_signals()
    assert signals["review_due"] is True and signals["evidence_refs"]
    result = session.handle_turn(text="提示一下")
    assert result.actions[0]["type"] == ActionType.HINT


def test_policy_due_review_and_diagnostic_context_are_candidates_subject_to_governor():
    _, _, session = setup_session()
    item = session.bank[0]
    proposals = TriggerEngine().evaluate(session.snapshot, 0, now=utcnow(),
        learning_signals={"unknown_evidence": True, "version": "learning-signals-v1"})
    ctx = TurnContext(snapshot=session.snapshot, ladder_pos=0, session_type="explore", item=None,
        next_item=item, verdict_pending=None, perception=PerceptionCandidate(), proposals=proposals,
        review_context={"due": True}, path_context={"signed": True})
    review = BuiltInPolicyV1().choose(ctx)
    assert review.type == ActionType.TASK
    assert session.governor.decide(review, session._governor_ctx()).decision == "allow"
    ctx.review_context = None
    diagnostic = BuiltInPolicyV1().choose(ctx)
    assert diagnostic.params["assessment_kind"] == "diagnostic"
    ctx.session_type = "checkpoint"
    ctx.item = item
    assert BuiltInPolicyV1().choose(ctx).type == ActionType.QUESTION


def test_not_mastered_own_kc_can_remediate_without_unlocking_advanced_kc():
    _, service, session = setup_session()
    for number in range(1, 7):
        observe(service, BALANCE, number, status="failed")
    context = session._signed_path_context()
    assert next(n for n in context["nodes"] if n["kc_id"] == BALANCE)["decision"] == "REMEDIATE"
    assert SOLVE in context["blocked_kcs"]
    assert session._peek_next().kc_id == BALANCE


def test_signed_legacy_bank_scope_cannot_fall_back_to_unmapped_setup_or_apply():
    setup_kc, apply_kc = "MATH.G7.EQ.SETUP", "MATH.G7.EQ.APPLY"
    _, service, session = setup_session(kcs=[SOLVE, setup_kc, apply_kc])
    observe(service, SOLVE, 1)
    original_count = len(session.bank)
    session.bank.extend([
        QuestionItem(item_id="legacy-setup", kc_id=setup_kc, stem="列出等量关系", answer={"value": "3"}, difficulty=.1),
        QuestionItem(item_id="legacy-discount", kc_id=apply_kc, stem="一件商品原价x打八折立减10", answer={"expr": "0.8*x-10"}, difficulty=.15),
    ])
    context = session._signed_path_context()
    assert context["unknown_kcs"] == {setup_kc, apply_kc}
    assert context["blocked_kcs"] == {SOLVE, setup_kc, apply_kc}
    assert all(n["decision"] == "UNKNOWN" and n["reason"] == "missing_catalog_asset"
               for n in context["nodes"] if n["kc_id"] in {setup_kc, apply_kc})
    assert session._peek_next() is None
    result = session.start()
    assert result.actions[0]["type"] == ActionType.FEEDBACK
    assert result.events[0].observation.payload["path_context"]["unknown_kcs"] == [apply_kc, setup_kc]
    assert len(session.bank) == original_count + 2


def test_unknown_signed_kc_is_blocked_even_with_many_passed_observations():
    unknown = "MATH.G7.EQ.APPLY"
    _, service, session = setup_session(kcs=[unknown])
    for number in range(1, 7):
        observe(service, unknown, number)
    session.bank.append(QuestionItem(item_id="discount", kc_id=unknown, stem="折扣应用",
                                   answer={"value": "3"}, difficulty=.1))
    assert unknown in session._signed_path_context()["unknown_kcs"]
    assert session._peek_next() is None


def test_discount_hint_lower_levels_do_not_supply_the_final_expression():
    item = next(q for q in load_bank() if q.item_id == "EQ-008")
    assert "0.8x" not in item.hint_text(1)
    assert all("0.8x-10" not in item.hint_text(level).replace(" ", "") for level in range(3))
    assert "0.8x-10" in item.hint_text(3).replace(" ", "")
