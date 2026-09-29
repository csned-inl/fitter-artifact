"""Construct relational reachability candidates and shared context identities."""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from typing import Any

from clarity.certification.equations import Const, Expr, Ite, Op, RawRef, Var

from .encoding import _and, _raw_reference_names
from ...model.optimization import conjunctive_comparisons
from ...model.proof_rules import expr_to_dict, expression_symbols, substitute
from ...model.expressions import expression_hash, simplify
from ...model.reduction_types import ReachabilityContext


def _relational_names(
    context: ReachabilityContext,
) -> tuple[set[str], set[str]]:
    states = set(context.post_dict()) | set(context.initial_variables)
    return states, set(context.action_variables)


def _relational_lift(
    expression: Expr,
    context: ReachabilityContext,
    frame: int,
) -> Expr:
    states, actions = _relational_names(context)
    mapping = {
        name: Var(f"rel_state_{frame}__{name}")
        for name in states
    }
    mapping.update({
        name: Var(f"rel_action_{frame}__{name}")
        for name in actions
    })
    return simplify(substitute(expression, mapping))


def _relational_transition(
    context: ReachabilityContext,
) -> Expr:
    states, _actions = _relational_names(context)
    post = context.post_dict()
    equations = []
    for name in sorted(states):
        next_value = Var(f"rel_state_1__{name}")
        source = post.get(name, Var(name))
        equations.append(Op("==", (
            next_value,
            _relational_lift(source, context, 0),
        )))
    return _and(equations)


def _comparison_atoms(expression: Expr) -> list[Expr]:
    atoms: list[Expr] = []
    if isinstance(expression, Op):
        if expression.op in {"<", "<=", ">", ">=", "=="}:
            atoms.append(expression)
        for argument in expression.args:
            atoms.extend(_comparison_atoms(argument))
    elif isinstance(expression, Ite):
        atoms.extend(_comparison_atoms(expression.cond))
        atoms.extend(_comparison_atoms(expression.then_expr))
        atoms.extend(_comparison_atoms(expression.else_expr))
    return atoms


def _numeric_value(expression: Expr) -> Fraction | None:
    if not isinstance(expression, Const) or isinstance(expression.value, bool):
        return None
    try:
        return Fraction(str(expression.value))
    except (ValueError, ZeroDivisionError):
        return None


def _numeric_expression(value: Fraction) -> Const:
    if value.denominator == 1:
        return Const(value.numerator)
    return Const(f"{value.numerator}/{value.denominator}")


def _numeric_constants(expression: Expr) -> set[Fraction]:
    if isinstance(expression, Const):
        value = _numeric_value(expression)
        return set() if value is None else {abs(value)}
    if isinstance(expression, Op):
        values: set[Fraction] = set()
        for argument in expression.args:
            values.update(_numeric_constants(argument))
        return values
    if isinstance(expression, Ite):
        return (
            _numeric_constants(expression.cond)
            | _numeric_constants(expression.then_expr)
            | _numeric_constants(expression.else_expr)
        )
    return set()


def _initial_numeric_values(
    context: ReachabilityContext,
) -> dict[str, Fraction]:
    values: dict[str, Fraction] = {}
    for expression in context.initial_constraints:
        if not isinstance(expression, Op) or expression.op != "==":
            continue
        left, right = expression.args
        right_value = _numeric_value(right)
        left_value = _numeric_value(left)
        if isinstance(left, Var) and right_value is not None:
            values[left.name] = right_value
        elif isinstance(right, Var) and left_value is not None:
            values[right.name] = left_value
    return values


def _clock_steps(context: ReachabilityContext) -> dict[str, Fraction]:
    clocks: dict[str, Fraction] = {}
    for name, expression in context.post_values:
        if not isinstance(expression, Op) or expression.op != "+":
            continue
        arguments = list(expression.args)
        variable_count = sum(
            isinstance(argument, Var) and argument.name == name
            for argument in arguments
        )
        constants = [
            value
            for argument in arguments
            if (value := _numeric_value(argument)) is not None
        ]
        if variable_count == 1 and len(constants) == 1 and constants[0] > 0:
            clocks[name] = constants[0]
    return clocks


def _raw_lower_bounds(
    context: ReachabilityContext,
) -> dict[str, Fraction]:
    bounds: dict[str, Fraction] = {}
    for expression in context.initial_constraints:
        if not isinstance(expression, Op) or expression.op not in {">=", "<="}:
            continue
        left, right = expression.args
        if expression.op == ">=" and isinstance(left, RawRef):
            value = _numeric_value(right)
            if value is not None:
                bounds[left.path] = max(bounds.get(left.path, value), value)
        elif expression.op == "<=" and isinstance(right, RawRef):
            value = _numeric_value(left)
            if value is not None:
                bounds[right.path] = max(bounds.get(right.path, value), value)
    return bounds


def _rate_candidates(expression: Expr) -> list[Fraction]:
    constants = sorted(value for value in _numeric_constants(expression) if value)
    rates: set[Fraction] = {Fraction(1)}
    for numerator in constants:
        for denominator in constants:
            rate = numerator / denominator
            if Fraction(1) <= rate <= Fraction(10):
                rates.add(rate)
                rates.add(Fraction(rate.numerator // rate.denominator))
    return sorted(rate for rate in rates if rate > 0)


def _dynamical_candidates(
    context: ReachabilityContext,
) -> list[Expr]:
    post = context.post_dict()
    states, _actions = _relational_names(context)
    initial = _initial_numeric_values(context)
    clocks = _clock_steps(context)
    raw_lower = _raw_lower_bounds(context)
    candidates: list[Expr] = []
    for dependent, dependent_post in sorted(post.items()):
        if dependent not in initial:
            continue
        raw_references = _raw_reference_names(dependent_post)
        bounded_references = sorted(raw_references & set(raw_lower))
        if not bounded_references:
            continue
        source_states = sorted(
            (expression_symbols(dependent_post) & states) - {dependent}
        )
        for source in source_states:
            if source not in initial or source not in post:
                continue
            rates = _rate_candidates(post[source])
            if not rates:
                continue
            for clock, step in sorted(clocks.items()):
                if clock not in initial:
                    continue
                clock_delta = Op("-", (
                    Var(clock),
                    _numeric_expression(initial[clock]),
                ))
                source_offset = _numeric_expression(initial[source])
                dependent_offset = _numeric_expression(initial[dependent])
                for name in (clock, source, dependent):
                    value = _numeric_expression(initial[name])
                    candidates.extend([
                        Op(">=", (Var(name), value)),
                        Op("<=", (Var(name), value)),
                    ])
                for upper in range(1, 21):
                    candidates.append(Op("<=", (
                        Var(clock),
                        _numeric_expression(initial[clock] + upper),
                    )))
                for rate in rates:
                    change = Op("*", (
                        _numeric_expression(rate),
                        clock_delta,
                    ))
                    line = Op("+", (source_offset, change))
                    candidates.extend([
                        Op("<=", (Var(source), line)),
                        Op(">=", (Var(source), line)),
                    ])
                    for reference in bounded_references:
                        lower = raw_lower[reference]
                        linear = lower + rate * step / 2
                        curve = Op("+", (
                            dependent_offset,
                            Op("*", (
                                _numeric_expression(linear),
                                clock_delta,
                            )),
                            Op("*", (
                                _numeric_expression(-rate / 2),
                                Op("*", (clock_delta, clock_delta)),
                            )),
                        ))
                        candidates.append(Op(">=", (
                            Var(dependent),
                            curve,
                        )))
    return candidates


def _candidate_invariants(
    context: ReachabilityContext,
) -> list[Expr]:
    states, actions = _relational_names(context)
    known_initial: dict[str, Expr] = {}
    for expression in context.initial_constraints:
        if not isinstance(expression, Op) or expression.op != "==":
            continue
        left, right = expression.args
        if isinstance(left, Var) and isinstance(right, Const):
            known_initial[left.name] = right
        elif isinstance(right, Var) and isinstance(left, Const):
            known_initial[right.name] = left

    candidates: dict[str, Expr] = {}
    for expression in context.initial_constraints:
        projected = simplify(substitute(expression, known_initial))
        symbols = expression_symbols(projected)
        if symbols & (states | actions):
            continue
        if isinstance(projected, Const) and projected.value is True:
            continue
        candidates[expression_hash(projected)] = projected

    boolean_variables = set(context.boolean_variables)
    for atom in _comparison_atoms(context.domain):
        if len(atom.args) != 2:
            continue
        symbols = expression_symbols(atom)
        if not (symbols & states) or symbols & actions:
            continue
        if symbols & boolean_variables:
            continue
        operation = {"<": "<=", ">": ">="}.get(atom.op, atom.op)
        candidate = simplify(Op(operation, atom.args))
        candidates[expression_hash(candidate)] = candidate
    for candidate in _dynamical_candidates(context):
        candidate = simplify(candidate)
        candidates[expression_hash(candidate)] = candidate
    return [candidates[key] for key in sorted(candidates)]


def _relational_query(
    expressions: list[Expr] | tuple[Expr, ...],
) -> Expr:
    return _and(list(expressions))


def _conjunct_hashes(expression: Expr) -> set[str]:
    if isinstance(expression, Op) and expression.op == "and":
        hashes: set[str] = set()
        for argument in expression.args:
            hashes.update(_conjunct_hashes(argument))
        return hashes
    return {expression_hash(expression)}


def _shared_context_record(context: ReachabilityContext) -> dict[str, Any]:
    return {
        "domain": expr_to_dict(context.domain),
        "initial_constraints": [
            expr_to_dict(item)
            for item in sorted(context.initial_constraints, key=expression_hash)
        ],
        "initial_variables": sorted(context.initial_variables),
        "post_values": {
            name: expr_to_dict(expression)
            for name, expression in sorted(context.post_values)
        },
        "action_variables": sorted(context.action_variables),
        "boolean_variables": sorted(context.boolean_variables),
        "integer_variables": sorted(context.integer_variables),
    }


def shared_reachability_context_sha256(
    context: ReachabilityContext,
) -> str:
    payload = json.dumps(
        _shared_context_record(context),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
