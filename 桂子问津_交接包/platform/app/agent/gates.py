"""三关口语义（docs/plan v2）：①计划签署 ②反思关卡 ③红线升级。

关口在本实现中表现为循环中的 Ask(gate=...) 暂停点；本模块提供关口的
判定与解析函数（纯函数，可单测）。
"""
from __future__ import annotations

import re

GATE_PLAN_CONFIRM = "PLAN_CONFIRM"
GATE_REFLECTION = "REFLECTION"
GATE_ESCALATION = "ESCALATION"

_CONFIRM_RE = re.compile(r"^\s*(确认|可以|ok|好的?|行|开始吧?|没问题)[。!！.~～]*\s*$", re.I)
_SKIP_RE = re.compile(r"^\s*(跳过|skip|下一题|next)\s*$", re.I)
_DAILY_RE = re.compile(r"每天\s*(\d+)\s*题")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_PRED_RE = re.compile(r"预计[=:]?\s*(\d{1,3})\s*%?")
_KEEP_RE = re.compile(r"存[=:]?\s*(是|否)")
_STRATEGY_RE = re.compile(r"策略[=:：]?\s*([^；;]+)")
_ATTR_NEG_RE = re.compile(r"学不会|没天赋|太笨|不是这块料")


def is_confirm(text: str) -> bool:
    return bool(_CONFIRM_RE.match(text or ""))


def is_skip(text: str) -> bool:
    return bool(_SKIP_RE.match(text or ""))


def parse_plan_revision(text: str) -> dict:
    """从学生的修改意见中提取结构化修订（确定性别名 + LLM 兜底）。"""
    revision: dict = {}
    if m := _DAILY_RE.search(text or ""):
        revision["daily_count"] = max(1, min(5, int(m.group(1))))
    if m := _DATE_RE.search(text or ""):
        revision["deadline"] = m.group(1)
    if revision:
        revision["note"] = text.strip()
    return revision


def parse_reflection(text: str) -> dict:
    """解析反思回复：格式宽松，缺项给默认值（反思必经，但不应卡人）。"""
    text = text or ""
    predicted = 0.7
    if m := _PRED_RE.search(text):
        predicted = min(1.0, max(0.0, int(m.group(1)) / 100))
    keep = bool(m := _KEEP_RE.search(text)) and m.group(1) == "是"
    strategy = ""
    if m := _STRATEGY_RE.search(text):
        strategy = m.group(1).strip()
    else:
        strategy = text.strip()[:30] or "未记录"
    attribution = ("ability_fixed_negative" if _ATTR_NEG_RE.search(text)
                   else "effort_positive")
    return {"attribution": attribution, "predicted_score": predicted,
            "strategy_keep": keep, "strategy_note": strategy}
