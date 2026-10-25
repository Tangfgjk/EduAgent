"""Compatibility renderer for legacy intents and checked model-authored questions.

New dialogue artifacts keep their prose after structural checks; this does not
certify pedagogical correctness or prove that arbitrary prose contains no answer.
Legacy modes retain the original restricted renderer."""
from copy import deepcopy

VERSION = 'intent-renderer.v1'
QUESTIONS = {
    'clarify': '你能先说明自己的思路，以及仍不确定的地方吗？',
    'sample_space': '你把什么看作基本结果，怎样检查是否遗漏或重复？',
    'equal_likelihood': '这些基本结果等可能的依据是什么？',
    'favorable': '你如何判断哪些基本结果符合题目描述的事件？',
    'ratio': '你写出的分子和分母分别在数什么，计数单位一致吗？',
}
ROLE_QUESTIONS = {
    'proposer': '你愿意先用列表或图表示自己的思路，再说明选择这种表示的依据吗？',
    'critic': '你的思路依赖哪些前提，题目是否支持这些前提？',
    'organizer': '比较自己的表示和前提后，你接下来准备核对哪项依据？',
}


def render_result(result):
    """Idempotent, also used at history read boundaries; preserves student quotes."""
    if not result:
        return result
    from app.modules.generation.grounded_response import check_text
    quoted='\n'.join([result.get('quote',''),*result.get('verified_quotes',[])])
    try:
        if result.get('teaching_decision'):
            check_text(result['teaching_decision']['reason'],quoted,require_question=False)
        if result.get('decision_reason'):check_text(result['decision_reason'],quoted,require_question=False)
    except (ValueError,TypeError,KeyError):return None
    if result.get('rendering_mode')=='grounded-dialogue.v1':
        from app.modules.generation.grounded_response import render
        return render(result)
    if result.get('rendering_mode')=='rational-agent-intent.v2':
        bank=result.get('approved_questions') or {}
        if set(bank)!={'clarify','basis','representation','verification','compare'} or result.get('focus') not in bank:
            return None
        value=deepcopy(result); value['question']=bank[value['focus']]
        if value.get('voices'):
            value['voices']=[{'role':role,'quote':voice.get('quote',''),'text':text} for voice,(role,text) in zip(value['voices'],[
                ('proposer',bank[value['focus']]),('critic',bank['basis']),('organizer',bank['verification'])])]
            value['question']=bank['verification']
        return value
    if result.get('rendering_mode') == 'generic-intent.v1':
        from app.modules.generation.interaction import QUESTIONS as generic_questions
        value = deepcopy(result)
        if value.get('focus') not in generic_questions: return None
        value['question'] = generic_questions[value['focus']]
        if value.get('voices'):
            value['voices'] = [{'role': role, 'quote': voice.get('quote', ''), 'text': text} for voice, (role, text) in zip(value['voices'], [('proposer', value['question']), ('critic', '目前哪些依据你已说明，是否还有需要核对的前提？'), ('organizer', '你准备怎样整理已有依据，并独立检查自己的结论？')])]
            value['question'] = value['voices'][-1]['text']
        return value
    if result.get('focus') not in QUESTIONS or any(v.get('role') not in ROLE_QUESTIONS for v in result.get('voices', [])):
        return None
    value = deepcopy(result)
    value['question'] = QUESTIONS[value['focus']]
    if value.get('voices'):
        value['voices'] = [{**voice, 'text': QUESTIONS[value['focus']] if voice['role'] == 'proposer' and value['focus'] != 'clarify' else ROLE_QUESTIONS[voice['role']]} for voice in value['voices']]
        value['question'] = value['voices'][-1]['text']
    value['judgment'] = '模型仅选择讨论方向；问题由受限安全模板展示，不评定讲解质量、掌握度或教学效果'
    value['rendering_mode'] = VERSION
    value['diagnostic_code'] = 'unverified_prose_replaced'
    return value
