"""v2 智能体运行时测试：工作区、工具门禁、全单元 E2E（三关口）、红线与预算。"""
from __future__ import annotations

import pytest

from app.agent.gates import parse_plan_revision, parse_reflection
from app.agent.loop import AgentTurn, LearningAgent
from app.agent.transcript import EventKind, TranscriptEvent
from app.agent.workspace import LearningWorkspace, WorkspaceError
from app.llm.client import FakeLLM
from app.storage.db import Store
from app.learning.service import LearningService
from app.core.schema import utcnow


@pytest.fixture()
def store(tmp_path):
    return Store(":memory:")


# ---------- 工作区 ----------

def test_workspace_read_write_and_mistake(tmp_path):
    ws = LearningWorkspace(tmp_path, "stu")
    ws.write_plan("# 计划\n- 每天 2 题")
    assert "每天 2 题" in ws.read("计划.md")
    ws.write("笔记/日思.md", "今天学会了移项变号")
    assert "移项变号" in ws.read("笔记/日思.md")
    ws.append_mistake("EQ-001", "3x+5=14", "x=8", "3", "MC.EQ.SIGN")
    assert "你的答案：x=8" in ws.read("错题本.md")
    assert "重练排期" in ws.read("错题本.md")


def test_workspace_blocks_traversal(tmp_path):
    ws = LearningWorkspace(tmp_path, "stu")
    with pytest.raises(WorkspaceError):
        ws.write("../escape.md", "x")
    with pytest.raises(WorkspaceError):
        ws.read("秘密.md")
    with pytest.raises(WorkspaceError):
        ws.write("笔记", "目录本身不可写")


# ---------- 转录 ----------

def test_transcript_plain_lines():
    call = TranscriptEvent(EventKind.tool_call, "verify.answer EQ-001")
    ok = TranscriptEvent(EventKind.tool_result, "verify.answer", detail="等价", status="ok")
    deny = TranscriptEvent(EventKind.tool_result, "hint.ladder", detail="预算用尽", status="denied")
    assert call.plain() == "  ▶ verify.answer EQ-001"
    assert ok.plain().startswith("  ✓ verify.answer — 等价")
    assert deny.plain().startswith("  ✗ hint.ladder — 预算用尽")


# ---------- 关口解析 ----------

def test_gates_parsers():
    assert parse_plan_revision("每天 3 题，期中提前到 2026-10-20") == {
        "daily_count": 3, "deadline": "2026-10-20",
        "note": "每天 3 题，期中提前到 2026-10-20"}
    assert parse_plan_revision("太难了") == {}
    r = parse_reflection("策略=先移项再验算；预计=80；存=是")
    assert r["strategy_note"] == "先移项再验算" and r["predicted_score"] == 0.8
    assert r["strategy_keep"] and r["attribution"] == "effort_positive"
    assert parse_reflection("我就是学不会")["attribution"] == "ability_fixed_negative"


# ---------- 工具门禁 ----------

def _agent(store, tmp_path, learner="stu_x", **kw):
    LearningService(store).set_consent(learner, ["teaching"], "test-teaching-v1", "learner:test", utcnow())
    return LearningAgent(store, FakeLLM(), learner, workspace_root=tmp_path, **kw)


def test_explain_gated_r01_falls_back_to_hint(store, tmp_path):
    agent = _agent(store, tmp_path)
    agent.tools.ladder_pos = 1
    item = agent.tools.bank[0]
    outcome = agent.tools.explain_gated(item, mode="worked_full", text="答案")
    assert outcome.status == "ok"                       # 回落成功
    assert "R-01" in outcome.detail                     # 且说明被拦
    assert outcome.payload["level"] == 1                # 给的是阶梯提示而非答案


def test_hint_proactive_over_budget_denied(store, tmp_path):
    agent = _agent(store, tmp_path)
    agent.tools.hint_budget = 1
    agent.tools.hints_used = 1
    outcome = agent.tools.hint_ladder(agent.tools.bank[0], proactive=True)
    assert outcome.status == "denied" and outcome.payload.get("rule") == "R-07"


def test_quiz_generate_falls_back_to_bank(store, tmp_path):
    agent = _agent(store, tmp_path)
    outcome = agent.tools.quiz_generate("MATH.G7.EQ.SOLVE", 0.3)
    assert outcome.status == "ok" and "回落题库" in outcome.detail


# ---------- 全单元 E2E（三关口） ----------

def _all_events(turns) -> list[TranscriptEvent]:
    # Ask 暂停对象也会进 turn.events，此处只保留转录事件
    return [e for t in turns for e in t.events if isinstance(e, TranscriptEvent)]


def test_full_unit_three_gates(store, tmp_path):
    agent = _agent(store, tmp_path, "s_full")
    turns: list[AgentTurn] = []

    turns.append(agent.start("两周学会一元一次方程应用题，期中考试 2026-11-03"))
    # 诊断第 1 题 = EQ-001
    assert turns[-1].ask and "3x + 5" in turns[-1].ask.prompt
    turns.append(agent.send("x=8"))                      # 答错 → 归档 + 提示
    assert "2x - 7" in turns[-1].ask.prompt              # 诊断第 2 题 = EQ-002
    assert any(e.title.startswith("workspace.write 错题本") for e in turns[-1].events)
    turns.append(agent.send("x=6"))                      # 答对
    # 关口①：计划签署
    assert turns[-1].ask and turns[-1].ask.gate == "PLAN_CONFIRM"
    plan_md = agent.workspace.read("计划.md")
    assert "每天 2 题" in plan_md and "期中考试" in plan_md

    turns.append(agent.send("每天 3 题"))                 # 修订 → 重新确认
    assert turns[-1].ask.gate == "PLAN_CONFIRM"
    turns.append(agent.send("确认"))                      # → 执行阶段
    assert "每天 3 题" in agent.workspace.read("计划.md")
    assert turns[-1].ask and "练习 1/" in turns[-1].ask.prompt

    turns.append(agent.send("x=99"))                     # 练习1 答错
    assert "练习 2/" in turns[-1].ask.prompt
    turns.append(agent.send("x=5"))                      # 练习2（EQ-004, x=5）
    assert "练习 3/" in turns[-1].ask.prompt
    turns.append(agent.send("x=16"))                     # 练习3（EQ-010, x=16）
    # 关口②：反思
    assert turns[-1].ask.gate == "REFLECTION"

    turns.append(agent.send("策略=先移项再验算；预计=70；存=是"))
    assert turns[-1].done
    events = _all_events(turns)
    assert any(e.kind == EventKind.summary for e in events)
    assert any(e.kind == EventKind.tool_call and e.title.startswith("learner_model.read")
               for e in events)
    # 工作区与状态
    assert "你的答案：x=99" in agent.workspace.read("错题本.md")
    mirror = agent.mirror()
    assert mirror["metacognition"]["calibration"]["predicted_mean"] == 0.7
    assert mirror["mastery"], "BKT 掌握记录应有条目"
    assert mirror["autonomy"]["composite"] > 0.4
    assert len(mirror["strategies"]) == 1


def test_goal_red_line_escalation_gate(store, tmp_path):
    agent = _agent(store, tmp_path, "s_red")
    turn = agent.start("帮我作弊通过考试")
    assert turn.ask and turn.ask.gate == "ESCALATION"
    turns = agent.send("那我想学一元一次方程")
    assert turns.done                                    # 生成器收尾


def test_step_budget_forces_reflect(store, tmp_path, monkeypatch):
    monkeypatch.setattr("app.agent.loop.MAX_STEPS", 6)
    agent = _agent(store, tmp_path, "s_budget")
    turns = []
    turn = agent.start("两周学会一元一次方程")
    turns.append(turn)
    while not (turn.ask and turn.ask.gate == "PLAN_CONFIRM"):
        turns.append(turn)
        turn = agent.send("确认") if turn.ask else None
        if turn is None:
            break
    turns.append(turn)
    # 执行阶段第一步就超预算 → 直接进反思
    if turn.ask and turn.ask.gate == "PLAN_CONFIRM":
        turn = agent.send("确认")
        turns.append(turn)
    while not (turn.done or (turn.ask and turn.ask.gate == "REFLECTION")):
        if turn.ask:
            turn = agent.send("跳过")
            turns.append(turn)
        else:
            break
    all_text = "\n".join(e.plain() for e in _all_events(turns))
    assert "步数预算" in all_text
