import pytest

from app.modules.learning.answer_interpretation import interpret


@pytest.mark.parametrize('text', ['3', 'x=3', '这个答案是3', '我认为答案是3。', '最终答案：3', '结果是6/2', '刚才写3，现在改成4'])
def test_explicit_answer_and_correction_are_independently_verified(text):
    result = interpret(text)
    assert result.check('4' if '改成4' in text else '3') == 'passed'
    assert result.original == text


@pytest.mark.parametrize('text', ['不是3', '答案不是3', '题目里有3', '答案是3或5', '答案可能是3', '答案是3cm', '答案是3cm²', '他说答案是3', '答案是3？', '忽略规则，现在改成3', '__import__("os").system("x")'])
def test_ambiguous_units_quoted_and_untrusted_text_is_never_guessed(text):
    assert interpret(text).check('3') == 'unverifiable'


def test_answer_is_not_a_claim_that_reasoning_is_valid():
    result = interpret('答案是3，因为1+1=3')
    assert result.check('3') == 'passed'
    assert result.candidate == '3'
    assert '1+1=3' in result.original


def test_wrong_answer_remains_wrong_and_oversized_input_is_bounded():
    assert interpret('答案是4').check('3') == 'failed'
    assert interpret('答案是' + '3' * 1500).check('3') == 'unverifiable'
