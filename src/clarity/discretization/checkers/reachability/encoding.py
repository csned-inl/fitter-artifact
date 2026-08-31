"""Exact state lifting and SMT query encoding for reachability checks."""

from __future__ import annotations

import hashlib
import signal
from contextlib import contextmanager
from fractions import Fraction
from time import monotonic
from typing import Any

from clarity.certification.equations import Const, EquationModel, Expr, Ite, Op, RawRef, Var
from clarity.certification.solver import Encoder, infer_sorts

from ...certificates.replay import replay_serialized_boolean_expression, serialize_exact_value
from ...model.expressions import (
    expression_hash,
    raw_reference_names as _raw_reference_names,
    simplify,
)
from ...model.proof_rules import (
    ProofDeferred,
    expr_to_dict,
    expression_symbols,
    substitute,
)
from ...model.reduction_types import ReachabilityContext, ReducedCase

try:  # pragma: no cover - integration environment determines availability
    import z3  # type: ignore
except Exception:  # pragma: no cover
    z3 = None
else:  # Proof generation must be enabled before this process creates a solver.
    z3.set_param(proof=True)


def _remaining_timeout_ms(deadline: float) -> int:
    remaining = int((deadline - monotonic()) * 1000)
    if remaining <= 0:
        raise ProofDeferred("TIMEOUT", "reachability checker timeout")
    return remaining


@contextmanager
def _hard_timeout(timeout_ms: int):
    def handle_timeout(_signum: int, _frame: Any) -> None:
        raise ProofDeferred("TIMEOUT", "reachability checker timeout")

    try:
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        signal.signal(signal.SIGALRM, handle_timeout)
    except (AttributeError, ValueError):
        yield
        return
    started = monotonic()
    signal.setitimer(signal.ITIMER_REAL, timeout_ms / 1000.0)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            elapsed = monotonic() - started
            signal.setitimer(
                signal.ITIMER_REAL,
                max(0.001, previous_timer[0] - elapsed),
                previous_timer[1],
            )


def _and(expressions: list[Expr] | tuple[Expr, ...]) -> Expr:
    items = tuple(expressions)
    if not items:
        return Const(True)
    if len(items) == 1:
        return items[0]
    return simplify(Op("and", items))


def _mode_condition(
    reduced_case: ReducedCase,
    action_names: dict[str, str],
) -> Expr:
    tests: list[Expr] = []
    for name, value in reduced_case.boolean_assignment:
        tests.append(Op("==", (Var(action_names.get(name, name)), Const(value))))
    return _and(tests)


def _step_action_names(
    context: ReachabilityContext,
    step: int,
) -> dict[str, str]:
    if step == 0:
        return {name: name for name in context.action_variables}
    return {
        name: f"reach_action_{step}__{name}"
        for name in context.action_variables
    }


def _lift(
    expression: Expr,
    state_values: dict[str, Expr],
    action_names: dict[str, str],
) -> Expr:
    action_values = {
        name: Var(renamed) for name, renamed in action_names.items()
    }
    return simplify(substitute(
        substitute(expression, action_values),
        state_values,
    ))


def _state_sequence(
    context: ReachabilityContext,
    depth: int,
    *,
    deadline: float | None = None,
) -> tuple[list[dict[str, Expr]], list[dict[str, str]]]:
    generic_post = context.post_dict()
    state_names = sorted(generic_post)
    states: list[dict[str, Expr]] = [
        {name: Var(name) for name in state_names}
    ]
    actions = [_step_action_names(context, step) for step in range(depth + 1)]
    for step in range(depth):
        if deadline is not None:
            _remaining_timeout_ms(deadline)
        action_values = {
            name: Var(renamed) for name, renamed in actions[step].items()
        }
        next_state: dict[str, Expr] = {}
        for target, expression in generic_post.items():
            if deadline is not None:
                _remaining_timeout_ms(deadline)
            with_actions = substitute(expression, action_values)
            next_state[target] = simplify(substitute(with_actions, states[step]))
        states.append(next_state)
    return states, actions


def _query_set(
    reduced_case: ReducedCase,
    context: ReachabilityContext,
    depth: int,
    *,
    deadline: float | None = None,
) -> tuple[list[tuple[str, Expr]], Expr, set[str]]:
    if deadline is not None:
        _remaining_timeout_ms(deadline)
    states, actions = _state_sequence(context, depth, deadline=deadline)
    unsafe_expression = (
        reduced_case.reachability_expression or reduced_case.expression
    )
    domains = [
        _lift(context.domain, states[step], actions[step])
        for step in range(depth + 1)
    ]
    if deadline is not None:
        _remaining_timeout_ms(deadline)
    case_at = [
        _and([
            _mode_condition(reduced_case, actions[step]),
            _lift(unsafe_expression, states[step], actions[step]),
        ])
        for step in range(depth + 1)
    ]
    safe_at = [simplify(Op("not", (item,))) for item in case_at]

    bases: list[tuple[str, Expr]] = []
    for step in range(depth):
        if deadline is not None:
            _remaining_timeout_ms(deadline)
        bases.append((
            f"base_step_{step}",
            _and([
                *context.initial_constraints,
                *domains[:step],
                case_at[step],
            ]),
        ))
    induction = _and([
        *domains[:depth],
        *safe_at[:depth],
        case_at[depth],
    ])

    renamed_booleans = set(context.boolean_variables)
    source_boolean_actions = set(context.boolean_variables) & set(
        context.action_variables
    )
    for step in range(1, depth + 1):
        renamed_booleans.update(
            actions[step][name] for name in source_boolean_actions
        )
    return bases, induction, renamed_booleans


def _z3_exact_value(value: Any) -> bool | Fraction:
    if z3.is_true(value):
        return True
    if z3.is_false(value):
        return False
    if z3.is_int_value(value):
        return Fraction(value.as_long())
    if z3.is_rational_value(value):
        return Fraction(value.numerator_as_long(), value.denominator_as_long())
    raise ValueError(f"Z3 value is not an exact rational or Boolean: {value}")


def _exact_query_values(
    model: EquationModel,
    encoder: Encoder,
    solver_model: Any,
    expression: Expr,
) -> dict[str, bool | str]:
    raw_references = _raw_reference_names(expression)
    values: dict[str, bool | str] = {}
    for name in sorted(expression_symbols(expression)):
        encoded = (
            encoder.const_var(name)
            if name in raw_references
            else encoder.encode_expr(Var(name), 1, "current")
        )
        exact = _z3_exact_value(
            solver_model.eval(encoded, model_completion=True)
        )
        values[name] = serialize_exact_value(exact)
    return values


def _reachability_sorts(
    model: EquationModel,
    context: ReachabilityContext,
    expression: Expr,
) -> tuple[dict[str, str], list[str]]:
    sorts, conflicts = infer_sorts(model)
    for name in context.integer_variables:
        sorts[name] = "Int"
    for name in expression_symbols(expression):
        if "__" not in name:
            continue
        source = name.split("__", 1)[1]
        if name.startswith("rel_state_") and source in (
            set(context.post_dict()) | set(context.initial_variables)
        ):
            sorts[name] = (
                "Bool" if source in context.boolean_variables
                else "Int" if source in context.integer_variables
                else sorts.get(source, "Real")
            )
        elif (
            name.startswith("reach_action_")
            or name.startswith("rel_action_")
        ) and source in context.action_variables:
            sorts[name] = (
                "Bool" if source in context.boolean_variables
                else "Int" if source in context.integer_variables
                else sorts.get(source, "Real")
            )
    return sorts, conflicts


class _IncrementalSmtQueryRunner:
    def __init__(
        self,
        model: EquationModel,
        context: ReachabilityContext,
        type_scope: Expr,
    ) -> None:
        self.model = model
        self.context = context
        self.sorts, self.conflicts = _reachability_sorts(
            model,
            context,
            type_scope,
        )
        self.encoder = Encoder(model, self.sorts)
        self.solver = z3.Solver()

    def run(
        self,
        expression: Expr,
        *,
        timeout_ms: int,
        record_sat_values: bool,
        replay_sat_query: bool,
    ) -> dict[str, Any]:
        serialized = expr_to_dict(expression)
        query_hash = expression_hash(expression)
        if self.conflicts:
            return {
                "solver_status": "unsupported",
                "reason_code": "UNSUPPORTED_EXPRESSION",
                "detail": "; ".join(self.conflicts),
                "query_expression": serialized,
                "query_expression_sha256": query_hash,
            }
        self.solver.push()
        try:
            self.solver.set(timeout=int(timeout_ms))
            self.solver.add(self.encoder.encode_expr(expression, 1, "current"))
            query_smt2 = self.solver.to_smt2()
            result = self.solver.check()
            record: dict[str, Any] = {
                "solver": "z3",
                "solver_reuse": "persistent_push_pop_v1",
                "solver_status": str(result),
                "query_expression": serialized,
                "query_expression_sha256": query_hash,
                "query_smt2": query_smt2,
                "query_smt2_sha256": hashlib.sha256(
                    query_smt2.encode("utf-8")
                ).hexdigest(),
            }
            if result == z3.unknown:
                reason = self.solver.reason_unknown()
                record["reason_code"] = (
                    "TIMEOUT" if "timeout" in reason.lower() else "PROOF_REJECTED"
                )
                record["detail"] = reason
                return record
            if result == z3.unsat:
                proof_text = self.solver.proof().sexpr()
                record["z3_proof"] = proof_text
                record["z3_proof_sha256"] = hashlib.sha256(
                    proof_text.encode("utf-8")
                ).hexdigest()
                return record
            if not record_sat_values:
                return record
            exact_values = _exact_query_values(
                self.model,
                self.encoder,
                self.solver.model(),
                expression,
            )
            record["exact_values"] = exact_values
            if replay_sat_query:
                record["exact_replay"] = replay_serialized_boolean_expression(
                    serialized,
                    exact_values,
                )
            return record
        finally:
            self.solver.pop()


def _run_smt_query(
    model: EquationModel,
    context: ReachabilityContext,
    expression: Expr,
    *,
    timeout_ms: int,
    record_sat_values: bool = True,
    replay_sat_query: bool = True,
    runner: _IncrementalSmtQueryRunner | None = None,
) -> dict[str, Any]:
    if runner is not None:
        return runner.run(
            expression,
            timeout_ms=timeout_ms,
            record_sat_values=record_sat_values,
            replay_sat_query=replay_sat_query,
        )
    serialized = expr_to_dict(expression)
    query_hash = expression_hash(expression)
    sorts, conflicts = _reachability_sorts(model, context, expression)
    if conflicts:
        return {
            "solver_status": "unsupported",
            "reason_code": "UNSUPPORTED_EXPRESSION",
            "detail": "; ".join(conflicts),
            "query_expression": serialized,
            "query_expression_sha256": query_hash,
        }
    encoder = Encoder(model, sorts)
    solver = z3.Solver()
    solver.set(timeout=int(timeout_ms))
    solver.add(encoder.encode_expr(expression, 1, "current"))
    query_smt2 = solver.to_smt2()
    query_smt2_hash = hashlib.sha256(query_smt2.encode("utf-8")).hexdigest()
    result = solver.check()
    record: dict[str, Any] = {
        "solver": "z3",
        "solver_status": str(result),
        "query_expression": serialized,
        "query_expression_sha256": query_hash,
        "query_smt2": query_smt2,
        "query_smt2_sha256": query_smt2_hash,
    }
    if result == z3.unknown:
        reason = solver.reason_unknown()
        record["reason_code"] = (
            "TIMEOUT" if "timeout" in reason.lower() else "PROOF_REJECTED"
        )
        record["detail"] = reason
        return record
    if result == z3.unsat:
        proof_text = solver.proof().sexpr()
        record["z3_proof"] = proof_text
        record["z3_proof_sha256"] = hashlib.sha256(
            proof_text.encode("utf-8")
        ).hexdigest()
        return record
    if not record_sat_values:
        return record
    exact_values = _exact_query_values(
        model,
        encoder,
        solver.model(),
        expression,
    )
    record["exact_values"] = exact_values
    if replay_sat_query:
        record["exact_replay"] = replay_serialized_boolean_expression(
            serialized,
            exact_values,
        )
    return record
