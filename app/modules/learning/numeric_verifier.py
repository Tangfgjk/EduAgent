"""面向不可信输入的有界有理数验证；不使用 eval、sympify 或代码执行。"""
import ast
import re
from fractions import Fraction

VERIFIER_VERSION = "bounded-rational.v1"


def parse_number(text: str) -> Fraction:
    if len(text) > 128:
        raise ValueError("too_long")
    text = text.translate(str.maketrans({"−": "-", "×": "*", "÷": "/", "＋": "+", "－": "-", "＝": "="})).strip()
    if "=" in text:
        match = re.fullmatch(r"[xX]\s*=\s*([^=]+)", text)
        if not match:
            raise ValueError("invalid_assignment")
        text = match[1].strip()
    if not re.fullmatch(r"[0-9.\s+*/()\-]+", text):
        raise ValueError("unsupported_notation")
    tree = ast.parse(text, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 48:
        raise ValueError("too_complex")
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            token = ast.get_source_segment(text, node)
            if len(token) > 16:
                raise ValueError("number_too_large")
            value = Fraction(token)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add): value = a + b
            elif isinstance(node.op, ast.Sub): value = a - b
            elif isinstance(node.op, ast.Mult): value = a * b
            else: value = a / b
        else:
            raise ValueError("unsupported_expression")
        if value.numerator.bit_length() > 128 or value.denominator.bit_length() > 128:
            raise ValueError("number_too_large")
        return value
    return visit(tree.body)


def verify(answer: str, expected: str) -> str:
    try:
        return "passed" if parse_number(answer) == Fraction(expected) else "failed"
    except (ValueError, SyntaxError, ZeroDivisionError, RecursionError, OverflowError, TypeError):
        return "unverifiable"
