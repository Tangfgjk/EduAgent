"""Minimal teach-the-virtual-student role.

The role exposes a possible misconception through a report. Its report is not
learner evidence and cannot promote mastery. Every proposed interaction still
goes through the shared ActionGovernor.
"""
from __future__ import annotations

import time
from dataclasses import replace
from typing import Callable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.actions import ActionEnvelope, ActionType, ActorKind
from app.core.rules import ActionGovernor, GovernorContext
from app.llm.client import BaseLLM, LLMError, extract_json


class TeachPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    misconception: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=1000)
    next_question: str = Field(min_length=1, max_length=1000)


class TeachReport(BaseModel):
    role: str = "teach_virtual_student"
    template_version: str = "1.0.0"
    artifact_refs: list[str]
    kc_refs: list[str]
    candidate_only: bool = True
    trust_weight: float = 0.0
    exit_status: str
    misconception: str | None = None
    rationale: str | None = None
    actions: list[ActionEnvelope] = Field(default_factory=list)
    reviews: list[dict] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)


class TeachVirtualStudentRunner:
    def __init__(self, llm: BaseLLM, *, clock: Callable[[], float] = time.monotonic):
        self.llm = llm
        self.clock = clock

    def run(self, *, session_id: str, kc_refs: list[str], artifact_refs: list[str],
            lesson: str, governor: ActionGovernor, context: GovernorContext,
            template_version: str = "1.0.0", max_output_bytes: int = 4000, deadline_ms: int = 3000) -> TeachReport:
        if not 1 <= max_output_bytes <= 16000 or not 1 <= deadline_ms <= 300000:
            raise ValueError("Invalid teach role output/time budget")
        started = self.clock()
        report = TeachReport(artifact_refs=list(artifact_refs), kc_refs=list(kc_refs),
                             template_version=template_version,
                             provenance={"template_version": template_version, "trust_weight": 0.0,
                                         "candidate_only": True}, exit_status="invalid_report")
        if not kc_refs or not artifact_refs:
            report.exit_status = "constraint_violation"
            report.provenance["reason"] = "KC and artifact references required"
            return report
        invocation = ActionEnvelope(session_id=session_id, actor_kind=ActorKind.derived_agent,
            actor_ref="teach_virtual_student", template_version=template_version, type=ActionType.ENV_OP,
            params={"operation": "teach_virtual_student_generate"},
            policy_provenance={"grounded_to_kg": kc_refs, "artifact_refs": artifact_refs})
        if governor.decide(invocation,context).decision == "deny":
            report.exit_status = "constraint_violation"
            return report
        messages = [
            {"role": "system", "content": "你是虚拟学生。只报告一个可检验的误区和一个追问；不要宣告真实学习者掌握，不要调用工具。只输出 JSON：" + str(TeachPayload.model_json_schema())},
            {"role": "user", "content": f"待教策略/材料：{lesson}\nKC:{kc_refs}\n学生作品引用:{artifact_refs}"},
        ]
        try:
            raw = self.llm.complete(messages, temperature=.2)
            if len(raw.encode("utf-8")) > max_output_bytes:
                report.exit_status = "budget_exhausted"
                return report
            if (self.clock() - started) * 1000 > deadline_ms:
                report.exit_status = "timed_out"
                return report
            payload = TeachPayload.model_validate_json(extract_json(raw))
        except (LLMError, ValidationError, ValueError):
            report.provenance["reason"] = "Invalid teach report; no action delivered"
            return report
        envelope = ActionEnvelope(session_id=session_id, actor_kind=ActorKind.derived_agent,
            actor_ref="teach_virtual_student", template_version=template_version, type=ActionType.QUESTION,
            params={"stem": payload.next_question, "target": "practice", "intent": "metacognitive_check"},
            policy_provenance={"generated_by": "teach-virtual-student-v1", "grounded_to_kg": kc_refs,
                               "artifact_refs": artifact_refs, "trust_weight": 0.0,
                               "candidate_only": True})
        checked = governor.decide(envelope, replace(context))
        report.reviews.append({"action_id": envelope.action_id, "decision": checked.decision,
                               "rule_id": checked.rule_id, "reason": checked.reason})
        if checked.decision == "deny":
            report.exit_status = "constraint_violation"
            return report
        report.misconception, report.rationale = payload.misconception, payload.rationale
        report.actions = [checked.envelope]
        report.exit_status = "report_ready"
        report.provenance["report_id"] = uuid4().hex
        return report
