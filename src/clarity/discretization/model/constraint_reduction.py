"""Normalize, compare, and merge exact reduction constraints."""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Iterable

from clarity.certification.equations import Const, Expr, Ite, Op, RawRef, Var

from .expressions import expression_hash, simplify
from .optimization import fraction_text as _fraction_text
from .proof_rules import (
    Comparison,
    ProofDeferred,
    comparison_inequalities,
    expr_to_dict,
    expression_is_linear,
    prove_implication_exact,
)
from .reduction_types import ReducedCase
def _normalized_linear_bound(
    expression: Expr,
    boolean_variables: set[str],
) -> tuple[tuple[tuple[str, Fraction], ...], Fraction, bool] | None:
    if not isinstance(expression, Op) or expression.op not in {
        "<", "<=", ">", ">="
    }:
        return None
    try:
        inequalities = comparison_inequalities(
            Comparison(expression.op, expression.args[0], expression.args[1]),
            boolean_variables,
        )
    except ProofDeferred:
        return None
    if len(inequalities) != 1 or not inequalities[0].coefficients:
        return None
    inequality = inequalities[0]
    scale = abs(inequality.coefficients[0][1])
    if scale == 0:
        return None
    return (
        tuple((name, value / scale) for name, value in inequality.coefficients),
        inequality.bound / scale,
        inequality.strict,
    )


def _normalized_bound_record(
    normalized: tuple[tuple[tuple[str, Fraction], ...], Fraction, bool],
) -> dict[str, Any]:
    coefficients, bound, strict = normalized
    return {
        "coefficients": {
            name: _fraction_text(value) for name, value in coefficients
        },
        "bound": _fraction_text(bound),
        "strict": strict,
    }


def _strictly_stronger_bound(
    left: tuple[tuple[tuple[str, Fraction], ...], Fraction, bool],
    right: tuple[tuple[tuple[str, Fraction], ...], Fraction, bool],
) -> bool:
    return left[1] < right[1] or (
        left[1] == right[1] and left[2] and not right[2]
    )


def _reduce_constraint_list(
    expressions: Iterable[Expr],
    boolean_variables: set[str],
) -> tuple[list[Expr], list[dict[str, Any]]]:
    retained: dict[str, Expr] = {}
    linear: dict[tuple[tuple[str, Fraction], ...], tuple[Expr, tuple]] = {}
    removals: list[dict[str, Any]] = []
    for expression in expressions:
        expression = simplify(expression)
        source_hash = expression_hash(expression)
        normalized = _normalized_linear_bound(expression, boolean_variables)
        if normalized is None:
            if source_hash in retained:
                removals.append({
                    "rule": "exact_duplicate_constraint_v1",
                    "removed_expression": expr_to_dict(expression),
                    "removed_expression_sha256": source_hash,
                    "retained_expression_sha256": source_hash,
                })
            else:
                retained[source_hash] = expression
            continue
        coefficients = normalized[0]
        previous = linear.get(coefficients)
        if previous is None:
            linear[coefficients] = (expression, normalized)
            continue
        previous_expression, previous_normalized = previous
        if _strictly_stronger_bound(normalized, previous_normalized):
            removed_expression = previous_expression
            removed_normalized = previous_normalized
            linear[coefficients] = (expression, normalized)
            dominating_expression = expression
            dominating_normalized = normalized
        else:
            removed_expression = expression
            removed_normalized = normalized
            dominating_expression = previous_expression
            dominating_normalized = previous_normalized
        removals.append({
            "rule": "normalized_linear_bound_dominance_v1",
            "removed_expression": expr_to_dict(removed_expression),
            "removed_expression_sha256": expression_hash(removed_expression),
            "removed_normalized_bound": _normalized_bound_record(
                removed_normalized
            ),
            "dominating_expression": expr_to_dict(dominating_expression),
            "dominating_expression_sha256": expression_hash(
                dominating_expression
            ),
            "dominating_normalized_bound": _normalized_bound_record(
                dominating_normalized
            ),
        })
    for expression, _normalized in linear.values():
        retained[expression_hash(expression)] = expression
    return [retained[key] for key in sorted(retained)], removals


def _normalize_constraint_expression(
    expression: Expr,
    boolean_variables: set[str],
    removals: list[dict[str, Any]],
) -> Expr:
    if isinstance(expression, (Const, Var, RawRef)):
        return expression
    if isinstance(expression, Ite):
        return simplify(Ite(
            _normalize_constraint_expression(
                expression.cond, boolean_variables, removals
            ),
            _normalize_constraint_expression(
                expression.then_expr, boolean_variables, removals
            ),
            _normalize_constraint_expression(
                expression.else_expr, boolean_variables, removals
            ),
        ))
    if not isinstance(expression, Op):
        return expression
    normalized_args = tuple(
        _normalize_constraint_expression(item, boolean_variables, removals)
        for item in expression.args
    )
    normalized = simplify(Op(expression.op, normalized_args))
    if not isinstance(normalized, Op) or normalized.op not in {"and", "or"}:
        return normalized
    items = list(normalized.args)
    if normalized.op == "and":
        items, local_removals = _reduce_constraint_list(
            items,
            boolean_variables,
        )
        removals.extend(local_removals)
    else:
        unique = {expression_hash(item): item for item in items}
        items = [unique[key] for key in sorted(unique)]
    return simplify(Op(normalized.op, tuple(items)))


def _conjunct_hashes(expression: Expr) -> set[str]:
    if isinstance(expression, Const) and expression.value is True:
        return set()
    if isinstance(expression, Op) and expression.op == "and":
        hashes: set[str] = set()
        for argument in expression.args:
            hashes.update(_conjunct_hashes(argument))
        return hashes
    return {expression_hash(expression)}


def _conjuncts(expression: Expr) -> list[Expr]:
    if isinstance(expression, Const) and expression.value is True:
        return []
    if isinstance(expression, Op) and expression.op == "and":
        result: list[Expr] = []
        for argument in expression.args:
            result.extend(_conjuncts(argument))
        return result
    return [expression]


def _bound_implies(
    stronger: tuple[tuple[tuple[str, Fraction], ...], Fraction, bool],
    weaker: tuple[tuple[tuple[str, Fraction], ...], Fraction, bool],
) -> bool:
    return stronger[0] == weaker[0] and (
        stronger[1] < weaker[1]
        or (
            stronger[1] == weaker[1]
            and (not weaker[2] or stronger[2])
        )
    )


def _conjunction_covers(weaker: Expr, stronger: Expr) -> bool:
    stronger_conjuncts = _conjuncts(stronger)
    stronger_hashes = {expression_hash(item) for item in stronger_conjuncts}
    stronger_bounds = [
        normalized
        for item in stronger_conjuncts
        if (normalized := _normalized_linear_bound(item, set())) is not None
    ]
    stronger_linear = [
        item
        for item in stronger_conjuncts
        if expression_is_linear(item, set())[0]
    ]
    for item in _conjuncts(weaker):
        if expression_hash(item) in stronger_hashes:
            continue
        normalized = _normalized_linear_bound(item, set())
        if normalized is not None and any(
            _bound_implies(candidate, normalized)
            for candidate in stronger_bounds
        ):
            continue
        if not expression_is_linear(item, set())[0]:
            return False
        try:
            implication = prove_implication_exact(
                stronger_linear,
                item,
                set(),
            )
        except ProofDeferred:
            return False
        if implication.get("proved") is not True:
            return False
    return True


def _merge_contained_cases(
    cases: list[ReducedCase],
    case_rows: list[dict[str, Any]],
) -> tuple[list[ReducedCase], list[dict[str, Any]], int]:
    expression_sets = {
        item.case_id: _conjunct_hashes(item.expression) for item in cases
    }
    reachability_sets = {
        item.case_id: _conjunct_hashes(
            item.reachability_expression or item.expression
        )
        for item in cases
    }
    rows_by_id = {row["case_id"]: row for row in case_rows}
    ordered = sorted(
        cases,
        key=lambda item: (
            len(expression_sets[item.case_id])
            + len(reachability_sets[item.case_id]),
            len(expression_sets[item.case_id]),
            len(reachability_sets[item.case_id]),
            item.boolean_assignment,
            item.case_id,
        ),
    )
    retained: list[ReducedCase] = []
    for candidate in ordered:
        dominating = next((
            item
            for item in retained
            if item.boolean_assignment == candidate.boolean_assignment
            and _conjunction_covers(item.expression, candidate.expression)
            and _conjunction_covers(
                item.reachability_expression or item.expression,
                candidate.reachability_expression or candidate.expression,
            )
        ), None)
        if dominating is None:
            retained.append(candidate)
            continue
        candidate_row = rows_by_id[candidate.case_id]
        dominating_row = rows_by_id[dominating.case_id]
        source = {
            key: value
            for key, value in candidate_row.items()
            if key != "merged_sources"
        }
        source["rule"] = "checked_case_containment_v2"
        dominating_row["merged_sources"].append(source)
        for nested_source in candidate_row.get("merged_sources", []):
            transferred = dict(nested_source)
            transferred["rule"] = "checked_case_containment_v2"
            dominating_row["merged_sources"].append(transferred)
    retained_ids = {item.case_id for item in retained}
    retained.sort(key=lambda item: item.case_id)
    retained_rows = [
        row for row in case_rows if row["case_id"] in retained_ids
    ]
    containment_merged_count = sum(
        source.get("rule") in {
            "conjunctive_case_containment_v1",
            "checked_case_containment_v2",
        }
        for row in retained_rows
        for source in row.get("merged_sources", [])
    )
    return (
        retained,
        retained_rows,
        containment_merged_count,
    )
