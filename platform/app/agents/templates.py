"""docs/01 §5：智能体派生 —— 模板五要素 + 生命周期契约 + v1 怀疑者实现。

派生体是无状态进程：一次 ask 带预算与回报契约，产物必须符合 SkepticReport。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.llm.client import BaseLLM, LLMError
from app.llm.prompts import skeptic_messages


class AgentTemplate(BaseModel):
    """派生模板五要素（docs/01 §5.3）。"""

    template_id: str
    version: str = "1.0.0"
    system_prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    knowledge_slice: str = ""
    budget: dict = Field(default_factory=lambda: {"max_turns": 6, "max_tokens": 8000})
    lifecycle_exit: str = "report_ready | budget_exhausted | constraint_violation"
    report_contract: str = "SkepticReport"


TEMPLATES: dict[str, AgentTemplate] = {
    "skeptic": AgentTemplate(
        template_id="skeptic",
        system_prompt="审题怀疑者：找漏洞、提追问、不给答案",
        tools=[],
        knowledge_slice="current_item",
        budget={"max_turns": 1, "max_tokens": 800},
        report_contract="SkepticReport",
    ),
}


class SkepticReport(BaseModel):
    """回报契约：怀疑者必须带回的结构化产物，否则注销且不入队。"""

    gaps: list[str] = Field(default_factory=list, max_length=3)
    followups: list[str] = Field(default_factory=list, max_length=3)


def derive_skeptic(llm: BaseLLM, item_stem: str, student_work: str) -> SkepticReport | None:
    """按 skeptic 模板实例化一次性派生体；产出不符合回报契约 → 返回 None（注销并留痕由调用方记录）。"""
    template = TEMPLATES["skeptic"]
    try:
        report = llm.complete_json(
            skeptic_messages(item_stem, student_work), SkepticReport, temperature=0.3,
        )
        if not report.gaps and not report.followups:
            return None  # 空产物 = 未达成 report_ready，注销
        return report
    except LLMError:
        return None
