"""Map sampled controller values to exact physical interval trajectories."""

from __future__ import annotations

from typing import Any, Iterable

from clarity.certification.equations import Equation, EquationModel, Expr, Ite, Op, Var

from .expressions import INTERVAL_TIME, _canonical_bytes, simplify
from .proof_rules import (
    ProofDeferred,
    expr_to_dict,
    expand_definitions,
    expression_symbols,
    substitute,
)


def _equation(model: EquationModel, name: str) -> Equation | None:
    return model.definitions.get(name) or model.transitions.get(name)


def _path_to_continuous(
    model: EquationModel,
    start: str,
    continuous: set[str],
) -> tuple[str, list[dict[str, Any]]] | None:
    """Find one deterministic equation path from a sampled value to physics."""

    queue: list[tuple[str, list[dict[str, Any]]]] = [(start, [])]
    visited: set[str] = set()
    found: list[tuple[str, list[dict[str, Any]]]] = []
    while queue:
        name, path = queue.pop(0)
        if name in visited:
            continue
        visited.add(name)
        if name in continuous and name != start:
            if _is_physical_target(name):
                found.append((name, path))
            continue
        equation = _equation(model, name)
        if equation is None:
            continue
        row = {
            "target": equation.target,
            "kind": equation.kind,
            "source": equation.source,
            "expression": expr_to_dict(equation.expr),
        }
        for reference in sorted(expression_symbols(equation.expr)):
            if reference == name:
                continue
            queue.append((reference, path + [row]))
    targets = sorted({target for target, _path in found})
    if len(targets) != 1:
        return None
    target = targets[0]
    candidates = [path for candidate, path in found if candidate == target]
    candidates.sort(key=lambda value: (len(value), _canonical_bytes(value)))
    return target, candidates[0]


def _is_physical_target(name: str) -> bool:
    return "time" not in name.lower()


def physical_aliases(
    model: EquationModel,
    expressions: Iterable[Expr],
    continuous: set[str],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    aliases: dict[str, str] = {}
    records: list[dict[str, Any]] = []
    symbols: set[str] = set()
    for expression in expressions:
        symbols |= expression_symbols(expression)
    for symbol in sorted(symbols):
        if symbol in continuous:
            continue
        path = _path_to_continuous(model, symbol, continuous)
        if path is None or not _is_physical_target(path[0]):
            continue
        target, equations = path
        aliases[symbol] = target
        records.append({
            "sampled_value": symbol,
            "physical_value": target,
            "equation_path": equations,
            "rule": "ordered_sensor_path_to_current_physical_value_v1",
        })
    return aliases, records


def _reaches_target(
    model: EquationModel,
    expr: Expr,
    target: str,
    *,
    seen: set[str] | None = None,
) -> bool:
    seen = set(seen or set())
    if isinstance(expr, Var):
        if expr.name == target:
            return True
        if expr.name in seen:
            return False
        equation = _equation(model, expr.name)
        return bool(
            equation
            and _reaches_target(
                model,
                equation.expr,
                target,
                seen=seen | {expr.name},
            )
        )
    if isinstance(expr, Op):
        return any(_reaches_target(model, arg, target, seen=set(seen)) for arg in expr.args)
    if isinstance(expr, Ite):
        return any(
            _reaches_target(model, arg, target, seen=set(seen))
            for arg in (expr.cond, expr.then_expr, expr.else_expr)
        )
    return False


def _required_mapping_guards(
    model: EquationModel,
    sampled: str,
    physical: str,
) -> list[Expr]:
    required: list[Expr] = []
    visited: set[str] = set()
    current = sampled
    while current != physical and current not in visited:
        visited.add(current)
        equation = _equation(model, current)
        if equation is None:
            break
        expression = equation.expr
        if isinstance(expression, Ite):
            then_reaches = _reaches_target(
                model, expression.then_expr, physical, seen=set(visited)
            )
            else_reaches = _reaches_target(
                model, expression.else_expr, physical, seen=set(visited)
            )
            if then_reaches and not else_reaches:
                required.append(expand_definitions(model, expression.cond))
                expression = expression.then_expr
            elif else_reaches and not then_reaches:
                required.append(
                    Op("not", (expand_definitions(model, expression.cond),))
                )
                expression = expression.else_expr
            else:
                break
        references = [
            name
            for name in sorted(expression_symbols(expression))
            if _reaches_target(model, Var(name), physical, seen=set(visited))
        ]
        if len(references) != 1:
            break
        current = references[0]
    return required


def _expand_post_update(
    model: EquationModel,
    expr: Expr,
    continuous: set[str],
    *,
    seen: set[str] | None = None,
) -> Expr:
    """Use the controller and actuator value effective for the next interval."""

    seen = set(seen or set())
    if isinstance(expr, Var):
        if expr.name in model.definitions:
            if expr.name in seen:
                raise ProofDeferred("UNSUPPORTED_EXPRESSION", f"cyclic definition {expr.name}")
            return _expand_post_update(
                model,
                model.definitions[expr.name].expr,
                continuous,
                seen=seen | {expr.name},
            )
        if expr.name in model.state and expr.name not in continuous:
            equation = model.transitions.get(expr.name)
            if equation is not None and expr.name not in seen:
                return _expand_post_update(
                    model,
                    equation.expr,
                    continuous,
                    seen=seen | {expr.name},
                )
        return expr
    if isinstance(expr, Op):
        return Op(
            expr.op,
            tuple(
                _expand_post_update(model, arg, continuous, seen=set(seen))
                for arg in expr.args
            ),
        )
    if isinstance(expr, Ite):
        return Ite(
            _expand_post_update(model, expr.cond, continuous, seen=set(seen)),
            _expand_post_update(model, expr.then_expr, continuous, seen=set(seen)),
            _expand_post_update(model, expr.else_expr, continuous, seen=set(seen)),
        )
    return expr


def _trajectory(
    model: EquationModel,
    target: str,
    continuous: set[str],
    constant_values: dict[str, Expr],
) -> tuple[Expr, list[str]]:
    equation = model.transitions.get(target)
    if equation is None:
        raise ProofDeferred("MISSING_WITHIN_STEP_MEANING", f"no transition for {target}")
    symbols = expression_symbols(equation.expr)
    dt_symbols = sorted(name for name in symbols if name == "dt" or name.endswith("_dt"))
    if not dt_symbols:
        raise ProofDeferred(
            "MISSING_WITHIN_STEP_MEANING",
            f"continuous assignment for {target} does not use dt",
        )
    expression = substitute(
        equation.expr,
        {name: Var(INTERVAL_TIME) for name in dt_symbols},
    )
    expression = _expand_post_update(model, expression, continuous)
    expression = substitute(
        expression,
        {name: value for name, value in constant_values.items() if name not in dt_symbols},
    )
    return simplify(expression), dt_symbols
