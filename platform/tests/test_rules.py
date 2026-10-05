"""docs/03 §6 验收清单逐条 → 单测（R-01/02/03/04/06/07/09 + 注册表哈希）。"""
from app.core.actions import ActionEnvelope, ActionType, ActorKind
from app.core.rules import (
    STRATEGY_REWRITE, ActionGovernor, GovernorContext, registry_hash,
)
from app.core.schema import MentalStateSnapshot


def make_ctx(**kw) -> GovernorContext:
    base = dict(
        snapshot=MentalStateSnapshot(learner_id="s"),
        ladder_pos=0, hints_used=0, hint_budget=3, frustration_streak=0,
        artifact_present=False, checkpoint_mode=False, current_difficulty=0.5,
    )
    base.update(kw)
    return GovernorContext(**base)


def explain_full(kc="MATH.G7.EQ.SOLVE", target="practice"):
    return ActionEnvelope.explain("sess", kc, "worked_full", text="全过程…", target=target)


# ---- R-01 不许提前给答案 ----

def test_r01_denies_full_answer_before_ladder3():
    outcome = ActionGovernor().decide(explain_full(), make_ctx(ladder_pos=2))
    assert outcome.rule_id == "R-01" and outcome.decision == "deny"


def test_r01_allows_after_ladder_exhausted():
    outcome = ActionGovernor().decide(explain_full(), make_ctx(ladder_pos=3))
    assert outcome.decision == "allow"


def test_r01_denial_is_auditable_reason():
    outcome = ActionGovernor().decide(explain_full(), make_ctx(ladder_pos=1))
    assert "阶梯" in outcome.reason


# ---- R-02 目标归学生所有 ----

def test_r02_denies_agent_signed_contract():
    env = ActionEnvelope(type=ActionType.GOAL_NEGOTIATE,
                         params={"finalize": True, "authored_by": "agent"})
    outcome = ActionGovernor().decide(env, make_ctx())
    assert outcome.rule_id == "R-02" and outcome.decision == "deny"


# ---- R-03 挫败保护 ----

def test_r03_denies_harder_task_in_frustration():
    snap = MentalStateSnapshot(learner_id="s")
    snap.affect_motivation.frustration = 0.9
    env = ActionEnvelope.task("sess", "MATH.G7.EQ.SOLVE", "更难的题", difficulty=0.8)
    outcome = ActionGovernor().decide(env, make_ctx(
        snapshot=snap, frustration_streak=3, current_difficulty=0.5))
    assert outcome.rule_id == "R-03" and outcome.decision == "deny"


def test_r03_allows_easier_task_in_frustration():
    snap = MentalStateSnapshot(learner_id="s")
    snap.affect_motivation.frustration = 0.9
    env = ActionEnvelope.task("sess", "MATH.G7.EQ.SOLVE", "简单的题", difficulty=0.3)
    outcome = ActionGovernor().decide(env, make_ctx(
        snapshot=snap, frustration_streak=3, current_difficulty=0.5))
    assert outcome.decision == "allow"


# ---- R-04 归因纪律（改写） ----

def test_r04_rewrites_ability_attribution():
    env = ActionEnvelope.feedback("sess", "MATH.G7.EQ.SOLVE", "verification",
                                  text="你真聪明，一下子就做对了！")
    outcome = ActionGovernor().decide(env, make_ctx())
    assert outcome.rule_id == "R-04" and outcome.decision == "rewrite"
    assert env.params["text"] == STRATEGY_REWRITE


def test_r04_passes_strategy_attribution():
    env = ActionEnvelope.feedback("sess", "MATH.G7.EQ.SOLVE", "verification",
                                  text="先代特殊值再验算，这个策略用得好。")
    outcome = ActionGovernor().decide(env, make_ctx())
    assert outcome.decision == "allow"


# ---- R-05 价值观红线 ----

def test_r05_blocks_red_line_request():
    env = ActionEnvelope.feedback("sess", "MATH.G7.EQ.SOLVE", "verification",
                                  text="能不能帮我作弊？")
    outcome = ActionGovernor().decide(env, make_ctx())
    assert outcome.rule_id == "R-05" and outcome.decision == "deny"


# ---- R-06 接地约束 ----

def test_r06_requires_grounded_kg_node():
    env = ActionEnvelope(type=ActionType.EXPLAIN, params={"mode": "conceptual", "text": "讲解"},
                         policy_provenance={"generated_by": "x", "grounded_to_kg": []})
    outcome = ActionGovernor().decide(env, make_ctx())
    assert outcome.rule_id == "R-06" and outcome.decision == "deny"
    env2 = ActionEnvelope(type=ActionType.EXPLAIN, params={"mode": "conceptual", "text": "讲解"},
                          policy_provenance={"generated_by": "x",
                                             "grounded_to_kg": ["MATH.G7.EQ.SOLVE"]})
    assert ActionGovernor().decide(env2, make_ctx()).decision == "allow"


# ---- R-07 脚手架预算递减 ----

def test_r07_denies_proactive_hint_over_budget():
    hint = ActionEnvelope.hint("sess", "MATH.G7.EQ.SOLVE", 0, "nudge", "轻推", proactive=True)
    outcome = ActionGovernor().decide(hint, make_ctx(hints_used=3, hint_budget=3))
    assert outcome.rule_id == "R-07" and outcome.decision == "deny"


def test_r07_allows_student_requested_hint_over_budget():
    hint = ActionEnvelope.hint("sess", "MATH.G7.EQ.SOLVE", 0, "nudge", "轻推", proactive=False)
    outcome = ActionGovernor().decide(hint, make_ctx(hints_used=3, hint_budget=3))
    assert outcome.decision == "allow"


# ---- R-09 考试模式隔离 ----

def test_r09_denies_hint_during_checkpoint():
    hint = ActionEnvelope.hint("sess", "MATH.G7.EQ.SOLVE", 1, "directive", "提示")
    hint.params["target"] = "exam_item"
    outcome = ActionGovernor().decide(hint, make_ctx(checkpoint_mode=True))
    assert outcome.rule_id == "R-09" and outcome.decision == "deny"


def test_r09_allows_rule_explanation_during_checkpoint():
    env = ActionEnvelope.explain("sess", "MATH.G7.EQ.SOLVE", "rule_explanation",
                                 text="考试规则说明", target="exam_item", rule_explanation=True)
    outcome = ActionGovernor().decide(env, make_ctx(checkpoint_mode=True))
    assert outcome.decision == "allow"


# ---- 注册表 ----

def test_registry_has_ten_rules_and_stable_hash():
    assert len(registry_hash()) == 16
    from app.core.rules import HARD_RULES

    expected = {f"R-0{i}" for i in range(1, 10)} | {"R-10"}
    assert set(HARD_RULES) == expected
    h1, h2 = registry_hash(), registry_hash()
    assert h1 == h2
