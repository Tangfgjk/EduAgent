"""学习转录（docs/plan v2）：CLI 与 Web 共用的事件流模型。

事件种类：message（智能体话术）/ tool_call / tool_result / gate（关口）/ summary / error。
Ask 是特殊的"等待学生输入"事件——驱动器（CLI/API）收到 Ask 后收集输入并回投给循环。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EventKind(str, Enum):
    message = "message"
    tool_call = "tool_call"
    tool_result = "tool_result"
    gate = "gate"
    summary = "summary"
    error = "error"


@dataclass
class TranscriptEvent:
    kind: EventKind
    title: str
    detail: str = ""
    status: str = "ok"          # ok | fail | denied | waiting
    payload: dict = field(default_factory=dict)

    def plain(self) -> str:
        """无色纯文本行（测试与降级渲染）。"""
        if self.kind == EventKind.tool_call:
            return f"  ▶ {self.title}"
        if self.kind == EventKind.tool_result:
            mark = {"ok": "✓", "fail": "✗", "denied": "✗"}.get(self.status, "·")
            return f"  {mark} {self.title}" + (f" — {self.detail}" if self.detail else "")
        if self.kind == EventKind.gate:
            return f"  ⏸ {self.title}"
        if self.kind == EventKind.summary:
            return f"✓ {self.title}"
        if self.kind == EventKind.error:
            return f"  ✗ {self.title}" + (f" — {self.detail}" if self.detail else "")
        return self.title


@dataclass
class Ask:
    """关口/作答请求：循环在此暂停等待学生输入。"""

    prompt: str
    gate: str | None = None      # PLAN_CONFIRM | REFLECTION | ESCALATION | None(作答)
    payload: dict = field(default_factory=dict)


# ANSI 渲染（CLI 用；不支持彩色的环境自动降级为 plain()）
_COLOR = {
    (EventKind.tool_call, "run"): "\033[36m▶ {title}\033[0m",
    (EventKind.tool_result, "ok"): "\033[32m✓ {title}\033[0m{detail}",
    (EventKind.tool_result, "fail"): "\033[31m✗ {title}\033[0m{detail}",
    (EventKind.tool_result, "denied"): "\033[33m✗ {title}\033[0m{detail}",
    (EventKind.gate, "waiting"): "\033[33m⏸ {title}\033[0m{detail}",
    (EventKind.summary, "ok"): "\033[32m✓ {title}\033[0m",
    (EventKind.error, "fail"): "\033[31m✗ {title}\033[0m{detail}",
}


def render_ansi(event: TranscriptEvent, color: bool = True) -> str:
    line = event.plain()
    if not color:
        return line
    template = _COLOR.get((event.kind, event.status))
    if template is None:
        return line
    return template.format(title=event.title, detail=f" — {event.detail}" if event.detail else "")
