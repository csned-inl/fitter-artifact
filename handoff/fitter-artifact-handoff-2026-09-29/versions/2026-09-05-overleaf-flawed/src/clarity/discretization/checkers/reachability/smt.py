"""Exact finite-prefix and inductive SMT reachability checks."""

from __future__ import annotations

from itertools import count
from time import monotonic
from typing import Any

from clarity.certification.equations import EquationModel

from .encoding import (
    _hard_timeout,
    _query_set,
    _remaining_timeout_ms,
    _run_smt_query,
    z3,
)
from ...model.proof_rules import ProofDeferred
from ...model.expressions import expression_hash
from ...model.reduction_types import ReachabilityContext, ReducedCase


def _run_smt_reachability_checker(
    model: EquationModel,
    reduced_case: ReducedCase,
    context: ReachabilityContext,
    *,
    timeout_ms: int,
) -> dict[str, Any]:
    """Check exact finite prefixes and induction with SMT."""

    if reduced_case.obligation != "physical_interval":
        return {
            "outcome": "DEFERRED",
            "reason_code": "BLOCKED_INPUT",
            "detail": "SMT reachability is only required for physical interval cases",
            "applicability_checks": {"accepted": False},
        }
    if z3 is None:
        return {
            "outcome": "DEFERRED",
            "reason_code": "BLOCKED_INPUT",
            "detail": "z3-solver is unavailable",
            "applicability_checks": {"accepted": False},
        }
    if not set(context.post_dict()).issubset(set(context.initial_variables)):
        return {
            "outcome": "DEFERRED",
            "reason_code": "BLOCKED_INPUT",
            "detail": "the SMT trace state is not completely initialized",
            "applicability_checks": {"accepted": False},
        }

    source_hash = expression_hash(reduced_case.expression)
    reachability_hash = expression_hash(
        reduced_case.reachability_expression or reduced_case.expression
    )
    depth_attempts: list[dict[str, Any]] = []
    deadline = monotonic() + (timeout_ms / 1000.0)
    try:
        for depth in count(1):
            _remaining_timeout_ms(deadline)
            bases, induction, _boolean_variables = _query_set(
                reduced_case,
                context,
                depth,
                deadline=deadline,
            )
            base_records: list[dict[str, Any]] = []
            incomplete = False
            for name, expression in bases:
                remaining_ms = _remaining_timeout_ms(deadline)
                query = _run_smt_query(
                    model,
                    context,
                    expression,
                    timeout_ms=remaining_ms,
                )
                base_records.append({"name": name, **query})
                if query.get("solver_status") == "sat":
                    if query.get("exact_replay") is not True:
                        return {
                            "outcome": "DEFERRED",
                            "reason_code": "COUNTEREXAMPLE_REPLAY_FAILED",
                            "detail": "the SMT reachability trace did not replay exactly",
                            "applicability_checks": {"accepted": True},
                            "proof": {
                                "rule": "smt_finite_prefix_counterexample_v1",
                                "case_expression_sha256": source_hash,
                                "reachability_expression_sha256": reachability_hash,
                                "prefix_depth": depth,
                                "trace_query": {"name": name, **query},
                            },
                        }
                    return {
                        "outcome": "VIOLATION",
                        "reason_code": "COUNTEREXAMPLE_REPLAYED",
                        "detail": (
                            "an exact SMT trace reaches the unsafe case from the "
                            "SysML initial state"
                        ),
                        "applicability_checks": {
                            "accepted": True,
                            "case_id": reduced_case.case_id,
                            "complete_initialization": True,
                            "complete_transition_encoding": True,
                        },
                        "proof": {
                            "rule": "smt_finite_prefix_counterexample_v1",
                            "case_id": reduced_case.case_id,
                            "case_expression_sha256": source_hash,
                            "reachability_expression_sha256": reachability_hash,
                            "prefix_depth": depth,
                            "trace_query": {"name": name, **query},
                        },
                    }
                if query.get("solver_status") != "unsat":
                    incomplete = True
                    break
            induction_record: dict[str, Any] | None = None
            if not incomplete:
                remaining_ms = _remaining_timeout_ms(deadline)
                induction_record = _run_smt_query(
                    model,
                    context,
                    induction,
                    timeout_ms=remaining_ms,
                )
            proved = bool(
                not incomplete
                and induction_record is not None
                and induction_record.get("solver_status") == "unsat"
            )
            depth_attempts.append({
                "depth": depth,
                "base_queries": base_records,
                "induction_query": induction_record,
                "proved": proved,
            })
            if proved:
                return {
                    "outcome": "CERTIFIED",
                    "reason_code": "",
                    "detail": (
                        f"exact SMT queries prove the unsafe case unreachable by "
                        f"{depth}-step induction from the SysML initial state"
                    ),
                    "applicability_checks": {
                        "accepted": True,
                        "case_id": reduced_case.case_id,
                        "induction_depth": depth,
                        "complete_initialization": True,
                        "complete_transition_encoding": True,
                    },
                    "proof": {
                        "rule": "smt_finite_prefix_and_inductive_exclusion_v1",
                        "case_id": reduced_case.case_id,
                        "case_expression_sha256": source_hash,
                        "reachability_expression_sha256": reachability_hash,
                        "depth_attempts": depth_attempts,
                    },
                }
    except ProofDeferred as exc:
        return {
            "outcome": "DEFERRED",
            "reason_code": exc.reason_code,
            "detail": exc.detail,
            "applicability_checks": {
                "accepted": True,
                "case_id": reduced_case.case_id,
                "complete_initialization": True,
                "complete_transition_encoding": True,
            },
            "proof": {
                "rule": "smt_finite_prefix_and_inductive_exclusion_v1",
                "case_id": reduced_case.case_id,
                "case_expression_sha256": source_hash,
                "reachability_expression_sha256": reachability_hash,
                "depth_attempts": depth_attempts,
            },
        }
    except Exception as exc:  # pragma: no cover - fail-closed boundary
        return {
            "outcome": "DEFERRED",
            "reason_code": "MALFORMED_OUTPUT",
            "detail": str(exc),
            "applicability_checks": {"accepted": False},
            "proof": {
                "rule": "smt_finite_prefix_and_inductive_exclusion_v1",
                "case_id": reduced_case.case_id,
                "case_expression_sha256": source_hash,
                "reachability_expression_sha256": reachability_hash,
                "depth_attempts": depth_attempts,
            },
        }

    last_query = next(
        (
            attempt.get("induction_query")
            for attempt in reversed(depth_attempts)
            if isinstance(attempt.get("induction_query"), dict)
        ),
        None,
    ) or {}
    return {
        "outcome": "DEFERRED",
        "reason_code": str(
            last_query.get("reason_code") or "REACHABILITY_BOUND_INCONCLUSIVE"
        ),
        "detail": str(
            last_query.get("detail")
            or "the exact SMT induction query remains feasible"
        ),
        "applicability_checks": {
            "accepted": True,
            "case_id": reduced_case.case_id,
            "complete_initialization": True,
            "complete_transition_encoding": True,
        },
        "proof": {
            "rule": "smt_finite_prefix_and_inductive_exclusion_v1",
            "case_id": reduced_case.case_id,
            "case_expression_sha256": source_hash,
            "reachability_expression_sha256": reachability_hash,
            "depth_attempts": depth_attempts,
        },
    }


def run_smt_reachability_checker(
    model: EquationModel,
    reduced_case: ReducedCase,
    context: ReachabilityContext,
    *,
    timeout_ms: int,
) -> dict[str, Any]:
    """Run SMT reachability within one complete wall clock timeout."""

    try:
        with _hard_timeout(timeout_ms):
            return _run_smt_reachability_checker(
                model,
                reduced_case,
                context,
                timeout_ms=timeout_ms,
            )
    except ProofDeferred as exc:
        return {
            "outcome": "DEFERRED",
            "reason_code": exc.reason_code,
            "detail": exc.detail,
            "applicability_checks": {
                "accepted": True,
                "case_id": reduced_case.case_id,
            },
        }
