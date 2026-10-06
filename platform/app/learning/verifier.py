"""docs/04 §2：验证器。一切进化的信任基础——unverifiable 的样本永远不得进入统计。

实现：数学 = SymPy 等价验算；代码 = 受限子进程执行；两者皆不可用 = LLM rubric
（置信度不足 → unverifiable）。
"""
from __future__ import annotations

import re
import ast
import subprocess
import sys
import tempfile
from pathlib import Path

from sympy import Eq, simplify, Rational, Symbol

from app.core.schema import QuestionItem, TaxonomyLevel, Verdict

_SANITIZE = {"−": "-", "×": "*", "÷": "/", "＋": "+", "－": "-", "　": " "}


def _sanitize(expr: str) -> str:
    for bad, good in _SANITIZE.items():
        expr = expr.replace(bad, good)
    expr = expr.replace("^", "**").strip()
    # 隐式乘法补全：0.8x → 0.8*x，2(3x-1) → 2*(3x-1)
    expr = re.sub(r"(\d)\s*([a-zA-Z])", r"\1*\2", expr)
    expr = re.sub(r"(\d)\s*\(", r"\1*(", expr)
    return expr


def _extract_value(answer: str, var: str) -> str:
    """学生可能写 'x=3' / '3' / 'X=3'；取等号右侧作值。"""
    text = _sanitize(answer)
    if "=" in text:
        parts = text.split("=")
        if len(parts) != 2 or parts[0].strip().lower() != var.lower():
            raise ValueError("Expected one assignment to the assessment variable")
        text = parts[1].strip()
    return text.replace(var.upper(), var)


def _parse_arithmetic(source: str, variable: str | None = None):
    """Build SymPy arithmetic from a small AST; never eval student strings."""
    if len(source) > 256:
        raise ValueError("Expression too long")
    tree = ast.parse(source, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError("Expression too complex")

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int,float):
            literal = ast.get_source_segment(source,node)
            if literal is None or len(literal)>30:
                raise ValueError("Numeric literal too large")
            return Rational(literal)
        if isinstance(node, ast.Name) and variable and node.id == variable:
            return Symbol(variable)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op,ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Pow)):
            left,right = visit(node.left),visit(node.right)
            if isinstance(node.op,ast.Add): return left+right
            if isinstance(node.op,ast.Sub): return left-right
            if isinstance(node.op,ast.Mult): return left*right
            if isinstance(node.op,ast.Div):
                if right == 0: raise ValueError("Division by zero")
                return left/right
            if right.is_Integer is not True or abs(right)>12:
                raise ValueError("Power exceeds supported arithmetic bounds")
            return left**right
        raise ValueError("Only bounded arithmetic and the declared variable are supported")
    return visit(tree.body)


def verify_math(student_answer: str, item: QuestionItem) -> Verdict:
    """数值解验证：期望 item.answer = {"var": "x", "value": "3"}。"""
    var = item.answer.get("var", "x")
    try:
        expected = _parse_arithmetic(_sanitize(str(item.answer["value"])))
        got = _parse_arithmetic(_extract_value(student_answer, var))
        passed = bool(simplify(Eq(expected, got)))
        return _verdict_math(item, passed=passed,
                             explain=(f"解与标准答案 {var}={expected} 等价"
                                      if passed else "解与标准答案不等价"))
    except Exception:  # sympy 解析失败 → 不可验证（宁缺勿脏）
        return Verdict(
            status="unverifiable", score=0.0,
            taxonomy_level=item.taxonomy_level,
            misconception_hits=list(item.misconception_links),
            explainability="无法解析学生输入为数学表达式，转 LLM rubric 或判不可验证",
            verifier_id="verifier.sympy.1.0",
        )


def _verdict_math(item: QuestionItem, passed: bool, explain: str) -> Verdict:
    return Verdict(
        status="passed" if passed else "failed",
        score=1.0 if passed else 0.0,
        taxonomy_level=item.taxonomy_level,
        misconception_hits=list(item.misconception_links) if not passed else [],
        explainability=explain,
        verifier_id="verifier.sympy.1.0",
    )


def verify_expression(student_expr: str, item: QuestionItem) -> Verdict:
    """表达式题：期望 item.answer = {"expr": "0.8x-10"}，与学生表达式做符号等价。"""
    var = item.answer.get("var", "x")
    try:
        expected = _parse_arithmetic(_sanitize(str(item.answer["expr"])),var)
        got = _parse_arithmetic(_sanitize(student_expr),var)
        diff = simplify(expected - got)
        passed = diff == 0
        return _verdict_math(item, passed=passed,
                             explain="表达式符号等价" if passed else f"表达式不等价（差 {diff}）")
    except Exception:
        return Verdict(
            status="unverifiable", score=0.0,
            taxonomy_level=item.taxonomy_level,
            explainability="表达式解析失败", verifier_id="verifier.sympy.1.0",
        )


def verify_code(code: str, tests: list[dict], timeout: float = 5.0) -> Verdict:
    """Local trusted-code test runner, not a security sandbox or public API."""
    passed, total = 0, len(tests)
    explain_parts: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "solution.py"
        path.write_text(code, encoding="utf-8")
        for i, case in enumerate(tests):
            try:
                proc = subprocess.run(
                    [sys.executable, str(path)],
                    input=str(case.get("stdin", "")),
                    capture_output=True, text=True, timeout=timeout,
                )
                got = proc.stdout.strip()
                want = str(case.get("expect_stdout", "")).strip()
                if got == want:
                    passed += 1
                else:
                    explain_parts.append(f"用例{i+1}: 期望 {want!r} 实得 {got!r}")
            except subprocess.TimeoutExpired:
                explain_parts.append(f"用例{i+1}: 超时")
                break
    if total == 0:
        status = "unverifiable"
    elif passed == total:
        status = "passed"
    elif passed == 0:
        status = "failed"
    else:
        status = "partial"
    return Verdict(
        status=status, score=passed / total if total else 0.0,
        taxonomy_level=TaxonomyLevel.apply,
        explainability="; ".join(explain_parts) or "全部用例通过",
        verifier_id="verifier.pyexec.1.0",
    )


def verify_item(student_answer: str, item: QuestionItem, llm=None) -> Verdict:
    """按题型分发；SymPy 失败且提供了 LLM → rubric，置信不足仍 unverifiable。"""
    if item.answer.get("expr") is not None:
        verdict = verify_expression(student_answer, item)
    else:
        verdict = verify_math(student_answer, item)
    if verdict.status != "unverifiable" or llm is None:
        return verdict
    return verify_with_rubric(llm, student_answer, item.stem)


def verify_with_rubric(llm, artifact_text: str, task_desc: str) -> Verdict:
    """LLM rubric 评阅：置信度 < 0.6 → unverifiable（宁缺勿脏，docs/04 §2）。"""
    from app.llm.prompts import rubric_messages

    try:
        raw = llm.complete(rubric_messages(artifact_text, task_desc), temperature=0.1)
        import json

        from app.llm.client import extract_json

        data = json.loads(extract_json(raw))
        score = float(data.get("score", 0.0))
        confidence = float(data.get("confidence", 0.0))
        if confidence < 0.6:
            return Verdict(status="unverifiable", score=score,
                           explainability="rubric 置信度不足",
                           verifier_id="verifier.rubric.1.0")
        status = "passed" if score >= 0.8 else ("partial" if score >= 0.4 else "failed")
        return Verdict(status=status, score=score,
                       rubric={"confidence": confidence},
                       taxonomy_level=TaxonomyLevel(data.get("taxonomy_level", "apply")),
                       explainability=str(data.get("feedback", "")),
                       verifier_id="verifier.rubric.1.0")
    except Exception:
        return Verdict(status="unverifiable", score=0.0,
                       explainability="rubric 评阅失败",
                       verifier_id="verifier.rubric.1.0")
