"""BKT 追踪与验证器单测。"""
from app.core.schema import MentalStateSnapshot, QuestionItem
from app.learning.tracer import BKTTracer
from app.learning.verifier import verify_code, verify_expression, verify_math, verify_with_rubric
from app.llm.client import FakeLLM


def _item():
    return QuestionItem.model_validate({
        "item_id": "T-1", "kc_id": "MATH.G7.EQ.SOLVE", "difficulty": 0.2,
        "stem": "3x + 5 = 14", "answer": {"var": "x", "value": "3"},
    })


def test_bkt_up_and_down_with_bounds():
    snap = MentalStateSnapshot(learner_id="s")
    tracer = BKTTracer()
    p1 = tracer.update(snap, "MATH.G7.EQ.SOLVE", correct=True).p_mastery
    p2 = tracer.update(snap, "MATH.G7.EQ.SOLVE", correct=True).p_mastery
    assert p2 > p1 > 0.1                          # 答对上升，且有先验下界
    p3 = tracer.update(snap, "MATH.G7.EQ.SOLVE", correct=False).p_mastery
    assert p3 < p2                                # 答错下降（状态可升可降）


def test_bkt_confidence_narrows():
    snap = MentalStateSnapshot(learner_id="s")
    tracer = BKTTracer()
    m1 = tracer.update(snap, "MATH.G7.EQ.SOLVE", correct=True)
    width1 = m1.ci95[1] - m1.ci95[0]
    for _ in range(5):
        m = tracer.update(snap, "MATH.G7.EQ.SOLVE", correct=True)
    assert (m.ci95[1] - m.ci95[0]) < width1


def test_verify_math_pass_fail_unverifiable():
    item = _item()
    assert verify_math("x=3", item).status == "passed"
    assert verify_math("3", item).status == "passed"
    assert verify_math("x = 8", item).status == "failed"
    v = verify_math("我不会", item)
    assert v.status == "unverifiable"            # 解析失败宁缺勿脏


def test_verify_expression_equivalence():
    item = QuestionItem.model_validate({
        "item_id": "T-2", "kc_id": "MATH.G7.EQ.APPLY", "difficulty": 0.55,
        "stem": "先打八折再减10", "answer": {"var": "x", "expr": "0.8x-10"},
    })
    assert verify_expression("0.8x - 10", item).status == "passed"
    assert verify_expression("0.8(x-10)", item).status == "failed"


def test_verify_code_exec():
    tests = [{"stdin": "", "expect_stdout": "6"}]
    good = "print(2*3)"
    bad = "print(7)"
    assert verify_code(good, tests).status == "passed"
    assert verify_code(bad, tests).status == "failed"
    assert verify_code("while True: pass", tests, timeout=1.0).status in ("failed", "partial")


def test_rubric_low_confidence_is_unverifiable():
    llm = FakeLLM(['{"score": 0.95, "taxonomy_level": "create", "feedback": "好", "confidence": 0.3}'])
    v = verify_with_rubric(llm, "作品文本", "任务")
    assert v.status == "unverifiable"            # 置信不足不入统计（docs/04 §2）
