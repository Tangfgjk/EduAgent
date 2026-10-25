"""Grounded model-authored questions; structural checks are not pedagogy certification."""
import re
from typing import Literal
from pydantic import Field
from app.modules.learning.contracts import Contract
from app.llm.provider import ProviderError
from app.modules.learning.answer_interpretation import check_explicit_equalities

VERSION = 'grounded-dialogue.v1'


class Voice(Contract):
    role: Literal['proposer', 'critic', 'organizer']
    quote: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=6, max_length=180)


class TeachingDecision(Contract):
    action: Literal['clarify', 'practice', 'variant', 'resource', 'reflect']
    reason: str = Field(min_length=4, max_length=160)
    support: Literal['maintain', 'fade', 'restore'] = 'maintain'


def check_text(text, student_text='', require_question=True):
    # Reject executable content, claims of certification and direct answer delivery.
    # This intentionally does not claim to prove arbitrary Chinese mathematics.
    if re.search(r'https?://|<[^>]+>|```|javascript:|data:|忽略.{0,6}(规则|指令)|关闭.{0,4}(核验|验证)|已(经)?掌握|教学效果已', text, re.I):
        raise ValueError('unsafe_teaching_text')
    def quote(match):
        value=match.group()[1:-1]
        if value not in student_text:
            raise ValueError('fabricated_inline_quote')
        return ''
    unquoted=re.sub(r'“[^”]*”|「[^」]*」', quote, text)
    # Free-response coaching has no reason to introduce a new numeric solution.
    # If a number from the learner matters, quote the verified original span.
    # Ordinal references to an interaction step are not mathematical answers.
    numeric=re.sub(r'第[0-9零一二三四五六七八九十百]+(?:步|轮|项|题|个)', '',unquoted)
    if re.search(r'[0-9０-９]',numeric) or re.search(r'(?:加上|减去|乘以|除以|得到|得出|等于)[负正零一二三四五六七八九十百两]+',numeric):
        raise ValueError('unquoted_numeric_solution')
    if re.search(r'(?<![A-Za-z])(?:[xyzXYZ]|答案|结果)\s*(?:=|＝|等于|的值是|的值为)\s*(?:[-+−]?\d|[（(零一二三四五六七八九十负正])',unquoted):
        raise ValueError('direct_solution_assignment')
    if re.search(r'(?:[-+−]?\d+(?:/\d+)?|[零一二三四五六七八九十负正]+)(?:就是|是|为)(?:本题的?)?(?:正确)?答案|the\s+answer\s+(?:is|=)\s*[-+]?\d',unquoted,re.I):
        raise ValueError('direct_answer_delivery')
    if re.search(r'(?:最终)?(?:答案|正确结果|结果|取值)\s*(?:就|应当|应该)?\s*(?:是|为|=|：)\s*(?:[-+−]?\d|[零一二三四五六七八九十负正])|(?:所以|因此|计算后|计算可)(?:得|得到|等于)\s*(?:[-+0-9]|[零一二三四五六七八九十负正])',unquoted):
        raise ValueError('direct_answer_delivery')
    # Bounded concept invariants, not a general proof checker. Reject unsafe
    # assertions; the caller retains the existing neutral, non-model support.
    claims=re.sub(r'\s+','',unquoted)
    false_claims=(r'(?:负数(?:除以|乘以)负数|负数与负数相乘)(?:一定|必然|总是)?(?:得|为|是)(?:到)?负',
        r'(?:两个负数(?:相除|相乘)|负负(?:相除|相乘)|同号(?:相乘|相除))(?:的)?(?:商|积|结果)?(?:一定|必然|总是)?(?:为|是|得|等于)负',
        r'异号(?:相乘|相除)(?:的)?(?:商|积|结果)?(?:一定|必然|总是)?(?:为|是|得|等于)正',
        r'(?:零|0)(?:可以|能)(?:作为|作)?除数',r'除数(?:可以|能)(?:为|是)(?:零|0)',
        r'绝对值(?:可以|能|一定)(?:为|是)负',r'概率(?:一定|可以)(?:大于1|小于0)',
        r'等式两边(?:同|同时)?除以(?:零|0)',r'(?:所有|任意)三角形(?:都)?满足勾股定理',
        r'斜边(?:一定|总是|是|为)?(?:最短|小于直角边)',r'一次函数(?:的)?斜率(?:可以|一定)?(?:为|是)(?:零|0)',
        r'频率(?:总是|一定|必然)等于概率',r'(?:不同颜色|颜色)(?:总是|一定|必然)等可能')
    if any(re.search(pattern,claims) for pattern in false_claims):
        raise ValueError('false_concept_claim')
    # Unknown categorical mathematical assertions are not certified by the
    # language model. Only a small explicit invariant set is accepted; failures
    # retain a neutral support question in the UI instead of displaying prose.
    invariant_assertion=re.search(r'(?:一定|必然|总是|恒成立|法则是|定理是).{0,20}(?:为|是|等于|大于|小于|满足)',claims)
    known_true=(r'负数(?:乘以|除以)负数(?:一定|必然|总是)?(?:为|是|得)正',
        r'同号(?:相乘|相除)(?:的)?(?:积|商)?(?:一定|必然|总是)?(?:为|是|得)正',
        r'异号(?:相乘|相除)(?:的)?(?:积|商)?(?:一定|必然|总是)?(?:为|是|得)负')
    unchecked=claims
    for pattern in known_true:unchecked=re.sub(pattern,'',unchecked)
    for clause in re.split(r'[，,。；;！？?!]',unchecked):
        # Questions about an unknown relation are allowed; an unverified
        # declarative premise followed by "can you explain?" is not.
        neutral=re.sub(r'是否|是不是|是什么|为什么|为了|作为|称为','',clause)
        mathematical=any(k in neutral for k in ('负数','绝对值','相反数','同号','异号','概率','频率','函数','勾股','直角','斜边','等式','方程','平均数','分母','分子','倒数','运算','结果','答案'))
        if mathematical and re.search(r'是|为|等于|得到|得出|必然|一定|总是|满足|大于|小于',neutral):
            raise ValueError('unverified_concept_claim')
        observation=not require_question and any(word in clause for word in ('表达','记录','讲解','原文','尝试','自述','未说明','待核对','练习','复习','任务','支持','澄清'))
        if mathematical and not observation and not re.match(r'^(?:你(?:准备|会|能|如何|怎样|怎么|打算)|请|如何|怎样|怎么|为什么|是否|能否|可否|什么|哪|先(?:核对|说明|检查|表示|比较)|核对|检查|比较|澄清|整理|说明|表示|反思)',clause):
            raise ValueError('declarative_mathematics_requires_verification')
    if invariant_assertion and re.search(r'(?:一定|必然|总是|恒成立|法则是|定理是).{0,20}(?:为|是|等于|大于|小于|满足)',unchecked) and any(k in unchecked for k in ('数','绝对值','概率','频率','函数','三角','方程','等式')):
        raise ValueError('unverified_concept_claim')
    if check_explicit_equalities(unquoted)['status'] == 'contradiction_found':
        raise ValueError('false_arithmetic')
    if require_question and not ('?' in text or '？' in text):
        raise ValueError('question_required')
    return text


def validate(value, snapshot):
    try:
        question = check_text(value.question, snapshot['student_text'])
        if value.quote not in snapshot['student_text']:
            raise ValueError('fabricated_quote')
        voices = []
        if snapshot['purpose'] == 'generic_roundtable':
            if [v.role for v in value.voices] != ['proposer', 'critic', 'organizer']:
                raise ValueError('three_distinct_roles_required')
            if len({v.text for v in value.voices}) != 3:
                raise ValueError('duplicate_roles')
            for voice in value.voices:
                if voice.quote not in snapshot['student_text']:
                    raise ValueError('fabricated_role_quote')
                check_text(voice.text, snapshot['student_text'])
                voices.append(voice.model_dump())
            question = voices[-1]['text']
        elif value.voices:
            raise ValueError('unexpected_roles')
        if not value.teaching_decision:
            raise ValueError('missing_teaching_decision')
        decision = value.teaching_decision.model_dump()
        check_text(decision['reason'],snapshot['student_text'],require_question=False)
        if value.decision_reason:check_text(value.decision_reason,snapshot['student_text'],require_question=False)
        # Fading must follow actual unassisted success; a struggling learner may
        # always recover support. The model cannot turn uncertainty into mastery.
        signals = snapshot.get('teaching_signals', {})
        if decision['support'] == 'fade' and not signals.get('can_fade'):
            decision['support'] = 'maintain'
        if signals.get('needs_support'):
            decision['support'] = 'restore'
        verified_quotes=[m.group()[1:-1] for text in [question,decision['reason'],value.decision_reason,*[v['text'] for v in voices]]
            for m in re.finditer(r'“[^”]*”|「[^」]*」',text)]
        return {'question': question, 'voices': voices, 'teaching_decision': decision,'verified_quotes':verified_quotes,
                'rendering_mode': VERSION, 'diagnostic_code': 'grounded_model_response',
                'judgment': 'AI依据本轮表达生成追问；结构与显式算术已检查，解释质量及教学效果尚未认证。'}
    except (ValueError, TypeError):
        raise ProviderError('invalid_grounded_response') from None


def render(result):
    """Validate new stored artifacts at read boundaries, without rewriting prose."""
    try:
        if not result.get('quote') or len(result['question']) > 240:
            return None
        quotes='\n'.join(result.get('verified_quotes', [result['quote']]))
        check_text(result['question'], quotes)
        for voice in result.get('voices', []):
            check_text(voice['text'], quotes)
        if result.get('teaching_decision'):
            check_text(result['teaching_decision']['reason'],quotes,require_question=False)
        if result.get('decision_reason'):
            check_text(result['decision_reason'],quotes,require_question=False)
        return result.copy()
    except (ValueError, TypeError, KeyError):
        return None
