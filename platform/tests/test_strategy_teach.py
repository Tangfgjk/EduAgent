from app.agents.teach_student import TeachVirtualStudentRunner
from app.core.rules import ActionGovernor, GovernorContext
from app.core.schema import MentalStateSnapshot
from app.learning.strategies import DEFAULT_STRATEGIES, StrategyCard, StrategyRegistry
from app.llm.client import FakeLLM


def context():
    return GovernorContext(snapshot=MentalStateSnapshot(learner_id="s"), ladder_pos=0,
        hints_used=0, hint_budget=3, frustration_streak=0)


def test_default_strategy_cards_are_versioned_and_replaceable():
    cards = DEFAULT_STRATEGIES.for_kc("MATH.G7.EQ.SETUP")
    assert cards and cards[0].strategy_id == "equation-model"
    assert cards[0].calibrated is False and cards[0].provenance
    registry = StrategyRegistry(cards=(cards[0],))
    newer = cards[0].model_copy(update={"version": "1.1.0", "steps": (*cards[0].steps, "复述")})
    registry.register(newer)
    assert registry.get("equation-model").version == "1.1.0"


def test_teach_virtual_student_report_is_zero_trust_and_governed():
    llm = FakeLLM(['{"misconception":"把减法移项当作不变号","rationale":"需要解释等式两边同时操作","next_question":"你能说明两边同时做了哪种运算吗？"}'])
    report = TeachVirtualStudentRunner(llm).run(session_id="s", kc_refs=["MATH.G7.EQ.SOLVE"],
        artifact_refs=["artifact:1"], lesson="等式两边保持相等", governor=ActionGovernor(), context=context())
    assert report.exit_status == "report_ready" and report.candidate_only
    assert report.trust_weight == 0 and report.actions[0].actor_ref == "teach_virtual_student"
    assert report.reviews[0]["decision"] == "allow"


def test_teach_virtual_student_never_converts_to_evidence_or_full_answer():
    llm = FakeLLM(['{"misconception":"wrong","rationale":"check","next_question":"为什么？"}'])
    report = TeachVirtualStudentRunner(llm).run(session_id="s", kc_refs=["MATH.G7.EQ.SOLVE"],
        artifact_refs=["artifact:1"], lesson="lesson", governor=ActionGovernor(), context=context())
    assert "evidence" not in report.model_dump() and report.actions[0].type.value == "QUESTION"


def test_teach_virtual_student_handles_invalid_report():
    llm = FakeLLM(["not-json"])
    report = TeachVirtualStudentRunner(llm).run(session_id="s", kc_refs=["MATH.G7.EQ.SOLVE"],
        artifact_refs=["artifact:1"], lesson="lesson", governor=ActionGovernor(), context=context())
    assert report.exit_status == "invalid_report" and not report.actions


def test_semantic_version_order_and_teach_budget():
    card = DEFAULT_STRATEGIES.get("equation-balance")
    registry = StrategyRegistry((card.model_copy(update={"version":"1.2.0"}),card.model_copy(update={"version":"1.10.0"})))
    assert registry.get("equation-balance").version == "1.10.0"
    llm = FakeLLM(['{"misconception":"test","rationale":"test","next_question":"why"}'])
    report = TeachVirtualStudentRunner(llm).run(session_id="s",kc_refs=["MATH.G7.EQ.SOLVE"],artifact_refs=["a"],
        lesson="lesson", governor=ActionGovernor(),context=context(),max_output_bytes=1)
    assert report.exit_status == "budget_exhausted" and report.actions == []
