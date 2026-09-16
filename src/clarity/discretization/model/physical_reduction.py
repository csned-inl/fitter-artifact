"""Physical trajectories with sampled state retained as independent storage."""

from __future__ import annotations

from clarity.certification.equations import Equation, EquationModel, Expr, Ite, Op, Var

from .expressions import INTERVAL_TIME, simplify
from .proof_rules import (
    ProofDeferred,
    expand_definitions,
    expression_symbols,
    substitute,
)


def _equation(model: EquationModel, name: str) -> Equation | None:
    return model.definitions.get(name) or model.transitions.get(name) or model.sample_events.get(name)


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
        if expr.name in model.sampled_state:
            return expr
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
                references = expand_definitions(model, equation.expr).refs()
                # Only an explicit action-controlled output is a post-action
                # substitution. A state-to-state assignment is a timed event.
                if references & model.state or not references & model.actions:
                    return expr
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
