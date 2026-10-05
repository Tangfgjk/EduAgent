"""docs/03 §4.1：提示阶梯状态机 + 渐撤参数的单调性。"""
from app.core.machines import Phase5E, HintLadder, Session5E, hint_budget, start_level


def test_ladder_advance_and_top():
    ladder = HintLadder(autonomy_index=0.4)
    assert ladder.pos == 1          # start_level(0.4) = 1
    ladder.advance(); ladder.advance()
    assert ladder.pos == 3 and ladder.exhausted()
    ladder.advance()
    assert ladder.pos == 3          # 顶端保持


def test_start_level_monotonic_decreasing():
    """自主性越高，起点干预越少（g 单调递减）。"""
    levels = [start_level(a) for a in [0.05, 0.3, 0.6, 0.95]]
    assert levels == sorted(levels, reverse=True)
    assert levels[-1] == 0


def test_hint_budget_monotonic_decreasing():
    """R-07：提示预算 f(autonomy) 单调递减，下限 1。"""
    budgets = [hint_budget(a) for a in [0.05, 0.3, 0.6, 0.95]]
    assert budgets == sorted(budgets, reverse=True)
    assert budgets[-1] >= 1
    assert hint_budget(0.9) == 1


def test_5e_transitions():
    s = Session5E()
    assert s.phase == Phase5E.ENGAGE
    s.transition(Phase5E.EXPLORE)
    s.transition(Phase5E.EXPLAIN)
    s.transition(Phase5E.EXPLORE)   # Explain → Explore 允许回探
    s.transition(Phase5E.EXPLAIN)
    s.transition(Phase5E.ELABORATE)
    s.transition(Phase5E.EVALUATE)
    s.transition(Phase5E.ENGAGE)    # 契约修订（双环）
    assert s.phase == Phase5E.ENGAGE


def test_5e_illegal_transition():
    import pytest

    s = Session5E()
    with pytest.raises(ValueError):
        s.transition(Phase5E.EVALUATE)  # Engage → Evaluate 非法
