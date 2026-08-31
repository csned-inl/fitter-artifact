"""Independent exact expression replay for the certificate verifier."""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from typing import Any, Iterable

from clarity.certification.equations import Const, Expr, Ite, Op, RawRef, Var

from .numbers import (
    LinearInequality,
    exact_linear_infeasible,
    fraction_text,
    parse_fraction,
)


INTERVAL_TIME = "proof_interval_time"


def expr_to_dict(expr: Expr) -> dict[str, Any]:
    if isinstance(expr, Const):
        return {"type": "const", "value": expr.value}
    if isinstance(expr, Var):
        return {"type": "var", "name": expr.name}
    if isinstance(expr, RawRef):
        return {"type": "raw_ref", "path": expr.path}
    if isinstance(expr, Op):
        return {
            "type": "op",
            "op": expr.op,
            "args": [expr_to_dict(arg) for arg in expr.args],
        }
    if isinstance(expr, Ite):
        return {
            "type": "ite",
            "cond": expr_to_dict(expr.cond),
            "then": expr_to_dict(expr.then_expr),
            "else": expr_to_dict(expr.else_expr),
        }
    raise ProofDeferred("UNSUPPORTED_EXPRESSION", type(expr).__name__)


def expression_symbols(expr: Expr) -> set[str]:
    if isinstance(expr, Const):
        return set()
    if isinstance(expr, Var):
        return {expr.name}
    if isinstance(expr, RawRef):
        return {expr.path}
    if isinstance(expr, Op):
        out: set[str] = set()
        for arg in expr.args:
            out |= expression_symbols(arg)
        return out
    if isinstance(expr, Ite):
        return (
            expression_symbols(expr.cond)
            | expression_symbols(expr.then_expr)
            | expression_symbols(expr.else_expr)
        )
    return set()


def substitute(expr: Expr, values: dict[str, Expr]) -> Expr:
    if isinstance(expr, Var) and expr.name in values:
        return values[expr.name]
    if isinstance(expr, RawRef) and expr.path in values:
        return values[expr.path]
    if isinstance(expr, Op):
        return Op(expr.op, tuple(substitute(arg, values) for arg in expr.args))
    if isinstance(expr, Ite):
        return Ite(
            substitute(expr.cond, values),
            substitute(expr.then_expr, values),
            substitute(expr.else_expr, values),
        )
    return expr


def expression_hash(expr: Expr) -> str:
    return hashlib.sha256(json.dumps(
        expr_to_dict(expr),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


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


def _and(expressions: list[Expr]) -> Expr:
    if not expressions:
        return Const(True)
    if len(expressions) == 1:
        return expressions[0]
    return _canonical(Op("and", tuple(expressions)))


def _or(expressions: list[Expr]) -> Expr:
    if not expressions:
        return Const(False)
    if len(expressions) == 1:
        return expressions[0]
    return _canonical(Op("or", tuple(expressions)))


def _canonical(expression: Expr) -> Expr:
    if isinstance(expression, (Const, Var, RawRef)):
        return expression
    if isinstance(expression, Ite):
        return simplify(Ite(
            _canonical(expression.cond),
            _canonical(expression.then_expr),
            _canonical(expression.else_expr),
        ))
    if not isinstance(expression, Op):
        return expression
    arguments = tuple(_canonical(item) for item in expression.args)
    normalized = simplify(Op(expression.op, arguments))
    if not isinstance(normalized, Op) or normalized.op not in {"and", "or"}:
        return normalized
    flattened: list[Expr] = []
    for item in normalized.args:
        if isinstance(item, Op) and item.op == normalized.op:
            flattened.extend(item.args)
        else:
            flattened.append(item)
    unique = {expression_hash(item): item for item in flattened}
    ordered = tuple(unique[key] for key in sorted(unique))
    if len(ordered) == 1:
        return ordered[0]
    return simplify(Op(normalized.op, ordered))


def _is_boolean(expression: Expr, boolean_variables: set[str]) -> bool:
    if isinstance(expression, Const):
        return isinstance(expression.value, bool)
    if isinstance(expression, Var):
        return expression.name in boolean_variables
    if isinstance(expression, Ite):
        return _is_boolean(
            expression.then_expr,
            boolean_variables,
        ) and _is_boolean(expression.else_expr, boolean_variables)
    if isinstance(expression, Op):
        return expression.op in {
            "not", "and", "or", "implies", "==", ">", "<", ">=", "<=",
        }
    return False


def _normal_boolean(
    expression: Expr,
    boolean_variables: set[str],
    *,
    truth: bool = True,
) -> Expr:
    """Push logical negation to atoms without distributing conjunctions."""

    expression = _canonical(expression)
    if isinstance(expression, Const) and isinstance(expression.value, bool):
        return Const(expression.value is truth)
    if isinstance(expression, Var) and expression.name in boolean_variables:
        return expression if truth else Op("not", (expression,))
    if isinstance(expression, Ite) and _is_boolean(
        expression,
        boolean_variables,
    ):
        return _or([
            _and([
                _normal_boolean(
                    expression.cond,
                    boolean_variables,
                    truth=True,
                ),
                _normal_boolean(
                    expression.then_expr,
                    boolean_variables,
                    truth=truth,
                ),
            ]),
            _and([
                _normal_boolean(
                    expression.cond,
                    boolean_variables,
                    truth=False,
                ),
                _normal_boolean(
                    expression.else_expr,
                    boolean_variables,
                    truth=truth,
                ),
            ]),
        ])
    if not isinstance(expression, Op):
        return expression if truth else Op("not", (expression,))
    if expression.op == "not":
        return _normal_boolean(
            expression.args[0],
            boolean_variables,
            truth=not truth,
        )
    if expression.op == "implies":
        left, right = expression.args
        expanded = _or([
            _normal_boolean(left, boolean_variables, truth=False),
            _normal_boolean(right, boolean_variables, truth=True),
        ])
        return _normal_boolean(expanded, boolean_variables, truth=truth)
    if expression.op in {"and", "or"}:
        operation = expression.op if truth else (
            "or" if expression.op == "and" else "and"
        )
        items = [
            _normal_boolean(
                item,
                boolean_variables,
                truth=truth,
            )
            for item in expression.args
        ]
        return _and(items) if operation == "and" else _or(items)
    if expression.op == "==" and all(
        _is_boolean(item, boolean_variables) for item in expression.args
    ):
        left, right = expression.args
        if truth:
            return _or([
                _and([
                    _normal_boolean(left, boolean_variables, truth=True),
                    _normal_boolean(right, boolean_variables, truth=True),
                ]),
                _and([
                    _normal_boolean(left, boolean_variables, truth=False),
                    _normal_boolean(right, boolean_variables, truth=False),
                ]),
            ])
        return _or([
            _and([
                _normal_boolean(left, boolean_variables, truth=True),
                _normal_boolean(right, boolean_variables, truth=False),
            ]),
            _and([
                _normal_boolean(left, boolean_variables, truth=False),
                _normal_boolean(right, boolean_variables, truth=True),
            ]),
        ])
    if expression.op in {"==", ">", "<", ">=", "<="}:
        left, right = expression.args
        if truth:
            if expression.op == "==":
                return _and([Op("<=", (left, right)), Op(">=", (left, right))])
            return expression
        inverse = {">": "<=", "<": ">=", ">=": "<", "<=": ">"}
        if expression.op == "==":
            return _or([Op("<", (left, right)), Op(">", (left, right))])
        return Op(inverse[expression.op], (left, right))
    return expression if truth else Op("not", (expression,))


def _conjuncts(expression: Expr) -> list[Expr]:
    if isinstance(expression, Const) and expression.value is True:
        return []
    if isinstance(expression, Op) and expression.op == "and":
        result: list[Expr] = []
        for item in expression.args:
            result.extend(_conjuncts(item))
        return result
    return [expression]


def _common_conjuncts(expression: Expr) -> dict[str, Expr]:
    if isinstance(expression, Const):
        return {}
    if isinstance(expression, Op) and expression.op == "and":
        result: dict[str, Expr] = {}
        for item in expression.args:
            result.update(_common_conjuncts(item))
        return result
    if isinstance(expression, Op) and expression.op == "or":
        children = [_common_conjuncts(item) for item in expression.args]
        if not children:
            return {}
        shared = set(children[0])
        for child in children[1:]:
            shared.intersection_update(child)
        return {key: children[0][key] for key in sorted(shared)}
    if isinstance(expression, Ite):
        return {}
    return {expression_hash(expression): expression}


def _first_ite(expression: Expr) -> Ite | None:
    if isinstance(expression, Ite):
        return expression
    if isinstance(expression, Op):
        for item in expression.args:
            found = _first_ite(item)
            if found is not None:
                return found
    return None


def _first_or(expression: Expr) -> Op | None:
    if isinstance(expression, Op) and expression.op == "or":
        return expression
    if isinstance(expression, Op):
        for item in expression.args:
            found = _first_or(item)
            if found is not None:
                return found
    if isinstance(expression, Ite):
        for item in (
            expression.cond,
            expression.then_expr,
            expression.else_expr,
        ):
            found = _first_or(item)
            if found is not None:
                return found
    return None


def _replace(expression: Expr, target: Expr, replacement: Expr) -> Expr:
    if expression == target:
        return replacement
    if isinstance(expression, Op):
        return _canonical(Op(
            expression.op,
            tuple(_replace(item, target, replacement) for item in expression.args),
        ))
    if isinstance(expression, Ite):
        return _canonical(Ite(
            _replace(expression.cond, target, replacement),
            _replace(expression.then_expr, target, replacement),
            _replace(expression.else_expr, target, replacement),
        ))
    return expression


def _affine_interval_endpoint_split(
    expression: Expr,
) -> tuple[list[Expr], dict[str, Any]] | None:
    """Reduce one affine interval comparison to its two endpoints."""

    conjuncts = _conjuncts(expression)
    lower_bound: Expr | None = None
    upper_bound: Expr | None = None
    interval_bounds: list[Expr] = []
    for item in conjuncts:
        if not isinstance(item, Op) or len(item.args) != 2:
            continue
        left, right = item.args
        if (
            isinstance(left, Var)
            and left.name == INTERVAL_TIME
            and isinstance(right, Const)
            and right.value == 0
            and item.op == ">="
        ):
            lower_bound = item
            interval_bounds.append(item)
        elif (
            isinstance(left, Var)
            and left.name == INTERVAL_TIME
            and item.op == "<="
            and INTERVAL_TIME not in expression_symbols(right)
        ):
            upper_bound = right
            interval_bounds.append(item)
    if lower_bound is None or upper_bound is None:
        return None

    body = [item for item in conjuncts if item not in interval_bounds]
    time_gates = [
        item
        for item in body
        if INTERVAL_TIME in expression_symbols(item)
        and all(
            name == INTERVAL_TIME or "time" in name.lower()
            for name in expression_symbols(item)
        )
    ]
    reduced_body = [item for item in body if item not in time_gates]
    changing = [
        item
        for item in reduced_body
        if INTERVAL_TIME in expression_symbols(item)
    ]
    if len(changing) != 1:
        return None
    changing_item = changing[0]
    if (
        not isinstance(changing_item, Op)
        or changing_item.op not in {">", "<", ">=", "<="}
        or len(changing_item.args) != 2
        or _time_degree(changing_item.args[0]) is None
        or int(_time_degree(changing_item.args[0]) or 0) > 1
        or _time_degree(changing_item.args[1]) is None
        or int(_time_degree(changing_item.args[1]) or 0) > 1
    ):
        return None

    relaxed_expression = _and(reduced_body)
    endpoints = [Const(0), upper_bound]
    children = [
        _canonical(substitute(
            relaxed_expression,
            {INTERVAL_TIME: endpoint},
        ))
        for endpoint in endpoints
    ]
    return children, {
        "relaxed_expression": relaxed_expression,
        "removed_interval_bounds": interval_bounds,
        "removed_time_gates": time_gates,
        "changing_comparison": changing_item,
        "time_variable": INTERVAL_TIME,
        "endpoints": endpoints,
    }
_factored_and = _and
_factored_or = _or
_factored_canonical = _canonical
_factored_normal_boolean = _normal_boolean
_factored_first_ite = _first_ite
_factored_first_or = _first_or
_factored_replace = _replace


def _serialized_expression_hash(expression: Any) -> str:
    return hashlib.sha256(json.dumps(
        expression,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _serialized_conjuncts(expression: Any) -> list[Any]:
    if (
        isinstance(expression, dict)
        and expression.get("type") == "const"
        and expression.get("value") is True
    ):
        return []
    if (
        isinstance(expression, dict)
        and expression.get("type") == "op"
        and expression.get("op") == "and"
        and isinstance(expression.get("args"), list)
    ):
        result: list[Any] = []
        for argument in expression["args"]:
            result.extend(_serialized_conjuncts(argument))
        return result
    return [expression]


def _serialized_conjunction(expressions: list[Any]) -> Any:
    if not expressions:
        return {"type": "const", "value": True}
    if len(expressions) == 1:
        return expressions[0]
    return {"type": "op", "op": "and", "args": expressions}


def _normalized_bound_values(record: Any) -> tuple[dict[str, Fraction], Fraction, bool]:
    if not isinstance(record, dict) or not isinstance(record.get("coefficients"), dict):
        raise ValueError("normalized bound is malformed")
    coefficients = {
        str(name): parse_fraction(value)
        for name, value in record["coefficients"].items()
    }
    bound = parse_fraction(record.get("bound"))
    strict = record.get("strict")
    if not isinstance(strict, bool) or not coefficients:
        raise ValueError("normalized bound is malformed")
    return coefficients, bound, strict


def _serialized_linear_form(
    expression: Any,
) -> tuple[dict[str, Fraction], Fraction]:
    if not isinstance(expression, dict):
        raise ValueError("linear expression is malformed")
    kind = expression.get("type")
    if kind == "const":
        value = expression.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError("linear constant is malformed")
        return {}, Fraction(str(value))
    if kind in {"var", "raw_ref"}:
        field = "name" if kind == "var" else "path"
        name = expression.get(field)
        if not isinstance(name, str):
            raise ValueError("linear variable is malformed")
        return {name: Fraction(1)}, Fraction(0)
    if kind != "op" or not isinstance(expression.get("args"), list):
        raise ValueError("linear operation is malformed")

    operation = expression.get("op")
    arguments = expression["args"]

    def add(
        left: tuple[dict[str, Fraction], Fraction],
        right: tuple[dict[str, Fraction], Fraction],
        scale: Fraction = Fraction(1),
    ) -> tuple[dict[str, Fraction], Fraction]:
        coefficients = dict(left[0])
        for name, value in right[0].items():
            coefficients[name] = coefficients.get(name, Fraction(0)) + scale * value
        return (
            {name: value for name, value in coefficients.items() if value},
            left[1] + scale * right[1],
        )

    if operation == "+":
        result = ({}, Fraction(0))
        for argument in arguments:
            result = add(result, _serialized_linear_form(argument))
        return result
    if operation == "-" and arguments:
        if len(arguments) == 1:
            coefficients, constant = _serialized_linear_form(arguments[0])
            return (
                {name: -value for name, value in coefficients.items()},
                -constant,
            )
        result = _serialized_linear_form(arguments[0])
        for argument in arguments[1:]:
            result = add(result, _serialized_linear_form(argument), Fraction(-1))
        return result
    if operation == "*" and len(arguments) == 2:
        left = _serialized_linear_form(arguments[0])
        right = _serialized_linear_form(arguments[1])
        if left[0] and right[0]:
            raise ValueError("variable multiplication is not linear")
        variable, constant = (left, right[1]) if left[0] else (right, left[1])
        return (
            {name: value * constant for name, value in variable[0].items()},
            variable[1] * constant,
        )
    if operation == "/" and len(arguments) == 2:
        numerator = _serialized_linear_form(arguments[0])
        denominator = _serialized_linear_form(arguments[1])
        if denominator[0] or denominator[1] == 0:
            raise ValueError("linear divisor is malformed")
        scale = Fraction(1) / denominator[1]
        return (
            {name: value * scale for name, value in numerator[0].items()},
            numerator[1] * scale,
        )
    raise ValueError("operation is not linear")


def _serialized_normalized_linear_bound(
    expression: Any,
) -> tuple[dict[str, Fraction], Fraction, bool]:
    if not isinstance(expression, dict) or expression.get("type") != "op":
        raise ValueError("linear comparison is malformed")
    operation = expression.get("op")
    arguments = expression.get("args")
    if operation not in {"<", "<=", ">", ">="} or not isinstance(
        arguments, list
    ) or len(arguments) != 2:
        raise ValueError("linear comparison is malformed")
    left = _serialized_linear_form(arguments[0])
    right = _serialized_linear_form(arguments[1])
    coefficients = dict(left[0])
    for name, value in right[0].items():
        coefficients[name] = coefficients.get(name, Fraction(0)) - value
    constant = left[1] - right[1]
    if operation in {">", ">="}:
        coefficients = {name: -value for name, value in coefficients.items()}
        constant = -constant
    coefficients = {
        name: value for name, value in sorted(coefficients.items()) if value
    }
    if not coefficients:
        raise ValueError("linear comparison has no variable")
    scale = abs(next(iter(coefficients.values())))
    return (
        {name: value / scale for name, value in coefficients.items()},
        -constant / scale,
        operation in {"<", ">"},
    )


def _serialized_linear_constraints(expression: Any) -> list[dict[str, Any]]:
    if not isinstance(expression, dict):
        raise ValueError("linear constraint expression is malformed")
    if expression.get("type") == "const":
        value = expression.get("value")
        if value is True:
            return []
        if value is False:
            return [{
                "coefficients": {},
                "bound": "-1/1",
                "strict": False,
            }]
        raise ValueError("linear constraint constant is malformed")
    if expression.get("type") == "op" and expression.get("op") == "and":
        constraints: list[dict[str, Any]] = []
        for argument in expression.get("args", []):
            constraints.extend(_serialized_linear_constraints(argument))
        return constraints
    if expression.get("type") != "op" or expression.get("op") not in {
        "==", "<", "<=", ">", ">="
    }:
        raise ValueError("linear constraint is not a comparison")
    arguments = expression.get("args")
    if not isinstance(arguments, list) or len(arguments) != 2:
        raise ValueError("linear comparison is malformed")
    left = _serialized_linear_form(arguments[0])
    right = _serialized_linear_form(arguments[1])

    def inequality(reverse: bool, strict: bool) -> dict[str, Any]:
        first, second = (right, left) if reverse else (left, right)
        coefficients = dict(first[0])
        for name, value in second[0].items():
            coefficients[name] = coefficients.get(name, Fraction(0)) - value
        coefficients = {
            name: value for name, value in sorted(coefficients.items()) if value
        }
        return {
            "coefficients": {
                name: fraction_text(value) for name, value in coefficients.items()
            },
            "bound": fraction_text(-(first[1] - second[1])),
            "strict": strict,
        }

    operation = expression["op"]
    if operation == "==":
        return [inequality(False, False), inequality(True, False)]
    return [
        inequality(
            operation in {">", ">="},
            operation in {"<", ">"},
        )
    ]


def _serialized_conjunct_hashes(expression: dict[str, Any]) -> set[str]:
    if expression.get("type") == "const" and expression.get("value") is True:
        return set()
    if expression.get("type") == "op" and expression.get("op") == "and":
        hashes: set[str] = set()
        for argument in expression.get("args", []):
            if isinstance(argument, dict):
                hashes.update(_serialized_conjunct_hashes(argument))
        return hashes
    return {_serialized_expression_hash(expression)}


def _serialized_conjunction_covers(
    weaker: dict[str, Any],
    stronger: dict[str, Any],
) -> bool:
    stronger_conjuncts = _serialized_conjuncts(stronger)
    stronger_hashes = {
        _serialized_expression_hash(item) for item in stronger_conjuncts
    }
    stronger_bounds = []
    stronger_linear: list[LinearInequality] = []
    for item in stronger_conjuncts:
        try:
            stronger_bounds.append(_serialized_normalized_linear_bound(item))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        try:
            stronger_linear.extend(
                LinearInequality.make(
                    {
                        name: parse_fraction(value)
                        for name, value in constraint["coefficients"].items()
                    },
                    parse_fraction(constraint["bound"]),
                    constraint["strict"],
                )
                for constraint in _serialized_linear_constraints(item)
            )
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            pass
    for item in _serialized_conjuncts(weaker):
        if _serialized_expression_hash(item) in stronger_hashes:
            continue
        try:
            normalized = _serialized_normalized_linear_bound(item)
        except (TypeError, ValueError, ZeroDivisionError):
            normalized = None
        if normalized is not None:
            coefficients, bound, strict = normalized
            if any(
                candidate_coefficients == coefficients
                and (
                    candidate_bound < bound
                    or (
                        candidate_bound == bound
                        and (not strict or candidate_strict)
                    )
                )
                for (
                    candidate_coefficients,
                    candidate_bound,
                    candidate_strict,
                ) in stronger_bounds
            ):
                continue
        try:
            weaker_constraints = [
                LinearInequality.make(
                    {
                        name: parse_fraction(value)
                        for name, value in constraint["coefficients"].items()
                    },
                    parse_fraction(constraint["bound"]),
                    constraint["strict"],
                )
                for constraint in _serialized_linear_constraints(item)
            ]
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return False
        for weaker_constraint in weaker_constraints:
            negated = LinearInequality.make(
                {
                    name: -value
                    for name, value in weaker_constraint.coefficients
                },
                -weaker_constraint.bound,
                not weaker_constraint.strict,
            )
            try:
                proved, _detail = exact_linear_infeasible([
                    *stronger_linear,
                    negated,
                ])
            except (TypeError, ValueError, ZeroDivisionError):
                proved = False
            if not proved:
                return False
    return True


def _serialized_disjunct_hashes(expression: dict[str, Any]) -> set[str]:
    if expression.get("type") == "op" and expression.get("op") == "or":
        hashes: set[str] = set()
        for argument in expression.get("args", []):
            if isinstance(argument, dict):
                hashes.update(_serialized_disjunct_hashes(argument))
        return hashes
    return {_serialized_expression_hash(expression)}


def _serialized_contains(expression: Any, target: Any) -> bool:
    if expression == target:
        return True
    if not isinstance(expression, dict):
        return False
    if expression.get("type") == "op":
        return any(
            _serialized_contains(item, target)
            for item in expression.get("args", [])
        )
    if expression.get("type") == "ite":
        return any(
            _serialized_contains(expression.get(field), target)
            for field in ("cond", "then", "else")
        )
    return False


def _factored_expr_from_dict(expression: Any) -> Expr:
    if not isinstance(expression, dict):
        raise ValueError("expression is malformed")
    kind = expression.get("type")
    if kind == "const":
        return Const(expression.get("value"))
    if kind == "var" and isinstance(expression.get("name"), str):
        return Var(expression["name"])
    if kind == "raw_ref" and isinstance(expression.get("path"), str):
        return RawRef(expression["path"])
    if kind == "op" and isinstance(expression.get("op"), str) and isinstance(
        expression.get("args"),
        list,
    ):
        return Op(
            expression["op"],
            tuple(_factored_expr_from_dict(item) for item in expression["args"]),
        )
    if kind == "ite":
        return Ite(
            _factored_expr_from_dict(expression.get("cond")),
            _factored_expr_from_dict(expression.get("then")),
            _factored_expr_from_dict(expression.get("else")),
        )
    raise ValueError("expression is malformed")


def _serialized_common_conjuncts(expression: Any) -> dict[str, Any]:
    if not isinstance(expression, dict) or expression.get("type") == "const":
        return {}
    if expression.get("type") == "op" and expression.get("op") == "and":
        result: dict[str, Any] = {}
        for item in expression.get("args", []):
            result.update(_serialized_common_conjuncts(item))
        return result
    if expression.get("type") == "op" and expression.get("op") == "or":
        children = [
            _serialized_common_conjuncts(item)
            for item in expression.get("args", [])
        ]
        if not children:
            return {}
        shared = set(children[0])
        for child in children[1:]:
            shared.intersection_update(child)
        return {key: children[0][key] for key in sorted(shared)}
    if expression.get("type") == "ite":
        return {}
    return {_serialized_expression_hash(expression): expression}
