"""Construct complete arithmetic cases for discretization obligations."""

from __future__ import annotations

from itertools import product
from typing import Any

from clarity.certification.equations import Const, Expr, Op, Var

from .constraint_reduction import _merge_contained_cases, _reduce_constraint_list
from .expressions import (
    INTERVAL_TIME,
    _and,
    _comparison_expression,
    _is_interval_bound,
    _split_conditionals,
    _time_degree,
    expression_hash,
    simplify,
)
from .proof_rules import (
    ProofDeferred,
    boolean_dnf,
    expr_to_dict,
    expression_symbols,
    substitute,
)
from .reduction_types import ReducedCase


def _factored_obligation(
    expression: Expr,
    property_id: str,
    obligation: str,
) -> tuple[list[ReducedCase], dict[str, Any]]:
    """Keep one exact obligation root instead of constructing all logical cases."""

    root = simplify(expression)
    root_hash = expression_hash(root)
    case_id = f"{property_id}.{obligation}.root"
    case = ReducedCase(
        case_id=case_id,
        expression=root,
        parent_hash=root_hash,
        boolean_assignment=(),
        time_reduction="symbolic_factored_formula",
        obligation=obligation,
        reachability_expression=root,
        factored=True,
    )
    row = {
        "case_id": case_id,
        "obligation": obligation,
        "boolean_assignment": {},
        "conditional_branch": "symbolic",
        "time_reduction": "symbolic_factored_formula",
        "expression": expr_to_dict(root),
        "expression_sha256": root_hash,
        "reachability_expression": expr_to_dict(root),
        "reachability_expression_sha256": root_hash,
        "constraint_reduction": {
            "rule": "canonical_conjunction_reduction_v1",
            "removed_constraints": [],
            "reachability_removed_constraints": [],
        },
        "merged_sources": [],
    }
    return [case], {
        "rule": "factored_obligation_root_v1",
        "obligation": obligation,
        "parent_expression_sha256": root_hash,
        "root_expression": expr_to_dict(root),
        "root_expression_sha256": root_hash,
        "generated_case_count": 1,
        "case_count": 1,
        "containment_merged_case_count": 0,
        "actual_split_count": 0,
        "complete": True,
        "cases": [row],
    }


def _case_split(
    expression: Expr,
    boolean_variables: set[str],
    property_id: str,
    obligation: str,
) -> tuple[list[ReducedCase], dict[str, Any]]:
    used_booleans = sorted(expression_symbols(expression) & boolean_variables)
    parent_hash = expression_hash(expression)
    cases: list[ReducedCase] = []
    case_rows: list[dict[str, Any]] = []
    case_by_key: dict[tuple[Any, ...], int] = {}
    index = 0
    generated_case_count = 0
    conditional_branch_count = 0
    for values in product((False, True), repeat=len(used_booleans)):
        assignment = tuple(zip(used_booleans, values))
        assigned = simplify(substitute(expression, {
            name: Const(value) for name, value in assignment
        }))
        conditional_branches = _split_conditionals(assigned)
        conditional_branch_count += len(conditional_branches)
        for conditional_branch, branch_expression in conditional_branches:
            alternatives = boolean_dnf(branch_expression, boolean_variables)
            for comparisons in alternatives:
                interval_bounds = [
                    item for item in comparisons if _is_interval_bound(item)
                ]
                body = [
                    item for item in comparisons if not _is_interval_bound(item)
                ]
                time_gate_relaxations = [
                    item
                    for item in body
                    if INTERVAL_TIME
                    in expression_symbols(_comparison_expression(item))
                    and all(
                        name == INTERVAL_TIME or "time" in name.lower()
                        for name in expression_symbols(_comparison_expression(item))
                    )
                ]
                body = [item for item in body if item not in time_gate_relaxations]
                changing = [
                    item
                    for item in body
                    if INTERVAL_TIME
                    in expression_symbols(_comparison_expression(item))
                ]
                endpoint_values: list[tuple[str, Expr | None]]
                if not changing:
                    endpoint_values = [("time_independent", None)]
                elif len(changing) == 1 and (
                    _time_degree(changing[0].left) is not None
                    and _time_degree(changing[0].left) <= 1
                    and _time_degree(changing[0].right) is not None
                    and _time_degree(changing[0].right) <= 1
                ):
                    endpoint_values = [
                        ("affine_time_endpoint_zero", Const(0)),
                        ("affine_time_endpoint_dt", Const("__DT__")),
                    ]
                else:
                    endpoint_values = [("unreduced_interval_time", None)]

                for time_reduction, endpoint in endpoint_values:
                    generated_case_count += 1
                    if time_gate_relaxations:
                        time_reduction = (
                            "time_gate_outer_relaxation+" + time_reduction
                        )
                    selected = (
                        body
                        if time_reduction != "unreduced_interval_time"
                        else comparisons
                    )
                    if time_reduction.endswith("unreduced_interval_time"):
                        selected = [
                            item
                            for item in comparisons
                            if item not in time_gate_relaxations
                        ]
                    expressions = [
                        _comparison_expression(item) for item in selected
                    ]
                    reachability_expressions = [
                        _comparison_expression(item) for item in comparisons
                    ]
                    if endpoint is not None:
                        value = endpoint
                        if isinstance(endpoint, Const) and endpoint.value == "__DT__":
                            upper = next(
                                (
                                    item.right
                                    for item in interval_bounds
                                    if isinstance(item.left, Var)
                                    and item.left.name == INTERVAL_TIME
                                    and item.op == "<="
                                ),
                                None,
                            )
                            if upper is None:
                                raise ProofDeferred(
                                    "INCOMPLETE_CASE_COVERAGE",
                                    "the interval upper bound is missing",
                                )
                            value = upper
                        expressions = [
                            substitute(item, {INTERVAL_TIME: value})
                            for item in expressions
                        ]
                        reachability_expressions = [
                            substitute(item, {INTERVAL_TIME: value})
                            for item in reachability_expressions
                        ]
                    expressions, expression_removals = _reduce_constraint_list(
                        expressions,
                        boolean_variables,
                    )
                    (
                        reachability_expressions,
                        reachability_removals,
                    ) = _reduce_constraint_list(
                        reachability_expressions,
                        boolean_variables,
                    )
                    case_expression = simplify(_and(expressions))
                    reachability_expression = simplify(_and(reachability_expressions))
                    case_key = (
                        assignment,
                        expression_hash(case_expression),
                        expression_hash(reachability_expression),
                    )
                    merged_index = case_by_key.get(case_key)
                    if merged_index is not None:
                        merged_row = case_rows[merged_index]
                        merged_row.setdefault("merged_sources", []).append({
                            "rule": "exact_duplicate_case_v1",
                            "source_id": (
                                f"{property_id}.{obligation}.source."
                                f"{generated_case_count - 1:04d}"
                            ),
                            "obligation": obligation,
                            "boolean_assignment": dict(assignment),
                            "conditional_branch": conditional_branch,
                            "time_reduction": time_reduction,
                            "expression": expr_to_dict(case_expression),
                            "expression_sha256": expression_hash(case_expression),
                            "reachability_expression": expr_to_dict(
                                reachability_expression
                            ),
                            "reachability_expression_sha256": expression_hash(
                                reachability_expression
                            ),
                            "constraint_reduction": {
                                "rule": "canonical_conjunction_reduction_v1",
                                "removed_constraints": expression_removals,
                                "reachability_removed_constraints": (
                                    reachability_removals
                                ),
                            },
                        })
                        continue
                    case_id = f"{property_id}.{obligation}.case.{index:04d}"
                    case = ReducedCase(
                        case_id,
                        case_expression,
                        parent_hash,
                        assignment,
                        time_reduction,
                        obligation,
                        reachability_expression,
                    )
                    cases.append(case)
                    case_rows.append({
                        "case_id": case_id,
                        "obligation": obligation,
                        "boolean_assignment": dict(assignment),
                        "conditional_branch": conditional_branch,
                        "time_reduction": time_reduction,
                        "expression": expr_to_dict(case_expression),
                        "expression_sha256": expression_hash(case_expression),
                        "reachability_expression": expr_to_dict(
                            reachability_expression
                        ),
                        "reachability_expression_sha256": expression_hash(
                            reachability_expression
                        ),
                        "constraint_reduction": {
                            "rule": "canonical_conjunction_reduction_v1",
                            "removed_constraints": expression_removals,
                            "reachability_removed_constraints": reachability_removals,
                        },
                        "merged_sources": [],
                    })
                    case_by_key[case_key] = len(case_rows) - 1
                    index += 1
    cases, case_rows, containment_merged_case_count = _merge_contained_cases(
        cases,
        case_rows,
    )
    return cases, {
        "rule": "exhaustive_boolean_assignment_then_exact_dnf_v1",
        "obligation": obligation,
        "parent_expression_sha256": parent_hash,
        "boolean_variables": used_booleans,
        "assignment_count": 2 ** len(used_booleans),
        "conditional_branch_count": conditional_branch_count,
        "generated_case_count": generated_case_count,
        "containment_merged_case_count": containment_merged_case_count,
        "case_count": len(cases),
        "complete": True,
        "time_reduction_rule": (
            "A single comparison affine in interval time holds somewhere on the "
            "closed interval exactly when it holds at at least one endpoint. A "
            "comparison involving only modeled time is removed only as an outer "
            "relaxation, so infeasibility still proves the original case."
        ),
        "cases": case_rows,
    }
