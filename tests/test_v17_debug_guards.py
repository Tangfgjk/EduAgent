import json
import pytest
from app.llm.provider import ProviderError
from app.modules.generation.interaction import validate
from app.modules.generation.grounded_response import VERSION, render


def response(question):
    return json.dumps({'quote':'先看符号','focus':'basis','question':question,
        'teaching_decision':{'action':'clarify','reason':'核对当前表达中的依据','support':'maintain'}},ensure_ascii=False)


@pytest.mark.parametrize('question',[
    '“答案是3”，你知道为什么吗？', '「正确结果为三分之一」，你理解了吗？',
    '负数除以负数一定为负，你能说明依据吗？',
    '零可以作为除数，你能举个例子吗？',
    'x=3，你能代入检查吗？','x等于三，你能说明吗？',
    '两个负数相除的商为负数，你理解了吗？','同号相乘得负数，你会应用吗？',
    '任意三角形都满足勾股定理，你会应用吗？','频率总是等于概率，你能解释吗？',
    '一次函数的斜率一定是零，你能说明吗？',
    '先从14减去5得到9，再除以3得到3，你能照做吗？',
    '先从十四减去五得到九，再除以三得到三，你能照做吗？',
    '负数的绝对值是负数，你能解释吗？',
])
def test_new_content_cannot_hide_answers_or_false_concepts(question):
    snapshot={'purpose':'generic_coach','prompt_version':VERSION,'student_text':'我想先看符号','teaching_signals':{}}
    with pytest.raises(ProviderError):validate(response(question),snapshot)


def test_actual_student_quote_is_preserved_but_not_a_license_for_new_answer():
    text='我想先看符号，我说答案是3但还不确定'
    snapshot={'purpose':'generic_coach','prompt_version':VERSION,'student_text':text,'teaching_signals':{}}
    result=validate(response('你说“答案是3”，能先说明所用的符号规则吗？'),snapshot)
    assert render(result)['question']==result['question']
    with pytest.raises(ProviderError):validate(response('你说“答案是4”，能说明依据吗？'),snapshot)


def test_real_student_wrong_equality_can_be_quoted_for_neutral_clarification():
    snapshot={'purpose':'generic_coach','prompt_version':VERSION,'student_text':'先看符号，我算1+1=3','teaching_signals':{}}
    assert validate(response('你写了“1+1=3”，准备怎样独立检查这一步？'),snapshot)['question'].endswith('？')


@pytest.mark.parametrize('field',['reason','decision_reason'])
def test_every_visible_model_reason_is_guarded(field):
    value=json.loads(response('你准备先核对哪条规则？'))
    if field=='reason':value['teaching_decision']['reason']='正确答案是3，负数的绝对值是负数'
    else:value['decision_reason']='负数的绝对值是负数'
    snapshot={'purpose':'generic_coach','prompt_version':VERSION,'student_text':'我想先看符号','teaching_signals':{}}
    with pytest.raises(ProviderError):validate(json.dumps(value,ensure_ascii=False),snapshot)


def test_observational_reason_is_not_forced_into_question_grammar():
    value=json.loads(response('你准备先核对哪条规则？'))
    value['teaching_decision']['reason']='本轮表达还没有说明负数运算的符号依据'
    snapshot={'purpose':'generic_coach','prompt_version':VERSION,'student_text':'先看符号，我不明白负数除法','teaching_signals':{}}
    result=validate(json.dumps(value,ensure_ascii=False),snapshot)
    assert render(result)['teaching_decision']['reason']==value['teaching_decision']['reason']
