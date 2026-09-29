"""Exact expression operations used by discretization reduction producers."""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from typing import Any, Iterable

from clarity.certification.equations import Const, Expr, Ite, Op, RawRef, Var

from .proof_rules import (
    Comparison,
    ProofDeferred,
    expr_to_dict,
    expression_symbols,
)


INTERVAL_TIME = "proof_interval_time"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def expression_hash(expr: Expr) -> str:
    return hashlib.sha256(_canonical_bytes(expr_to_dict(expr))).hexdigest()


def raw_reference_names(expression: Expr) -> set[str]:
    """Return the exact raw-reference paths contained in an expression."""

    if isinstance(expression, RawRef):
        return {expression.path}
    if isinstance(expression, Op):
        result: set[str] = set()
        for argument in expression.args:
            result.update(raw_reference_names(argument))
        return result
    if isinstance(expression, Ite):
        return (
            raw_reference_names(expression.cond)
            | raw_reference_names(expression.then_expr)
            | raw_reference_names(expression.else_expr)
        )
    return set()




def _and(expressions: Iterable[Expr]) -> Expr:
    items = tuple(expressions)
    if not items:
        return Const(True)
    if len(items) == 1:
        return items[0]
    return Op("and", items)


def _comparison_expression(comparison: Comparison) -> Expr:
    return Op(comparison.op, (comparison.left, comparison.right))


def _time_degree(expr: Expr) -> int | None:
    """Return the exact polynomial degree in the interval time alone."""

    if isinstance(expr, (Const, RawRef)):
        return 0
    if isinstance(expr, Var):
        return 1 if expr.name == INTERVAL_TIME else 0
    if isinstance(expr, Ite):
        condition = _time_degree(expr.cond)
        then_degree = _time_degree(expr.then_expr)
        else_degree = _time_degree(expr.else_expr)
        if condition != 0 or then_degree is None or else_degree is None:
            return None
        return max(then_degree, else_degree)
    if not isinstance(expr, Op):
        return None
    degrees = [_time_degree(arg) for arg in expr.args]
    if any(value is None for value in degrees):
        return None
    known = [int(value) for value in degrees if value is not None]
    if expr.op in {"+", "-"}:
        return max(known, default=0)
    if expr.op == "*" and len(known) == 2:
        return known[0] + known[1]
    if expr.op == "/" and len(known) == 2 and known[1] == 0:
        return known[0]
    if expr.op in {">", "<", ">=", "<=", "==", "and", "or", "not", "implies"}:
        return max(known, default=0)
    return None


def _is_interval_bound(comparison: Comparison) -> bool:
    expression = _comparison_expression(comparison)
    symbols = expression_symbols(expression)
    if symbols != {INTERVAL_TIME}:
        return False
    constants = [
        item.value
        for item in (comparison.left, comparison.right)
        if isinstance(item, Const)
    ]
    return bool(constants)


def _first_ite(expr: Expr) -> Ite | None:
    if isinstance(expr, Ite):
        return expr
    if isinstance(expr, Op):
        for arg in expr.args:
            found = _first_ite(arg)
            if found is not None:
                return found
    return None


def _assume_condition(expr: Expr, condition: Expr, truth: bool) -> Expr:
    if expr == condition:
        return Const(truth)
    if (
        isinstance(expr, Op)
        and expr.op == "not"
        and len(expr.args) == 1
        and expr.args[0] == condition
    ):
        return Const(not truth)
    if isinstance(expr, Op):
        return simplify(Op(
            expr.op,
            tuple(
                _assume_condition(argument, condition, truth)
                for argument in expr.args
            ),
        ))
    if isinstance(expr, Ite):
        return simplify(Ite(
            _assume_condition(expr.cond, condition, truth),
            _assume_condition(expr.then_expr, condition, truth),
            _assume_condition(expr.else_expr, condition, truth),
        ))
    return expr


def _split_conditionals(expr: Expr, *, limit: int = 512) -> list[tuple[str, Expr]]:
    pending: list[tuple[str, Expr]] = [("root", simplify(expr))]
    complete: list[tuple[str, Expr]] = []
    while pending:
        branch_id, current = pending.pop(0)
        conditional = _first_ite(current)
        if conditional is None:
            complete.append((branch_id, current))
            continue
        if len(pending) + len(complete) + 2 > limit:
            raise ProofDeferred(
                "INCOMPLETE_CASE_COVERAGE",
                f"conditional case split exceeds {limit} branches",
            )
        then_expression = _assume_condition(current, conditional.cond, True)
        else_expression = _assume_condition(current, conditional.cond, False)
        pending.append((
            branch_id + ".then",
            simplify(_and([conditional.cond, then_expression])),
        ))
        pending.append((
            branch_id + ".else",
            simplify(_and([Op("not", (conditional.cond,)), else_expression])),
        ))
    return complete


def simplify(expr: Expr) -> Expr:
    """Apply only exact local Boolean and conditional simplifications."""

    if isinstance(expr, (Const, Var, RawRef)):
        return expr
    if isinstance(expr, Ite):
        condition = simplify(expr.cond)
        then_expr = simplify(expr.then_expr)
        else_expr = simplify(expr.else_expr)
        if isinstance(condition, Const) and isinstance(condition.value, bool):
            return then_expr if condition.value else else_expr
        if then_expr == else_expr:
            return then_expr
        return Ite(condition, then_expr, else_expr)
    if not isinstance(expr, Op):
        return expr
    args = tuple(simplify(arg) for arg in expr.args)
    if args and all(isinstance(arg, Const) for arg in args):
        values = [arg.value for arg in args]
        if expr.op == "==" and len(values) == 2:
            return Const(values[0] == values[1])
        if expr.op in {">", "<", ">=", "<="} and len(values) == 2:
            try:
                left = Fraction(str(values[0]))
                right = Fraction(str(values[1]))
                result = {
                    ">": left > right,
                    "<": left < right,
                    ">=": left >= right,
                    "<=": left <= right,
                }[expr.op]
                return Const(result)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        if expr.op in {"+", "-", "*", "/"}:
            try:
                numbers = [Fraction(str(value)) for value in values]
                if expr.op == "+":
                    value = sum(numbers, Fraction(0))
                elif expr.op == "-":
                    value = -numbers[0] if len(numbers) == 1 else numbers[0] - sum(numbers[1:], Fraction(0))
                elif expr.op == "*" and len(numbers) == 2:
                    value = numbers[0] * numbers[1]
                elif expr.op == "/" and len(numbers) == 2 and numbers[1] != 0:
                    value = numbers[0] / numbers[1]
                else:
                    value = None
                if value is not None:
                    return Const(
                        value.numerator
                        if value.denominator == 1
                        else f"{value.numerator}/{value.denominator}"
                    )
            except (TypeError, ValueError, ZeroDivisionError):
                pass
    if expr.op == "not" and len(args) == 1:
        if isinstance(args[0], Const) and isinstance(args[0].value, bool):
            return Const(not args[0].value)
        if isinstance(args[0], Op) and args[0].op == "not":
            return args[0].args[0]
    if expr.op in {"and", "or"}:
        flattened: list[Expr] = []
        for arg in args:
            if isinstance(arg, Op) and arg.op == expr.op:
                flattened.extend(arg.args)
            else:
                flattened.append(arg)
        identity = expr.op == "and"
        absorbing = not identity
        if any(
            isinstance(arg, Const)
            and isinstance(arg.value, bool)
            and arg.value == absorbing
            for arg in flattened
        ):
            return Const(absorbing)
        kept = [
            arg
            for arg in flattened
            if not (
                isinstance(arg, Const)
                and isinstance(arg.value, bool)
                and arg.value == identity
            )
        ]
        if not kept:
            return Const(identity)
        if len(kept) == 1:
            return kept[0]
        return Op(expr.op, tuple(kept))
    if expr.op == "implies" and len(args) == 2:
        left, right = args
        if isinstance(left, Const) and isinstance(left.value, bool):
            return right if left.value else Const(True)
        if isinstance(right, Const) and isinstance(right.value, bool):
            return Const(True) if right.value else simplify(Op("not", (left,)))
    if expr.op == "==" and len(args) == 2:
        for boolean_value, other in ((args[0], args[1]), (args[1], args[0])):
            if not (
                isinstance(boolean_value, Const)
                and isinstance(boolean_value.value, bool)
            ):
                continue
            if isinstance(other, Op) and other.op in {
                "not", "and", "or", "implies", "==", ">", "<", ">=", "<="
            }:
                return other if boolean_value.value else simplify(Op("not", (other,)))
    if expr.op == "==" and len(args) == 2 and args[0] == args[1]:
        return Const(True)
    return Op(expr.op, args)
