"""Linear and convex finite-prefix reachability progression."""

from __future__ import annotations

from itertools import count
from time import monotonic
from typing import Any, Callable

from clarity.certification.equations import Expr, Op

from ..convex import run_convex_checker
from ..convex_envelope import run_convex_envelope_checker
from ..factored import run_lazy_factored_checker
from ..linear import run_linear_checker
from ..linear_envelope import run_linear_envelope_checker
from .encoding import _hard_timeout, _query_set, _remaining_timeout_ms
from .invariants import shared_reachability_context_sha256
from ...model.optimization import quadratic_constraints
from ...model.proof_rules import ProofDeferred, expr_to_dict, expression_is_linear
from ...model.expressions import expression_hash
from ...model.reduction_types import ReachabilityContext, ReducedCase


def _attempt_query(
    expression: Expr,
    boolean_variables: set[str],
    prefix: str,
    checker: Callable[..., dict[str, Any]],
    timeout_ms: int,
    context: ReachabilityContext,
    *,
    deadline: float,
) -> tuple[bool, list[dict[str, Any]], dict[str, Any] | None]:
    _remaining_timeout_ms(deadline)
    source_case = ReducedCase(
        case_id=prefix + ".root",
        expression=expression,
        parent_hash=expression_hash(expression),
        boolean_assignment=(),
        time_reduction="lazy_factored_reachability",
        obligation="reachability",
        reachability_expression=expression,
        factored=True,
    )
    factored_timeout_ms = min(
        timeout_ms,
        _remaining_timeout_ms(deadline),
    )
    attempt = run_lazy_factored_checker(
        source_case,
        boolean_variables,
        "reachability_arithmetic",
        lambda leaf, remaining: checker(
            leaf,
            set(),
            timeout_ms=min(timeout_ms, remaining),
        ),
        lambda item: (
            isinstance(item, Op)
            and item.op in {"==", ">", "<", ">=", "<="}
        ),
        timeout_ms=factored_timeout_ms,
        context_sha256=shared_reachability_context_sha256(context),
        total_timeout_ms=factored_timeout_ms,
    )
    record = {
        "case_id": source_case.case_id,
        "expression": expr_to_dict(expression),
        "expression_sha256": expression_hash(expression),
        "attempt": attempt,
    }
    if attempt.get("outcome") == "CERTIFIED":
        return True, [record], None
    return False, [record], attempt


def _run_reachability_checker(
    reduced_case: ReducedCase,
    context: ReachabilityContext,
    *,
    method: str,
    timeout_ms: int,
) -> dict[str, Any]:
    """Prove an unsafe case unreachable with checked finite-prefix induction."""

    if reduced_case.obligation != "physical_interval":
        return {
            "outcome": "DEFERRED",
            "reason_code": "BLOCKED_INPUT",
            "detail": "reachability is only required for physical interval cases",
            "applicability_checks": {"accepted": False},
        }
    if method == "linear":
        def checker(
            arithmetic_case: ReducedCase,
            boolean_variables: set[str],
            *,
            timeout_ms: int,
        ) -> dict[str, Any]:
            if expression_is_linear(
                arithmetic_case.expression,
                boolean_variables,
            )[0]:
                linear = run_linear_checker(
                    arithmetic_case,
                    boolean_variables,
                    timeout_ms=timeout_ms,
                )
                if linear.get("outcome") in {"CERTIFIED", "VIOLATION"}:
                    linear.setdefault("applicability_checks", {})[
                        "reachability_submethod"
                    ] = "linear"
                    return linear
            envelope = run_linear_envelope_checker(
                arithmetic_case,
                timeout_ms=timeout_ms,
            )
            envelope.setdefault("applicability_checks", {})[
                "reachability_submethod"
            ] = "bounded_product_linear_envelope"
            return envelope
    elif method == "convex":
        def checker(
            arithmetic_case: ReducedCase,
            boolean_variables: set[str],
            *,
            timeout_ms: int,
        ) -> dict[str, Any]:
            if expression_is_linear(
                arithmetic_case.expression,
                boolean_variables,
            )[0]:
                linear = run_linear_checker(
                    arithmetic_case,
                    boolean_variables,
                    timeout_ms=timeout_ms,
                )
                if linear.get("outcome") == "CERTIFIED":
                    linear.setdefault("applicability_checks", {})[
                        "reachability_submethod"
                    ] = "linear"
                    return linear
            try:
                quadratic_constraints(
                    arithmetic_case.expression,
                    boolean_variables,
                )
            except ProofDeferred:
                convex = None
            else:
                convex = run_convex_checker(
                    arithmetic_case,
                    boolean_variables,
                    timeout_ms=timeout_ms,
                )
                convex.setdefault("applicability_checks", {})[
                    "reachability_submethod"
                ] = "convex"
                if convex.get("outcome") == "CERTIFIED":
                    return convex
            envelope = run_convex_envelope_checker(
                arithmetic_case,
                timeout_ms=timeout_ms,
            )
            envelope.setdefault("applicability_checks", {})[
                "reachability_submethod"
            ] = "convex_square_envelope"
            return envelope
    else:
        return {
            "outcome": "DEFERRED",
            "reason_code": "MALFORMED_OUTPUT",
            "detail": f"unknown reachability method {method}",
            "applicability_checks": {"accepted": False},
        }

    depth_records: list[dict[str, Any]] = []
    deadline = monotonic() + (timeout_ms / 1000.0)
    for depth in count(1):
        try:
            _remaining_timeout_ms(deadline)
            bases, induction, boolean_variables = _query_set(
                reduced_case,
                context,
                depth,
                deadline=deadline,
            )
            base_records: list[dict[str, Any]] = []
            failed: dict[str, Any] | None = None
            for name, expression in bases:
                proved, records, failed = _attempt_query(
                    expression,
                    boolean_variables,
                    f"{reduced_case.case_id}.reach.depth{depth}.{name}",
                    checker,
                    timeout_ms,
                    context,
                    deadline=deadline,
                )
                base_records.extend(records)
                if not proved:
                    if (
                        failed is not None
                        and failed.get("outcome") == "VIOLATION"
                        and set(context.post_dict()).issubset(
                            set(context.initial_variables)
                        )
                    ):
                        return {
                            "outcome": "VIOLATION",
                            "reason_code": "COUNTEREXAMPLE_REPLAYED",
                            "detail": (
                                f"an exact unsafe trace from the SysML initial "
                                f"state was replayed at prefix step {len(base_records) - 1}"
                            ),
                            "applicability_checks": {
                                "accepted": True,
                                "method": method,
                                "case_id": reduced_case.case_id,
                                "complete_initialization": True,
                                "complete_transition_mode_coverage": True,
                            },
                            "proof": {
                                "rule": "exact_finite_prefix_counterexample_v1",
                                "method": method,
                                "case_id": reduced_case.case_id,
                                "case_expression_sha256": expression_hash(
                                    reduced_case.expression
                                ),
                                "reachability_expression_sha256": expression_hash(
                                    reduced_case.reachability_expression
                                    or reduced_case.expression
                                ),
                                "prefix_depth": depth,
                                "base_query": name,
                                "base_obligations": base_records,
                                "counterexample": (
                                    failed.get("proof") or {}
                                ).get("counterexample", {}),
                            },
                        }
                    break
            induction_records: list[dict[str, Any]] = []
            if failed is None:
                proved, induction_records, failed = _attempt_query(
                    induction,
                    boolean_variables,
                    f"{reduced_case.case_id}.reach.depth{depth}.induction",
                    checker,
                    timeout_ms,
                    context,
                    deadline=deadline,
                )
            else:
                proved = False
            depth_record = {
                "depth": depth,
                "base_query_count": len(bases),
                "base_obligations": base_records,
                "induction_obligations": induction_records,
                "proved": bool(proved and failed is None),
            }
            depth_records.append(depth_record)
            if depth_record["proved"]:
                return {
                    "outcome": "CERTIFIED",
                    "reason_code": "",
                    "detail": (
                        f"the unsafe case is excluded by checked {depth}-step "
                        f"{method} induction from the SysML initial state"
                    ),
                    "applicability_checks": {
                        "accepted": True,
                        "method": method,
                        "induction_depth": depth,
                        "case_id": reduced_case.case_id,
                        "complete_initial_prefix": True,
                        "complete_transition_mode_coverage": True,
                        "symbolic_action_modes": True,
                        "eager_action_mode_product": False,
                    },
                    "proof": {
                        "rule": "finite_prefix_and_inductive_case_exclusion_v1",
                        "method": method,
                        "case_id": reduced_case.case_id,
                        "case_expression_sha256": expression_hash(
                            reduced_case.expression
                        ),
                        "reachability_expression_sha256": expression_hash(
                            reduced_case.reachability_expression
                            or reduced_case.expression
                        ),
                        "depth_attempts": depth_records,
                    },
                }
        except ProofDeferred as exc:
            depth_records.append({
                "depth": depth,
                "proved": False,
                "reason_code": exc.reason_code,
                "detail": exc.detail,
            })
            if exc.reason_code in {
                "NOT_LINEAR",
                "NOT_CONVEX",
                "UNSUPPORTED_EXPRESSION",
                "INCOMPLETE_CASE_COVERAGE",
                "TIMEOUT",
            }:
                break
        except Exception as exc:  # pragma: no cover - fail-closed boundary
            depth_records.append({
                "depth": depth,
                "proved": False,
                "reason_code": "MALFORMED_OUTPUT",
                "detail": str(exc),
            })
            break

    last_failure = next(
        (
            record.get("attempt")
            for depth_record in reversed(depth_records)
            for record in reversed(
                depth_record.get("induction_obligations", [])
                + depth_record.get("base_obligations", [])
            )
            if record.get("attempt", {}).get("outcome") != "CERTIFIED"
        ),
        None,
    )
    return {
        "outcome": "DEFERRED",
        "reason_code": str(
            (last_failure or {}).get("reason_code")
            or depth_records[-1].get("reason_code")
            or "REACHABILITY_BOUND_INCONCLUSIVE"
        ),
        "detail": str(
            (last_failure or {}).get("detail")
            or depth_records[-1].get("detail")
            or "the checked reachable-state overapproximation intersects the unsafe case"
        ),
        "applicability_checks": {
            "accepted": True,
            "method": method,
            "case_id": reduced_case.case_id,
            "complete_initial_prefix": True,
            "complete_transition_mode_coverage": True,
            "symbolic_action_modes": True,
            "eager_action_mode_product": False,
        },
        "proof": {
            "rule": "finite_prefix_and_inductive_case_exclusion_v1",
            "method": method,
            "case_id": reduced_case.case_id,
            "case_expression_sha256": expression_hash(reduced_case.expression),
            "reachability_expression_sha256": expression_hash(
                reduced_case.reachability_expression or reduced_case.expression
            ),
            "depth_attempts": depth_records,
        },
    }


def run_reachability_checker(
    reduced_case: ReducedCase,
    context: ReachabilityContext,
    *,
    method: str,
    timeout_ms: int,
) -> dict[str, Any]:
    """Run optimized reachability within one complete wall clock timeout."""

    try:
        with _hard_timeout(timeout_ms):
            return _run_reachability_checker(
                reduced_case,
                context,
                method=method,
                timeout_ms=timeout_ms,
            )
    except ProofDeferred as exc:
        return {
            "outcome": "DEFERRED",
            "reason_code": exc.reason_code,
            "detail": exc.detail,
            "applicability_checks": {
                "accepted": True,
                "method": method,
                "case_id": reduced_case.case_id,
            },
        }
