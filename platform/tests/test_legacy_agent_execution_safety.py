"""Compatibility CLI uses the same source binding and exact-output discipline."""
import json

import pytest

from app.agent.loop import LearningAgent
from app.core.schema import utcnow
from app.governance.authority import Principal
from app.governance.service import GovernanceService
from app.learning.service import ConsentDenied, LearningService
from app.llm.client import FakeLLM
from app.storage.db import Store


@pytest.fixture
def workspace(tmp_path):
    store = Store()
    LearningService(store).set_consent("student", ["teaching"], "c1", "student", utcnow())
    yield store, tmp_path
    store.close()


class OpenAICompatClient(FakeLLM):
    def __init__(self, payload=None):
        super().__init__()
        self.calls = []
        self.payload = payload or "MODEL-INJECTED-ANSWER-987654321"
    def complete(self, messages, temperature=.2):
        self.calls.append(messages)
        return self.payload


def test_legacy_message_polish_never_calls_model_or_changes_source_text(workspace):
    store, path = workspace
    model = OpenAICompatClient()
    agent = LearningAgent(store, model, "student", workspace_root=path)
    assert agent._polish_plain("先写下自己的第一步。") == "先写下自己的第一步。"
    assert model.calls == []
    result = agent.start("掌握方程")
    assert "987654321" not in str(result)
    assert not any("润色" in str(messages) for messages in model.calls)


def test_legacy_plan_does_not_persist_or_display_free_model_draft(workspace):
    store, path = workspace
    model = OpenAICompatClient(json.dumps({"diag_count": 1, "daily_count": 1,
        "content_md": "MODEL-INJECTED-ANSWER-987654321" * 8}))
    agent = LearningAgent(store, model, "student", workspace_root=path)
    turn = agent.start("掌握方程")
    turn = agent.send("跳过")
    assert turn.ask.gate == "PLAN_CONFIRM"
    assert "987654321" not in agent.workspace.read("计划.md")
    assert "987654321" not in str(turn)
    assert len(model.calls) == 1  # structured UnitIntent only; no PlanDraft generator


def test_generated_quiz_is_separate_candidate_never_self_promotes_to_trusted_bank(workspace):
    store, path = workspace
    model = OpenAICompatClient(json.dumps({"stem": "MODEL-INJECTED-QUESTION-987654321", "value": "12"}))
    agent = LearningAgent(store, model, "student", workspace_root=path)
    before = [item.model_dump() for item in agent.tools.bank]
    result = agent.tools.quiz_generate("MATH.G7.EQ.SOLVE", .55)
    assert result.status == "ok" and result.payload["generated_candidate_only"]
    assert [item.model_dump() for item in agent.tools.bank] == before
    assert "987654321" not in str(result)
    candidate = agent.tools.pending_generated_candidates[0]
    assert agent.tools.task_issue(candidate).status == "denied"


def test_legacy_forged_task_hint_and_full_answer_cannot_borrow_known_kc(workspace):
    store, path = workspace
    agent = LearningAgent(store, FakeLLM(), "student", workspace_root=path)
    item = agent.tools.bank[0]
    fake = item.model_copy(update={"stem": "Forged model solution 987654321"})
    assert agent.tools.task_issue(fake).status == "denied"
    hint = agent.tools.hint_ladder(item, text="Forged model solution 987654321")
    assert hint.status == "denied" and hint.payload["rule"] == "R-06"
    agent.tools.ladder_pos = 3
    explanation = agent.tools.explain_gated(item, text="Forged model solution 987654321")
    assert explanation.status == "denied" and explanation.payload["rule"] == "R-06"
    assert agent.tools.hints_used == 0 and item.item_id not in agent.tools.exposed_answers


def test_legacy_canonical_answer_still_obeys_r01_and_full_exposure_tracking(workspace):
    store, path = workspace
    agent = LearningAgent(store, FakeLLM(), "student", workspace_root=path)
    item = agent.tools.bank[0]
    answer = str(item.answer["value"])
    early = agent.tools.explain_gated(item, text=answer)
    assert early.status == "ok" and "R-01" in early.detail and early.payload["level"] == 1
    assert item.item_id not in agent.tools.exposed_answers
    agent.tools.ladder_pos = 3
    late = agent.tools.explain_gated(item, text=answer)
    assert late.status == "ok" and item.item_id in agent.tools.exposed_answers


def test_legacy_second_denied_fallback_never_consumes_or_displays_a_hint(workspace):
    store, path = workspace
    agent = LearningAgent(store, FakeLLM(), "student", workspace_root=path)
    from app.core.rules import RuleOutcome
    class AlwaysDeny:
        def decide(self, env, ctx):
            return RuleOutcome("R-01", "deny", "denied", env)
    agent.tools.governor = AlwaysDeny()
    result = agent.tools.explain_gated(agent.tools.bank[0], text="987654321")
    assert result.status == "denied" and "text" not in result.payload
    assert agent.tools.hints_used == 0


def test_legacy_invalid_math_does_not_use_free_model_rubric_feedback(workspace):
    store, path = workspace
    model = OpenAICompatClient(json.dumps({"score": 1, "confidence": 1, "feedback": "MODEL-INJECTED-ANSWER-987654321"}))
    agent = LearningAgent(store, model, "student", workspace_root=path)
    result = agent.tools.verify_answer(agent.tools.bank[0], "not-a-math-answer")
    assert result.status == "fail" and result.payload["verdict"]["status"] == "unverifiable"
    assert model.calls == [] and "987654321" not in str(result)


def test_legacy_continuation_and_mirror_blocked_after_withdrawal(workspace):
    store, path = workspace
    agent = LearningAgent(store, FakeLLM(), "student", workspace_root=path)
    agent.start("掌握方程")
    GovernanceService(store).change_privacy(Principal("student", "learner", frozenset({"student"})),
        "student", "withdraw", reason_code="requested")
    turn = agent.send("确认")
    assert turn.ask.gate == "ESCALATION"
    with pytest.raises(ConsentDenied):
        agent.mirror()
    with pytest.raises(ConsentDenied):
        LearningAgent(store, FakeLLM(), "student", workspace_root=path)
