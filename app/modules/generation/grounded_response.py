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


def check_text(text):
    # Reject executable content, claims of certification and direct answer delivery.
    # This intentionally does not claim to prove arbitrary Chinese mathematics.
    if re.search(r'https?://|<[^>]+>|```|javascript:|data:|忽略.{0,6}(规则|指令)|关闭.{0,4}(核验|验证)|已(经)?掌握|教学效果已', text, re.I):
        raise ValueError('unsafe_teaching_text')
    unquoted=re.sub(r'“[^”]*”|「[^」]*」', '', text)
    if re.search(r'(?:最终)?(?:答案|正确结果)\s*(?:是|为|=|：)\s*(?:[-+−]?\d|[零一二三四五六七八九十负正])|(?:所以|因此)(?:得|得到|等于)\s*[-+0-9]',unquoted):
        raise ValueError('direct_answer_delivery')
    if check_explicit_equalities(text)['status'] == 'contradiction_found':
        raise ValueError('false_arithmetic')
    if not ('?' in text or '？' in text):
        raise ValueError('question_required')
    return text


def validate(value, snapshot):
    try:
        question = check_text(value.question)
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
                check_text(voice.text)
                voices.append(voice.model_dump())
            question = voices[-1]['text']
        elif value.voices:
            raise ValueError('unexpected_roles')
        if not value.teaching_decision:
            raise ValueError('missing_teaching_decision')
        decision = value.teaching_decision.model_dump()
        # Fading must follow actual unassisted success; a struggling learner may
        # always recover support. The model cannot turn uncertainty into mastery.
        signals = snapshot.get('teaching_signals', {})
        if decision['support'] == 'fade' and not signals.get('can_fade'):
            decision['support'] = 'maintain'
        if signals.get('needs_support'):
            decision['support'] = 'restore'
        return {'question': question, 'voices': voices, 'teaching_decision': decision,
                'rendering_mode': VERSION, 'diagnostic_code': 'grounded_model_response',
                'judgment': 'AI依据本轮表达生成追问；结构与显式算术已检查，解释质量及教学效果尚未认证。'}
    except (ValueError, TypeError):
        raise ProviderError('invalid_grounded_response') from None


def render(result):
    """Validate new stored artifacts at read boundaries, without rewriting prose."""
    try:
        if not result.get('quote') or len(result['question']) > 240:
            return None
        check_text(result['question'])
        for voice in result.get('voices', []):
            check_text(voice['text'])
        return result.copy()
    except (ValueError, TypeError, KeyError):
        return None
