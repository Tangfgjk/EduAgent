"""Conservative answer interpretation, separate from the mathematical oracle.

Only an explicit final-answer assertion is extracted here. Ambiguous language
is retained as a clarification, never guessed from the first number in prose.
"""
from dataclasses import dataclass
import re

from app.modules.learning.numeric_verifier import parse_number, verify

VERSION = 'answer-interpretation.v1'


@dataclass(frozen=True)
class Interpretation:
    original: str
    candidate: str | None
    reason: str
    evidence: str | None = None
    version: str = VERSION

    def check(self, expected: str) -> str:
        return verify(self.candidate, expected) if self.candidate is not None else 'unverifiable'


_ASSERTION = re.compile(r'(?:(?:我(?:认为|觉得|算出|得到)|这(?:个|道题的)?)\s*)?(?:(?:最终|最后的|我的)?(?:答案|结果)(?:应该|应当)?(?:是|为|等于|=|：|:)?|(?:最终|现在)(?:答案)?(?:改为|改成|是)|所以(?:等于|是)?)\s*(.+)')
_CORRECTION = re.compile(r'(?:现在|最终|最后)(?:我)?(?:把答案|答案)?(?:改为|改成|是)\s*(.+)')


def interpret(text: str) -> Interpretation:
    original = text
    if not isinstance(text, str) or not text.strip() or len(text) > 1500:
        return Interpretation(original, None, 'invalid_input')
    text = text.strip().rstrip('。！!；;')
    try:
        parse_number(text)
        return Interpretation(original, text, 'numeric_expression', text)
    except (ValueError, SyntaxError, ZeroDivisionError, RecursionError, OverflowError):
        pass
    correction = list(_CORRECTION.finditer(text))
    match = correction[-1] if correction else _ASSERTION.fullmatch(text)
    if match is None:
        return Interpretation(original, None, '请明确最终答案；没有将句子中的数字自动当作答案。')
    candidate = match[1].strip()
    # Explanation remains the original learning record, not mathematical proof.
    candidate = re.split(r'[，,。；;]?\s*(?:因为|理由是|依据是|我的思路是)', candidate, maxsplit=1)[0].strip()
    candidate = candidate.rstrip('。！!；;，,')
    # Restrict correction prefixes as well; quoted/problem numbers are not intent.
    if correction and not re.fullmatch(r'(?:刚才|之前|原来|我)[^。！？!?\n]{0,100}[，,；;]\s*', text[:match.start()]):
        return Interpretation(original, None, '请单独确认更正后的最终答案。')
    if any(word in candidate for word in ('不是', '不等于', '或者', '或', '可能', '大概', '也许', '？', '?', 'cm', '厘米', '米', '平方')):
        return Interpretation(original, None, '答案存在否定、候选、不确定或单位，请澄清后再核验。')
    try:
        parse_number(candidate)
    except (ValueError, SyntaxError, ZeroDivisionError, RecursionError, OverflowError):
        return Interpretation(original, None, '尚不能可靠提取唯一数值；请明确答案或保留待评阅。')
    return Interpretation(original, candidate, 'explicit_final_answer', match[0])


def check_explicit_equalities(text: str) -> dict:
    """Check written arithmetic equalities only; never certify a prose proof."""
    normalized=text.translate(str.maketrans({'−':'-','×':'*','÷':'/','＝':'='}))
    claims=[]
    for match in list(re.finditer(r'(?<![0-9A-Za-z_.])([0-9()+*/.\s-]{1,60})\s*=\s*([0-9()+*/.\s-]{1,60})(?![0-9A-Za-z_.])',normalized))[:8]:
        # A negated/quoted claim is not asserted as the student's justification.
        if re.search(r'(?:不是|不对|例如错误|题目|假设|有人说|不是说)[^，,。；;]{0,12}$',normalized[:match.start()]):continue
        try:
            left,right=parse_number(match[1].strip()),parse_number(match[2].strip())
        except (ValueError,SyntaxError,ZeroDivisionError,RecursionError,OverflowError):continue
        claims.append({'claim':match[0].strip(),'consistent':left==right})
    return {'status':'contradiction_found' if any(not c['consistent'] for c in claims) else 'arithmetic_checked' if claims else 'not_assessed',
        'claims':claims,'proof_assessed':False}
