import pytest
from app.core.schema import QuestionItem
from app.learning.verifier import verify_math, verify_expression


def item(**answer):
    return QuestionItem(item_id="safe",kc_id="MATH.G7.EQ.SOLVE",difficulty=.2,stem="3x=9",answer=answer)


@pytest.mark.parametrize("input",["__import__('os').getcwd()", "open('secret')", "x.__class__", "[3][0]", "3 if True else 4", "9**99999999", "1/0", "y=3", "x=1=3"])
def test_untrusted_syntax_is_unverifiable_without_execution(input):
    assert verify_math(input,item(var="x",value="3")).status == "unverifiable"


def test_expression_parser_preserves_implicit_arithmetic():
    assert verify_math("6/2",item(var="x",value="3")).status == "passed"
    assert verify_expression("0.8(x-10)",item(var="x",expr="0.8x-8")).status == "passed"
    assert verify_expression("0.8x-8+__import__('os').getcwd()",item(var="x",expr="0.8x-8")).status == "unverifiable"
