"""Optional expression port; decisions and assistance are immutable."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


SAFE_EXPRESSIONS = {
    "ASK_HINT": {"请给我一个提示。", "我想再看一个提示。"},
    "ASK_EXPLANATION": {"可以解释这一步吗？", "我想理解这里的原因。"},
    "SELF_CHECK": {"我想先检查自己的步骤。", "让我先自检一下。"},
    "REFLECT": {"我想回顾一下刚才的过程。", "让我整理一下自己的思路。"},
    "QUIT": {"我想先暂停这次学习。", "我想休息一下。"},
}


class ExpressionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(pattern="^(ANSWER|ASK_HINT|ASK_EXPLANATION|SELF_CHECK|REFLECT|QUIT)$")
    answer: str | None = Field(default=None, max_length=1000)
    text: str = Field(default="", max_length=2000)
    hint_level: int = Field(default=0, ge=0, le=10)
    assistance_mode: str = Field(default="none", pattern="^(none|hint|explanation|answer)$")
    answer_exposed: bool = False


class VisibleContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stem: str = Field(default="", max_length=4000)
    kc_id: str = Field(default="", max_length=100)
    teaching_text: str = Field(default="", max_length=4000)


class ExpressionProvider(Protocol):
    def render(self, payload: dict[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ExpressionResult:
    decision: ExpressionDecision
    source: str
    fallback_reason: str | None = None


class LearnerRenderer:
    """Remote clients must be supplied explicitly; never reads local credentials.

    Non-answer expressions use reviewed phrases. Free-form text is not trusted
    to preserve meaning. ANSWER text stays fixed so a language model cannot
    repair a deliberately incorrect synthetic answer.
    """

    def __init__(self, provider: ExpressionProvider | None = None, *, max_calls: int = 0):
        if type(max_calls) is not int or not 0 <= max_calls <= 100:
            raise ValueError("max_calls must be 0..100")
        self.provider = provider
        self.max_calls = max_calls
        self.calls = 0

    def render(self, decision: ExpressionDecision, context: VisibleContext) -> ExpressionResult:
        if not isinstance(decision, ExpressionDecision) or not isinstance(context, VisibleContext):
            raise TypeError("renderer requires validated public contracts")
        if self.provider is None:
            return ExpressionResult(decision, "template")
        if self.calls >= self.max_calls:
            return ExpressionResult(decision, "template", "call_budget_exhausted")
        self.calls += 1
        # Only allowlisted visible fields cross the port, never profile/latent/oracle.
        payload = {"decision": decision.model_dump(), "visible_context": context.model_dump()}
        try:
            candidate = ExpressionDecision.model_validate(self.provider.render(payload))
        except Exception:
            return ExpressionResult(decision, "template", "provider_failure_or_invalid_schema")
        immutable = ("action", "answer", "hint_level", "assistance_mode", "answer_exposed")
        if any(getattr(candidate, key) != getattr(decision, key) for key in immutable):
            return ExpressionResult(decision, "template", "decision_changed")
        if decision.action == "ANSWER" and candidate.text != decision.text:
            return ExpressionResult(decision, "template", "answer_expression_changed")
        if not candidate.text.strip():
            return ExpressionResult(decision, "template", "empty_expression")
        if candidate.text != decision.text and candidate.text not in SAFE_EXPRESSIONS.get(decision.action, set()):
            return ExpressionResult(decision, "template", "unreviewed_expression")
        return ExpressionResult(candidate, "explicit_provider")
