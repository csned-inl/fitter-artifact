"""Verify grouped cases, removed constraints, and merged case sources."""

from __future__ import annotations

from typing import Any

from .convex import verify_recorded_convex_certificate, verify_recorded_outer_reduction
from .expressions import (
    _normalized_bound_values,
    _serialized_conjunct_hashes,
    _serialized_conjunction_covers,
    _serialized_conjuncts,
    _serialized_expression_hash,
    _serialized_linear_constraints,
    _serialized_normalized_linear_bound,
)
from .linear import verify_recorded_linear_certificate


def _verify_common_case_group(
    proof: dict[str, Any],
    case: dict[str, Any],
) -> list[str]:
    reduction = proof.get("group_reduction")
    if reduction is None:
        return []
    if not isinstance(reduction, dict):
        return ["common case group reduction is malformed"]
    errors: list[str] = []
    rule = reduction.get("rule")
    if rule not in {
        "common_conjunctive_case_group_v1",
        "certified_conjunctive_subset_v2",
    }:
        errors.append("common case group reduction rule is invalid")
    source = case.get("expression")
    shared = reduction.get("shared_expression")
    if not isinstance(source, dict) or reduction.get(
        "source_expression_sha256"
    ) != _serialized_expression_hash(source):
        errors.append("common case group source expression hash is invalid")
        return errors
    if not isinstance(shared, dict) or reduction.get(
        "shared_expression_sha256"
    ) != _serialized_expression_hash(shared):
        errors.append("common case group expression hash is invalid")
        return errors
    source_hashes = {
        _serialized_expression_hash(item) for item in _serialized_conjuncts(source)
    }
    shared_hashes = sorted(
        _serialized_expression_hash(item) for item in _serialized_conjuncts(shared)
    )
    if reduction.get("shared_conjunct_sha256") != shared_hashes:
        errors.append("common case group conjunct hashes are invalid")
    if not set(shared_hashes) <= source_hashes:
        errors.append("common case group is not a conjunction subset")
    minimum_covered = 2 if rule == "common_conjunctive_case_group_v1" else 1
    if not isinstance(reduction.get("covered_case_count"), int) or reduction.get(
        "covered_case_count"
    ) < minimum_covered:
        errors.append("common case group size is invalid")
    if rule == "certified_conjunctive_subset_v2" and (
        not isinstance(reduction.get("starting_group_case_count"), int)
        or reduction.get("starting_group_case_count") < 1
        or not isinstance(reduction.get("subset_minimization_checks"), int)
        or reduction.get("subset_minimization_checks") < 0
    ):
        errors.append("certified conjunctive subset record is malformed")
    return errors


def _verify_linear_certificate_source(
    certificate: dict[str, Any],
    proof: dict[str, Any],
    case: dict[str, Any],
) -> list[str]:
    reduction = proof.get("group_reduction") or {}
    expression = reduction.get("shared_expression", case.get("expression"))
    try:
        expected = _serialized_linear_constraints(expression)
    except (TypeError, ValueError, ZeroDivisionError):
        return ["linear certificate source expression is malformed"]
    if certificate.get("constraints") != expected:
        return ["linear certificate does not match its source expression"]
    return []


def _verify_constraint_removals(records: Any) -> list[str]:
    if not isinstance(records, list):
        return ["constraint removal records are malformed"]
    errors: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            errors.append("constraint removal record is malformed")
            continue
        removed = record.get("removed_expression")
        if not isinstance(removed, dict) or record.get(
            "removed_expression_sha256"
        ) != _serialized_expression_hash(removed):
            errors.append("removed constraint expression hash is invalid")
            continue
        rule = record.get("rule")
        if rule == "exact_duplicate_constraint_v1":
            if record.get("retained_expression_sha256") != record.get(
                "removed_expression_sha256"
            ):
                errors.append("duplicate constraint removal is not identical")
            continue
        if rule != "normalized_linear_bound_dominance_v1":
            errors.append("constraint removal rule is invalid")
            continue
        dominating = record.get("dominating_expression")
        if not isinstance(dominating, dict) or record.get(
            "dominating_expression_sha256"
        ) != _serialized_expression_hash(dominating):
            errors.append("dominating constraint expression hash is invalid")
        try:
            removed_coefficients, removed_bound, removed_strict = (
                _normalized_bound_values(record.get("removed_normalized_bound"))
            )
            dominating_coefficients, dominating_bound, dominating_strict = (
                _normalized_bound_values(record.get("dominating_normalized_bound"))
            )
        except (TypeError, ValueError, ZeroDivisionError):
            errors.append("normalized constraint dominance record is malformed")
            continue
        try:
            if (
                removed_coefficients,
                removed_bound,
                removed_strict,
            ) != _serialized_normalized_linear_bound(removed):
                errors.append("removed normalized bound does not match its expression")
            if (
                dominating_coefficients,
                dominating_bound,
                dominating_strict,
            ) != _serialized_normalized_linear_bound(dominating):
                errors.append(
                    "dominating normalized bound does not match its expression"
                )
        except (TypeError, ValueError, ZeroDivisionError):
            errors.append("constraint dominance expression is not linear")
        if removed_coefficients != dominating_coefficients:
            errors.append("constraint dominance uses different normalized left sides")
        if not (
            dominating_bound < removed_bound
            or (
                dominating_bound == removed_bound
                and (dominating_strict or not removed_strict)
            )
        ):
            errors.append("dominating constraint is not stronger")
    return errors


def _verify_merged_case_sources(
    retained: dict[str, Any],
    sources: Any,
) -> list[str]:
    if not isinstance(sources, list):
        return ["merged case sources are malformed"]
    errors: list[str] = []
    retained_expression = retained.get("expression")
    retained_reachability = retained.get("reachability_expression")
    if not isinstance(retained_expression, dict) or not isinstance(
        retained_reachability, dict
    ):
        return ["retained case expressions are malformed"]
    retained_conjuncts = _serialized_conjunct_hashes(retained_expression)
    retained_reachability_conjuncts = _serialized_conjunct_hashes(
        retained_reachability
    )
    for source in sources:
        if not isinstance(source, dict):
            errors.append("merged case source is malformed")
            continue
        expression = source.get("expression")
        reachability = source.get("reachability_expression")
        if not isinstance(expression, dict) or source.get(
            "expression_sha256"
        ) != _serialized_expression_hash(expression):
            errors.append("merged case expression hash is invalid")
            continue
        if not isinstance(reachability, dict) or source.get(
            "reachability_expression_sha256"
        ) != _serialized_expression_hash(reachability):
            errors.append("merged case reachability expression hash is invalid")
            continue
        if source.get("boolean_assignment") != retained.get(
            "boolean_assignment"
        ):
            errors.append("merged case Boolean assignment differs")
        if source.get("obligation") != retained.get("obligation"):
            errors.append("merged case obligation differs")
        reduction = source.get("constraint_reduction") or {}
        if reduction.get("rule") != "canonical_conjunction_reduction_v1":
            errors.append("merged case constraint reduction rule is invalid")
        errors.extend(_verify_constraint_removals(
            reduction.get("removed_constraints")
        ))
        errors.extend(_verify_constraint_removals(
            reduction.get("reachability_removed_constraints")
        ))
        expression_conjuncts = _serialized_conjunct_hashes(expression)
        reachability_conjuncts = _serialized_conjunct_hashes(reachability)
        rule = source.get("rule")
        if rule == "exact_duplicate_case_v1":
            if (
                retained_conjuncts != expression_conjuncts
                or retained_reachability_conjuncts != reachability_conjuncts
            ):
                errors.append("duplicate merged case is not identical")
        elif rule == "conjunctive_case_containment_v1":
            if (
                not retained_conjuncts <= expression_conjuncts
                or not retained_reachability_conjuncts
                <= reachability_conjuncts
            ):
                errors.append("contained merged case is not covered")
        elif rule == "checked_case_containment_v2":
            try:
                covered = _serialized_conjunction_covers(
                    retained_expression,
                    expression,
                ) and _serialized_conjunction_covers(
                    retained_reachability,
                    reachability,
                )
            except (TypeError, ValueError, ZeroDivisionError):
                covered = False
            if not covered:
                errors.append("checked merged case is not covered")
        else:
            errors.append("merged case rule is invalid")
    return errors
