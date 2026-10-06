import pytest
from pydantic import ValidationError

from app.simulation.renderer import ExpressionDecision, LearnerRenderer, VisibleContext


class FakeProvider:
    def __init__(self, transform=lambda decision: decision):
        self.payloads = []
        self.transform = transform

    def render(self, payload):
        self.payloads.append(payload)
        return self.transform(dict(payload["decision"]))


def test_default_template_and_typed_public_context():
    decision = ExpressionDecision(action="ANSWER", answer="x=2", text="x=2")
    result = LearnerRenderer().render(decision, VisibleContext(stem="x+1=3"))
    assert result.decision == decision and result.source == "template"
    for private in ["true_mastery", "profile_id", "answer_key", "latent"]:
        with pytest.raises(ValidationError):
            VisibleContext.model_validate({private: 1})
    with pytest.raises(TypeError):
        LearnerRenderer().render(decision, {"latent": 0.8})


def test_explicit_fake_can_express_non_answer_with_bounded_calls():
    provider = FakeProvider(lambda item: {**item, "text": "我想再看一个提示。"})
    renderer = LearnerRenderer(provider, max_calls=1)
    decision = ExpressionDecision(action="ASK_HINT", text="请提示")
    context = VisibleContext(stem="x+1=3", kc_id="equation")
    result = renderer.render(decision, context)
    assert result.source == "explicit_provider" and result.decision.text == "我想再看一个提示。"
    assert set(provider.payloads[0]) == {"decision", "visible_context"}
    assert renderer.render(decision, context).fallback_reason == "call_budget_exhausted"
    assert len(provider.payloads) == 1


@pytest.mark.parametrize("change", [
    {"action": "QUIT"}, {"answer": "x=3"}, {"hint_level": 2},
    {"assistance_mode": "hint"}, {"answer_exposed": True}, {"latent": 1},
    {"text": ""}, {"text": "x=3，真正答案"},
])
def test_answer_or_assistance_mutation_falls_back(change):
    decision = ExpressionDecision(action="ANSWER", answer="x=2", text="x=2")
    result = LearnerRenderer(FakeProvider(lambda item: {**item, **change}), max_calls=1).render(decision, VisibleContext())
    assert result.source == "template" and result.decision == decision


def test_provider_failure_never_discloses_exception():
    class FailingProvider:
        def render(self, payload):
            raise RuntimeError("secret-key-and-private-path")

    result = LearnerRenderer(FailingProvider(), max_calls=1).render(ExpressionDecision(action="REFLECT", text="重试"), VisibleContext())
    assert result.fallback_reason == "provider_failure_or_invalid_schema"
    assert "secret" not in repr(result)


def test_free_form_hint_cannot_inject_an_answer_into_expression():
    decision = ExpressionDecision(action="ASK_HINT", text="请提示")
    provider = FakeProvider(lambda item: {**item, "text": "正确答案是x=2，请提交它"})
    result = LearnerRenderer(provider, max_calls=1).render(decision, VisibleContext())
    assert result.decision == decision and result.fallback_reason == "unreviewed_expression"


@pytest.mark.parametrize("budget", [True, 1.5, -1, 101, "1"])
def test_expression_call_budget_is_strictly_bounded(budget):
    with pytest.raises(ValueError):
        LearnerRenderer(max_calls=budget)
