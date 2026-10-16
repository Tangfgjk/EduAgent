"""端到端：FakeLLM 下完整 SRL 闭环（契约→探究会话→反思→镜子）+ 检查点 R-09 流程。"""
from app.agents.templates import derive_skeptic
from app.core.schema import GoalContract, GoalStatement, MentalStateSnapshot, AutonomyIndex
from app.llm.client import FakeLLM
from app.orchestration.session import TutorSession
from app.orchestration.trigger import TriggerEngine
from app.storage.db import Store


def _contract(store, learner_id):
    contract = GoalContract(
        learner_id=learner_id,
        goal_statement=GoalStatement(text="两周内学会解一元一次方程应用题"),
    )
    contract.status = "active"
    store.save_contract(contract)
    return contract


def test_e2e_explore_full_loop():
    store, llm = Store(":memory:"), FakeLLM()
    contract = _contract(store, "s1")
    s = TutorSession(store, llm, "s1", contract=contract, session_type="explore")

    r0 = s.start()
    assert "3x + 5 = 14" in r0.reply                      # EQ-001 开题
    assert r0.ui["ladder"]["pos"] == 1                    # autonomy 0.4 → 起始 L1

    # 答错：验证 failed + 阶梯升级 + 归因安全反馈
    r1 = s.handle_turn(answer="x=8")
    assert r1.ui["verdict"]["status"] == "failed"
    assert r1.ui["ladder"]["pos"] == 2
    assert "没走通" in r1.reply

    # 求助 → 阶梯提示（学生求助不占主动预算）
    r2 = s.handle_turn(text="怎么做？给点提示")
    assert "3x = 9" in r2.reply                           # L2 部分范例文本
    assert r2.ui["hint_budget"]["used"] == 0

    # 想要答案 → R-01 拦截 + 教学化替代 + 审计留痕
    r3 = s.handle_turn(text="直接告诉我答案")
    assert r3.denial is not None and r3.denial["rule"] == "R-01"
    events = store.events_for_learner("s1")
    assert any(e.observation.payload.get("audit") and
               e.observation.payload.get("denial", {}).get("rule") == "R-01"
               for e in events)

    # 答对：反馈 + 状态更新
    r4 = s.handle_turn(answer="3")
    assert r4.ui["verdict"]["status"] == "passed"

    # 下一题推进
    r5 = s.handle_turn()
    assert "2x - 7" in r5.reply                           # EQ-002

    # 反思关卡（必经）→ 校准与策略档案写入
    ref = s.submit_reflection({"attribution": "effort_positive",
                               "predicted_score": 0.8, "strategy_keep": True})
    assert ref["status"] == "reflection_saved"
    mirror = s.mirror()
    assert mirror["metacognition"]["calibration"]["predicted_mean"] == 0.8
    assert len(mirror["strategies"]) == 1
    assert mirror["mastery"] and mirror["mastery"][0]["ci95"]
    assert len(mirror["session"]["verdicts"]) == 2


def test_checkpoint_r09_isolation():
    store, llm = Store(":memory:"), FakeLLM()
    s = TutorSession(store, llm, "s2", session_type="checkpoint")
    r0 = s.start()
    assert "考试" not in r0.reply and "3x + 5" in r0.reply   # 考题直接开卷

    r1 = s.handle_turn(text="怎么做？给点提示")               # 求助 → 只讲规则（R-09 允许）
    assert r1.denial is None and "规则" in r1.reply

    r2 = s.handle_turn(text="直接告诉我答案")                 # 要答案 → R-09 拦截
    assert r2.denial is not None and r2.denial["rule"] == "R-09"

    r3 = s.handle_turn(answer="3")                           # 作答 → 判分
    assert r3.ui["verdict"]["status"] == "passed"

    r4 = s.handle_turn()                                     # 队列弹出下一考题 EQ-003
    assert "5x = 3x + 8" in r4.reply


def test_trigger_fires_once_within_cooldown():
    snap = MentalStateSnapshot(learner_id="s")
    snap.affect_motivation.frustration = 0.9
    engine = TriggerEngine()
    first = engine.evaluate(snap, wrong_streak=0)
    assert [p.rule_id for p in first] == ["TR_FRUSTRATION"]
    second = engine.evaluate(snap, wrong_streak=0)           # 冷却期内不再触发
    assert second == []


def test_stuck_trigger_for_hint():
    snap = MentalStateSnapshot(learner_id="s")
    engine = TriggerEngine()
    proposals = engine.evaluate(snap, wrong_streak=2)
    assert any(p.rule_id == "TR_STUCK" for p in proposals)


def test_skeptic_derivation_with_contract():
    llm = FakeLLM(['{"gaps": ["没有代入验算"], "followups": ["把 x=4 代回原等式，两边相等吗？"]}'])
    report = derive_skeptic(llm, "5x = 3x + 8", "x = 4")
    assert report is not None and report.followups


def test_skeptic_empty_report_is_terminated():
    llm = FakeLLM(['{"gaps": [], "followups": []}'])
    assert derive_skeptic(llm, "题", "答") is None           # 未达成回报契约 → 注销


def test_r07_budget_exhaustion_blocks_proactive_hint():
    """自主性 0.9 → 预算 1；主动提示用掉后再次触发 → R-07 拦截转鼓励反馈。"""
    store = Store(":memory:")
    store.append_snapshot(MentalStateSnapshot(
        learner_id="s4", autonomy_index=AutonomyIndex(composite=0.9)))
    s = TutorSession(store, FakeLLM(), "s4", session_type="explore")
    s.start()
    s.handle_turn(answer="x=8")            # wrong1 → 反馈
    s.handle_turn(answer="x=8")            # wrong2 → 反馈；wrong_streak=2
    r3 = s.handle_turn()                   # TR_STUCK → 主动提示（预算 1/1 用掉）
    assert r3.ui["hint_budget"]["used"] == 1
    s.handle_turn(answer="x=8")            # wrong3 → 反馈
    r5 = s.handle_turn()                   # TR_STUCK 冷却中 → 无提示
    assert r5.denial is None
    s.trigger._last_fired.clear()          # 模拟冷却期已过
    r6 = s.handle_turn()                   # TR_STUCK 再次触发 → R-07 拦截
    assert r6.denial is not None and r6.denial["rule"] == "R-07"
    assert r6.ui["hint_budget"]["used"] == 1
    assert r6.ui["verdict"] is None
