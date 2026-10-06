"""ZCode 式终端 REPL：`uv run python -m app.cli`。

输入目标 → 转录滚动（▶✓✗⏸）→ 关口处停下等人 → 反思 → 单元总结。
会话内命令：/mirror 镜子 · /plan 看计划 · /quit 退出（进度已实时落盘）。
"""
from __future__ import annotations

import sys
from uuid import uuid4

from app.agent.loop import LearningAgent
from app.agent.transcript import Ask, EventKind, render_ansi
from app.config import Settings
from app.gateway.routes import WEB_INDEX  # noqa: F401  (保持单一路径来源)
from app.llm.client import FakeLLM, OpenAICompatClient
from app.storage.db import Store
from app.core.schema import utcnow
from app.learning.service import LearningService

BANNER = r"""
╭──────────────────────────────────────────────────╮
│  RSI 学习智能体 · 自主学习定制版（v2, ZCode 式）  │
│  给我目标，我自主跑完：诊断→计划→练习→反思        │
│  命令：/mirror 镜子  /plan 计划  /quit 退出        │
╰──────────────────────────────────────────────────╯"""


def render(turn_events, color: bool = True) -> None:
    for ev in turn_events:
        if isinstance(ev, Ask):
            continue                     # Ask 的提示词在主循环单独展示
        if ev.kind is EventKind.message:
            print(f"\n小伴> {ev.title}")
        else:
            print(render_ansi(ev, color=color))


def ensure_teaching_consent(store: Store, learner_id: str, input_fn=None) -> bool:
    """Only an explicit learner response creates a teaching-only consent record."""
    service = LearningService(store)
    current = service.consent(learner_id)
    if current and "teaching" in current["scopes"]:
        return True
    input_fn = input_fn or input
    try:
        reply = input_fn("学习记录仅用于本地教学。是否同意保存作答与学习状态？输入「同意授权」继续：").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    if reply != "同意授权":
        return False
    service.set_consent(learner_id, ["teaching"], "cli-teaching:" + uuid4().hex,
                        "learner:cli-explicit", utcnow())
    return True


def main() -> None:
    settings = Settings.load()
    llm = (OpenAICompatClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
           if settings.llm_api_key else FakeLLM())
    store = Store(settings.db_path)
    color = sys.stdout.isatty()
    print(BANNER)
    print(f"LLM: {'GLM(OpenAI兼容)' if settings.llm_api_key else 'FakeLLM（未配置 Key）'}")
    if not ensure_teaching_consent(store, settings.learner_id):
        print("尚未授予教学记录授权，已退出。")
        store.close()
        return
    try:
        goal = input("\n你> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n下次见。"); return
    if not goal:
        print("（空目标，退出）"); return

    agent = LearningAgent(store, llm, settings.learner_id)
    try:
        turn = agent.start(goal)
        while True:
            render(turn.events, color)
            if turn.done:
                print("\n本单元完成。进度已落盘，随时回来继续。")
                return
            ask: Ask = turn.ask
            print(f"\n⏸ {ask.prompt}")
            try:
                line = input("你> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n已暂停——进度与工作区已实时保存，下次输入同一目标可继续。")
                return
            if not line:
                continue
            if line == "/quit":
                print("进度已保存，再见。"); return
            if line == "/mirror":
                import json

                print(json.dumps(agent.mirror(), ensure_ascii=False, indent=2))
                continue
            if line == "/plan":
                print(agent.workspace.read("计划.md") or "（计划还没生成）")
                continue
            turn = agent.send(line)
    except KeyboardInterrupt:
        print("\n已中断——进度与工作区已实时保存。")


if __name__ == "__main__":
    main()
