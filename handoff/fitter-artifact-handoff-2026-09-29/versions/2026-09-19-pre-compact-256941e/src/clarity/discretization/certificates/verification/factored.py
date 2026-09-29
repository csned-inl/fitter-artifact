"""Verify lazy factored proof construction independently of its producer."""

from __future__ import annotations

from typing import Any

from clarity.certification.equations import Const, Op

from .convex import verify_recorded_convex_certificate, verify_convex_source
from .expressions import (
    _affine_interval_endpoint_split,
    _factored_and,
    _factored_canonical,
    _factored_expr_from_dict,
    _factored_first_ite,
    _factored_first_or,
    _factored_normal_boolean,
    _factored_or,
    _factored_replace,
    _serialized_common_conjuncts,
    _serialized_conjuncts,
    _serialized_contains,
    _serialized_expression_hash,
    _serialized_linear_constraints,
    expr_to_dict,
    substitute,
)
from .linear import verify_recorded_linear_certificate


def _verify_nested_factored_attempt(
    attempt: Any,
    expression: Any,
    checker: str,
) -> list[str]:
    if not isinstance(attempt, dict) or attempt.get("outcome") != "CERTIFIED":
        return ["factored proof leaf is not certified"]
    proof = attempt.get("proof") or {}
    certificate = proof.get("certificate")
    if checker in {"linear", "reachability_arithmetic"}:
        if not isinstance(certificate, dict):
            return ["factored linear proof certificate is missing"]
        errors = verify_recorded_linear_certificate(certificate)
        try:
            expected = _serialized_linear_constraints(expression)
        except (TypeError, ValueError, ZeroDivisionError):
            errors.append("factored linear source expression is malformed")
        else:
            if certificate.get("constraints") != expected:
                errors.append(
                    "factored linear certificate does not match its source"
                )
        return errors
    if checker == "convex":
        if not isinstance(certificate, dict):
            return ["factored convex proof certificate is missing"]
        return verify_recorded_convex_certificate(certificate) + verify_convex_source(certificate, expression)
    return ["factored proof checker is unsupported"]


def _verify_lazy_factored_stage(
    stage: dict[str, Any],
    case: dict[str, Any],
) -> list[str]:
    proof = stage.get("proof") or {}
    errors: list[str] = []
    if proof.get("rule") != "lazy_factored_formula_coverage_v1":
        return ["factored coverage proof rule is invalid"]
    source = proof.get("source_expression")
    if not isinstance(source, dict) or source != case.get("expression") or proof.get(
        "source_expression_sha256"
    ) != _serialized_expression_hash(source):
        return ["factored coverage source does not match its case"]
    if proof.get("coverage_complete") is not True:
        errors.append("certified factored coverage is incomplete")
    checker = str(proof.get("checker") or stage.get("checker"))
    boolean_variables_record = proof.get("boolean_variables")
    if (
        not isinstance(boolean_variables_record, list)
        or any(not isinstance(item, str) for item in boolean_variables_record)
        or boolean_variables_record != sorted(set(boolean_variables_record))
    ):
        return ["factored Boolean variable inventory is malformed"]
    boolean_variables = set(boolean_variables_record)
    certified: set[str] = set()

    def visit(node: Any, expected: Any) -> None:
        if not isinstance(node, dict):
            errors.append("factored proof node is malformed")
            return
        rule = node.get("rule")
        if rule == "reused_factored_proof_v1":
            reference = node.get("expression_sha256")
            if reference != _serialized_expression_hash(expected):
                errors.append('factored reused proof has the wrong obligation')
            if reference not in certified:
                errors.append("factored proof reuse has no prior certified node")
            return
        expression = node.get("expression")
        expression_hash = node.get("expression_sha256")
        if expression != expected:
            errors.append('factored child proof does not match the expected obligation')
            return
        if not isinstance(expression, dict) or expression_hash != (
            _serialized_expression_hash(expression)
        ):
            errors.append("factored proof node expression hash is invalid")
            return
        before = len(errors)
        if rule == "exact_boolean_normalization_v1":
            normalized = node.get("normalized_expression")
            normalized_hash = node.get("normalized_expression_sha256")
            try:
                expected_normalized = expr_to_dict(_factored_canonical(
                    _factored_normal_boolean(
                        _factored_expr_from_dict(expression),
                        boolean_variables,
                    )
                ))
            except (TypeError, ValueError):
                expected_normalized = None
            if (
                not isinstance(normalized, dict)
                or normalized_hash != _serialized_expression_hash(normalized)
                or node.get("outcome") != "CERTIFIED"
                or normalized != expected_normalized
            ):
                errors.append("factored Boolean normalization is malformed")
            else:
                visit(node.get("proof"), normalized)
                if normalized_hash not in certified:
                    errors.append(
                        "factored Boolean normalization child is not covered"
                    )
        elif rule == "constant_false_factored_leaf_v1":
            if expression != {"type": "const", "value": False}:
                errors.append("factored constant leaf is not false")
        elif rule == "common_conjunct_factored_proof_v1":
            common = node.get("common_expression")
            if not isinstance(common, dict) or node.get(
                "common_expression_sha256"
            ) != _serialized_expression_hash(common):
                errors.append("factored common expression hash is invalid")
            else:
                available = _serialized_common_conjuncts(expression)
                common_hashes = {
                    _serialized_expression_hash(item)
                    for item in _serialized_conjuncts(common)
                }
                if not common_hashes <= set(available):
                    errors.append(
                        "factored common proof is not shared by every branch"
                    )
                errors.extend(_verify_nested_factored_attempt(
                    node.get("checker_attempt"),
                    common,
                    checker,
                ))
        elif rule == "factored_checker_leaf_v1":
            errors.extend(_verify_nested_factored_attempt(
                node.get("checker_attempt"),
                expression,
                checker,
            ))
        elif rule == "exact_factored_split_v1":
            children = node.get("children")
            if not isinstance(children, list) or len(children) != 2:
                errors.append("factored split does not have two children")
                return
            split_kind = node.get("split_kind")
            expected_children: list[dict[str, Any]] | None = None
            try:
                parent_expression = _factored_expr_from_dict(expression)
            except (TypeError, ValueError):
                parent_expression = None
            if split_kind == "boolean_variable":
                variable = node.get("variable")
                if not isinstance(variable, str) or not _serialized_contains(
                    expression,
                    {"type": "var", "name": variable},
                ):
                    errors.append("factored Boolean split variable is absent")
                elif variable not in boolean_variables or parent_expression is None:
                    errors.append("factored Boolean split variable is invalid")
                else:
                    expected_children = [
                        expr_to_dict(_factored_canonical(substitute(
                            parent_expression,
                            {variable: Const(value)},
                        )))
                        for value in (False, True)
                    ]
            elif split_kind == "logical_alternative":
                alternative = node.get("alternative_expression")
                if (
                    not isinstance(alternative, dict)
                    or alternative.get("type") != "op"
                    or alternative.get("op") != "or"
                    or not _serialized_contains(expression, alternative)
                ):
                    errors.append("factored logical split source is invalid")
                elif parent_expression is not None:
                    selected = _factored_first_or(parent_expression)
                    if selected is None or expr_to_dict(selected) != alternative:
                        errors.append("factored logical split source is not first")
                    else:
                        alternatives = list(selected.args)
                        expected_children = [
                            expr_to_dict(_factored_replace(
                                parent_expression,
                                selected,
                                alternatives[0],
                            )),
                            expr_to_dict(_factored_replace(
                                parent_expression,
                                selected,
                                _factored_or(alternatives[1:]),
                            )),
                        ]
            elif split_kind == "conditional":
                condition = node.get("condition")
                if not isinstance(condition, dict):
                    errors.append("factored conditional split is malformed")
                elif parent_expression is not None:
                    selected = _factored_first_ite(parent_expression)
                    if selected is None or expr_to_dict(selected.cond) != condition:
                        errors.append("factored conditional split source is invalid")
                    else:
                        expected_children = [
                            expr_to_dict(_factored_and([
                                _factored_normal_boolean(
                                    selected.cond,
                                    boolean_variables,
                                    truth=value,
                                ),
                                _factored_replace(
                                    parent_expression,
                                    selected,
                                    selected.then_expr if value else selected.else_expr,
                                ),
                            ]))
                            for value in (False, True)
                        ]
            elif split_kind == "affine_interval_endpoints":
                if parent_expression is None:
                    errors.append("factored interval endpoint source is malformed")
                else:
                    reduction = _affine_interval_endpoint_split(parent_expression)
                    if reduction is None:
                        errors.append("factored interval endpoint reduction is invalid")
                    else:
                        expected, expected_record = reduction
                        expected_fields = {
                            "relaxed_expression": expr_to_dict(
                                expected_record["relaxed_expression"]
                            ),
                            "relaxed_expression_sha256": _serialized_expression_hash(
                                expr_to_dict(expected_record["relaxed_expression"])
                            ),
                            "removed_interval_bounds": [
                                expr_to_dict(item)
                                for item in expected_record["removed_interval_bounds"]
                            ],
                            "removed_time_gates": [
                                expr_to_dict(item)
                                for item in expected_record["removed_time_gates"]
                            ],
                            "changing_comparison": expr_to_dict(
                                expected_record["changing_comparison"]
                            ),
                            "changing_comparison_sha256": _serialized_expression_hash(
                                expr_to_dict(expected_record["changing_comparison"])
                            ),
                            "time_variable": expected_record["time_variable"],
                            "endpoints": [
                                expr_to_dict(item)
                                for item in expected_record["endpoints"]
                            ],
                        }
                        for field, expected_value in expected_fields.items():
                            if node.get(field) != expected_value:
                                errors.append(
                                    f"factored interval endpoint {field} is invalid"
                                )
                        expected_children = [expr_to_dict(item) for item in expected]
            else:
                errors.append("factored split kind is invalid")
            for index, child in enumerate(children):
                child_expression = child.get("expression") if isinstance(
                    child,
                    dict,
                ) else None
                if (
                    not isinstance(child, dict)
                    or child.get("branch") != index
                    or child.get("outcome") != "CERTIFIED"
                    or not isinstance(child_expression, dict)
                    or child.get("expression_sha256")
                    != _serialized_expression_hash(child_expression)
                    or expected_children is None
                    or child_expression != expected_children[index]
                ):
                    errors.append("factored split child is malformed")
                    continue
                visit(child.get("proof"), child_expression)
        else:
            errors.append("factored proof node rule is invalid")
        if len(errors) == before:
            certified.add(expression_hash)

    visit(proof.get("tree"), source)
    if _serialized_expression_hash(source) not in certified:
        errors.append("factored proof tree does not cover its root")
    return errors
