"""Type checking of source expressions without coercing Boolean/numeric values."""

from __future__ import annotations

from .parser import BinaryExpr, LiteralExpr, RefExpr, TernaryExpr, UnaryExpr, ExpressionParser

NUMERIC = {"Integer", "Real"}


def parse_checked_expression(text):
    """Validate complete input while retaining the original parser's grouping."""
    import re
    depth = 0
    for token in re.findall(r"'[^']*'|\"[^\"]*\"|[()]", text):
        if token == '(':
            depth += 1
        elif token == ')':
            depth -= 1
        if depth < 0:
            raise ValueError('unmatched expression parenthesis')
    if depth:
        raise ValueError('unclosed expression parenthesis')
    parser = ExpressionParser(text)
    expression = parser.parse()
    if parser.text[parser.pos:].strip():
        raise ValueError(f'unparsed expression: {parser.text[parser.pos:]}')
    expression_type(expression)
    return expression


def expression_type(expr, references=None):
    """Check known operand types; a supplied resolver must resolve every reference.

    Without a resolver this performs the parser's literal/operator checks. Full
    source checking supplies one; Unknown is never a proof-side declared sort.
    """
    if isinstance(expr, LiteralExpr):
        if type(expr.value) is bool:
            return "Boolean"
        if type(expr.value) is int:
            return "Integer"
        if type(expr.value) is float:
            return "Real"
        raise ValueError(f"unsupported source literal: {expr.value!r}")
    if isinstance(expr, RefExpr):
        if references is None:
            return "Unknown"
        result = references(expr.path)
        if result not in NUMERIC | {"Boolean"}:
            raise ValueError(f"unresolved/unsupported type of {'.'.join(expr.path)}")
        return result
    if isinstance(expr, UnaryExpr):
        operand = expression_type(expr.operand, references)
        expected = {"Boolean"} if expr.op == "not" else NUMERIC
        if expr.op not in {"not", "-"} or operand not in expected | {"Unknown"}:
            raise ValueError(f"invalid operand type for {expr.op}: {operand}")
        return "Boolean" if expr.op == "not" else operand
    if isinstance(expr, BinaryExpr):
        left = expression_type(expr.left, references)
        right = expression_type(expr.right, references)
        if expr.op in {"and", "or", "implies"}:
            allowed = {"Boolean", "Unknown"}
            result = "Boolean"
        elif expr.op in {"+", "-", "*", "/", "<", "<=", ">", ">="}:
            allowed = NUMERIC | {"Unknown"}
            result = ("Boolean" if expr.op in {"<", "<=", ">", ">="} else
                      "Real" if expr.op == "/" or "Real" in {left, right} else
                      "Unknown" if "Unknown" in {left, right} else "Integer")
        elif expr.op == "==":
            if "Unknown" not in {left, right} and left != right and not {left, right} <= NUMERIC:
                raise ValueError(f"incompatible equality operands: {left}, {right}")
            return "Boolean"
        else:
            raise ValueError(f"unsupported source operator: {expr.op}")
        if left not in allowed or right not in allowed:
            raise ValueError(f"invalid operands for {expr.op}: {left}, {right}")
        return result
    if isinstance(expr, TernaryExpr):
        cond = expression_type(expr.condition, references)
        if cond not in {"Boolean", "Unknown"}:
            raise ValueError("conditional guard is not Boolean")
        left = expression_type(expr.true_expr, references)
        right = expression_type(expr.false_expr, references)
        if left == right:
            return left
        if {left, right} <= NUMERIC:
            return "Real"
        if "Unknown" in {left, right}:
            return "Unknown"
        raise ValueError(f"incompatible conditional branches: {left}, {right}")
    raise ValueError(f"unsupported expression: {type(expr).__name__}")
