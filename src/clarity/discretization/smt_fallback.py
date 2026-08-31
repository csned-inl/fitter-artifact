"""SMT fallback and solver-selected subset recertification."""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from clarity.certification.equations import Const, EquationModel, Expr, Ite, Op, RawRef, Var
from clarity.certification.solver import Encoder, infer_sorts

from .certificates.replay import replay_serialized_boolean_expression, serialize_exact_value
from .checkers.convex import run_convex_checker
from .checkers.convex_envelope import run_convex_envelope_checker
from .checkers.linear import run_linear_checker
from .checkers.linear_envelope import run_linear_envelope_checker
from .model.expressions import expression_hash, raw_reference_names as _raw_reference_names
from .model.proof_rules import expr_to_dict, expression_symbols
from .model.reduction_types import ReducedCase
from .analysis import _stage, _validated_attempt

try:  # pragma: no cover - integration environment determines availability
    import z3  # type: ignore
except Exception:  # pragma: no cover
    z3 = None


def _top_level_conjuncts(expression: Expr) -> list[Expr]:
    if isinstance(expression, Op) and expression.op == "and":
        result: list[Expr] = []
        for argument in expression.args:
            result.extend(_top_level_conjuncts(argument))
        return result
    return [expression]


def _conjunction(expressions: list[Expr]) -> Expr:
    if not expressions:
        return Const(True)
    if len(expressions) == 1:
        return expressions[0]
    return Op("and", tuple(expressions))


def _z3_exact_value(value: Any) -> bool | Fraction:
    if z3.is_true(value):
        return True
    if z3.is_false(value):
        return False
    if z3.is_rational_value(value):
        return Fraction(value.numerator_as_long(), value.denominator_as_long())
    raise ValueError(f"Z3 value is not an exact rational or Boolean: {value}")


def _exact_model_values(
    model: EquationModel,
    encoder: Encoder,
    solver_model: Any,
    expression: Expr,
) -> dict[str, bool | str]:
    raw_references = _raw_reference_names(expression)
    values: dict[str, bool | str] = {}
    for name in sorted(expression_symbols(expression)):
        if name in raw_references:
            encoded = encoder.const_var(name)
        else:
            encoded = encoder.encode_expr(Var(name), 1, "current")
        exact = _z3_exact_value(
            solver_model.eval(encoded, model_completion=True)
        )
        values[name] = serialize_exact_value(exact)
    return values


def _core_recertification_attempts(
    reduced_case: ReducedCase,
    selected_constraints: list[Expr],
    *,
    timeout_ms: int,
) -> tuple[ReducedCase, list[dict[str, Any]], dict[str, Any] | None]:
    core_case = ReducedCase(
        case_id=reduced_case.case_id + ".smt_core",
        expression=_conjunction(selected_constraints),
        parent_hash=expression_hash(reduced_case.expression),
        boolean_assignment=reduced_case.boolean_assignment,
        time_reduction=reduced_case.time_reduction,
        obligation=reduced_case.obligation,
    )
    checks = [
        (
            "linear",
            lambda: run_linear_checker(core_case, set(), timeout_ms=timeout_ms),
        ),
        (
            "linear_envelope",
            lambda: run_linear_envelope_checker(core_case, timeout_ms=timeout_ms),
        ),
        (
            "convex",
            lambda: run_convex_checker(core_case, set(), timeout_ms=timeout_ms),
        ),
        (
            "convex_envelope",
            lambda: run_convex_envelope_checker(core_case, timeout_ms=timeout_ms),
        ),
    ]
    attempts: list[dict[str, Any]] = []
    for checker, run in checks:
        attempt = _validated_attempt(run())
        record = {
            "checker": checker,
            "outcome": attempt.get("outcome", "DEFERRED"),
            "reason_code": attempt.get("reason_code", ""),
            "detail": attempt.get("detail", ""),
            "applicability_checks": attempt.get("applicability_checks") or {},
            "proof": attempt.get("proof") or {},
        }
        attempts.append(record)
        if record["outcome"] == "CERTIFIED":
            return core_case, attempts, record
    return core_case, attempts, None


def _smt_fallback(
    model: EquationModel,
    reduced_case: ReducedCase,
    *,
    timeout_ms: int,
    recertification_timeout_ms: int,
) -> dict[str, Any]:
    if z3 is None:
        return _stage(
            "smt_fallback",
            "DEFERRED",
            reason_code="BLOCKED_INPUT",
            detail="z3-solver is unavailable",
        )
    try:
        sorts, conflicts = infer_sorts(model)
        if conflicts:
            return _stage(
                "smt_fallback",
                "DEFERRED",
                reason_code="UNSUPPORTED_EXPRESSION",
                detail="; ".join(conflicts),
            )
        encoder = Encoder(model, sorts)
        solver = z3.Solver()
        solver.set(timeout=int(timeout_ms))
        solver.set(unsat_core=True)
        source_constraints = _top_level_conjuncts(reduced_case.expression)
        trackers = [
            z3.Bool(f"discretization_core_{index}")
            for index in range(len(source_constraints))
        ]
        for index, constraint in enumerate(source_constraints):
            solver.add(z3.Implies(
                trackers[index],
                encoder.encode_expr(constraint, 1, "current"),
            ))
        result = solver.check(*trackers)
        if result == z3.unknown:
            reason = solver.reason_unknown()
            code = "TIMEOUT" if "timeout" in reason.lower() else "PROOF_REJECTED"
            return _stage(
                "smt_fallback",
                "DEFERRED",
                reason_code=code,
                detail=reason,
                proof={"solver_status": "unknown"},
            )
        if result == z3.unsat:
            selected_indices = sorted({
                int(str(item).removeprefix("discretization_core_"))
                for item in solver.unsat_core()
            })
            minimization_checks = 0
            minimization_complete = True
            for index in tuple(selected_indices):
                candidate = [
                    item for item in selected_indices if item != index
                ]
                candidate_result = solver.check(*[
                    trackers[item] for item in candidate
                ])
                minimization_checks += 1
                if candidate_result == z3.unsat:
                    selected_indices = candidate
                elif candidate_result == z3.unknown:
                    minimization_complete = False
            selected_constraints = [
                source_constraints[index] for index in selected_indices
            ]
            core_case, attempts, certified = _core_recertification_attempts(
                reduced_case,
                selected_constraints,
                timeout_ms=recertification_timeout_ms,
            )
            proof = {
                "rule": "solver_selected_subset_recertification_v1",
                "solver_status": "unsat",
                "source_expression_sha256": expression_hash(
                    reduced_case.expression
                ),
                "source_constraint_count": len(source_constraints),
                "selected_indices": selected_indices,
                "subset_minimization_checks": minimization_checks,
                "subset_minimization_complete": minimization_complete,
                "selected_constraints": [
                    expr_to_dict(item) for item in selected_constraints
                ],
                "selected_expression": expr_to_dict(core_case.expression),
                "selected_expression_sha256": expression_hash(
                    core_case.expression
                ),
                "recertification_attempts": attempts,
            }
            if certified is not None:
                proof["certifying_checker"] = certified["checker"]
                proof["certificate_attempt"] = certified
                return _stage(
                    "smt_fallback",
                    "CERTIFIED",
                    detail=(
                        "a solver-selected subset of the source constraints "
                        "has an independently checked linear or convex certificate"
                    ),
                    proof=proof,
                )
            return _stage(
                "smt_fallback",
                "DEFERRED",
                reason_code="PROOF_REJECTED",
                detail=(
                    "the solver-selected source constraint subset was not "
                    "certified by the linear or convex checkers"
                ),
                proof=proof,
            )
        exact_values = _exact_model_values(
            model,
            encoder,
            solver.model(),
            reduced_case.expression,
        )
        replayed = replay_serialized_boolean_expression(
            expr_to_dict(reduced_case.expression),
            exact_values,
        )
        if replayed:
            return _stage(
                "smt_fallback",
                "DEFERRED",
                reason_code="REACHABILITY_BOUND_INCONCLUSIVE",
                detail=(
                    "the exact values replay the local unsafe constraints, but "
                    "do not establish reachability from the declared initial state"
                ),
                proof={
                    "rule": "exact_local_feasibility_replay_v1",
                    "solver_status": "sat",
                    "source_expression_sha256": expression_hash(
                        reduced_case.expression
                    ),
                    "exact_values": exact_values,
                    "exact_replay": True,
                },
            )
        return _stage(
            "smt_fallback",
            "DEFERRED",
            reason_code="COUNTEREXAMPLE_REPLAY_FAILED",
            detail="solver candidate was not independently replayed",
            proof={"solver_status": "sat"},
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        return _stage(
            "smt_fallback",
            "DEFERRED",
            reason_code="MALFORMED_OUTPUT",
            detail=str(exc),
        )
