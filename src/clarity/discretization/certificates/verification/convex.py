"""Verify recorded convex certificates and certified outer reductions."""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from .linear import _fraction_mapping, verify_recorded_linear_certificate
from .numbers import fraction_text, parse_fraction


def verify_recorded_outer_reduction(
    proof: dict[str, Any],
    source_expression_sha256: str,
) -> list[str]:
    outer = proof.get("outer_reduction")
    if outer is None:
        return []
    errors: list[str] = []
    if outer.get("rule") not in {
        "linear_skeleton_outer_reduction_v1",
        "certified_bounded_product_linear_envelope_v1",
        "certified_square_tangent_secant_envelope_v1",
    }:
        errors.append("outer reduction rule is invalid")
    if outer.get("source_expression_sha256") != source_expression_sha256:
        errors.append("outer reduction source expression hash is invalid")
    for bound in outer.get("bounds", []):
        variable = bound.get("variable")
        if not isinstance(variable, str):
            errors.append("outer reduction bound variable is malformed")
            continue
        for direction in ("lower", "upper"):
            value = bound.get(direction)
            recorded_proof = bound.get(direction + "_proof")
            if value is None:
                if recorded_proof is not None:
                    errors.append("outer reduction records a proof for a missing bound")
                continue
            try:
                exact_value = parse_fraction(value)
                certificate = recorded_proof["proof"]["certificate"]
                constraints = certificate["constraints"]
                final = constraints[-1]
            except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError):
                errors.append("outer reduction bound proof is malformed")
                continue
            if recorded_proof.get("outcome") != "CERTIFIED":
                errors.append("outer reduction bound is not certified")
            if certificate.get("kind") == "linear_infeasibility_weights_v1":
                errors.extend(
                    "outer reduction bound certificate: " + error
                    for error in verify_recorded_linear_certificate(certificate)
                )
                expected_coefficients = {
                    variable: fraction_text(
                        Fraction(1) if direction == "lower" else Fraction(-1)
                    )
                }
                expected_bound = fraction_text(
                    exact_value if direction == "lower" else -exact_value
                )
                if (
                    final.get("coefficients") != expected_coefficients
                    or final.get("bound") != expected_bound
                    or final.get("strict") is not True
                ):
                    errors.append("outer reduction bound proof checks the wrong inequality")
            elif certificate.get("kind") == "convex_dual_bound_v1":
                errors.extend(
                    "outer reduction bound certificate: " + error
                    for error in verify_recorded_convex_certificate(certificate)
                )
                expected_linear = {
                    variable: fraction_text(
                        Fraction(1) if direction == "lower" else Fraction(-1)
                    )
                }
                expected_constant = fraction_text(
                    -exact_value if direction == "lower" else exact_value
                )
                if (
                    final.get("square") != {}
                    or final.get("linear") != expected_linear
                    or final.get("constant") != expected_constant
                    or final.get("relation") != "< 0"
                ):
                    errors.append("outer reduction bound proof checks the wrong inequality")
            else:
                errors.append("outer reduction bound certificate kind is invalid")
    return errors


def verify_recorded_convex_certificate(certificate: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if certificate.get("kind") != "convex_dual_bound_v1":
        errors.append("convex certificate kind is invalid")
    try:
        constraints = certificate["constraints"]
        if not isinstance(constraints, list):
            raise ValueError
        squares = [_fraction_mapping(item["square"]) for item in constraints]
        linears = [_fraction_mapping(item["linear"]) for item in constraints]
        constants = [parse_fraction(item["constant"]) for item in constraints]
        strict = [item.get("relation") == "< 0" for item in constraints]
        if any(item.get("relation") not in {"<= 0", "< 0"} for item in constraints):
            errors.append("convex certificate relation is malformed")
        multipliers = [parse_fraction(value) for value in certificate["multipliers"]]
    except (AttributeError, KeyError, TypeError, ValueError, ZeroDivisionError):
        errors.append("convex certificate contents are malformed")
        return errors
    if len(multipliers) != len(constraints):
        errors.append("convex certificate multiplier count is incorrect")
        return errors
    if any(value < 0 for value in multipliers):
        errors.append("convex certificate contains a negative multiplier")
    if not any(value > 0 for value in multipliers):
        errors.append("convex certificate multipliers are all zero")
    if any(value < 0 for square in squares for value in square.values()):
        errors.append("convex certificate contains a nonconvex source constraint")

    combined_square: dict[str, Fraction] = {}
    combined_linear: dict[str, Fraction] = {}
    combined_constant = Fraction(0)
    combined_strict = False
    for square, linear, constant, multiplier in zip(
        squares, linears, constants, multipliers
    ):
        for name, value in square.items():
            combined_square[name] = combined_square.get(name, Fraction(0)) + multiplier * value
        for name, value in linear.items():
            combined_linear[name] = combined_linear.get(name, Fraction(0)) + multiplier * value
        combined_constant += multiplier * constant
    combined_strict = any(
        is_strict and multiplier > 0
        for is_strict, multiplier in zip(strict, multipliers)
    )
    combined_square = {
        name: value for name, value in sorted(combined_square.items()) if value
    }
    combined_linear = {
        name: value for name, value in sorted(combined_linear.items()) if value
    }
    lower_bound: Fraction | None = combined_constant
    for variable in sorted(set(combined_square) | set(combined_linear)):
        quadratic = combined_square.get(variable, Fraction(0))
        linear = combined_linear.get(variable, Fraction(0))
        if quadratic < 0 or (quadratic == 0 and linear != 0):
            lower_bound = None
            break
        if quadratic > 0:
            lower_bound -= linear * linear / (4 * quadratic)
    if lower_bound is None or lower_bound < 0 or (
        lower_bound == 0 and not combined_strict
    ):
        errors.append("convex weighted sum does not exclude the constrained region")
    expected_combined = {
        "square": {
            name: fraction_text(value) for name, value in combined_square.items()
        },
        "linear": {
            name: fraction_text(value) for name, value in combined_linear.items()
        },
        "constant": fraction_text(combined_constant),
        "relation": "< 0" if combined_strict else "<= 0",
    }
    if certificate.get("combined_quadratic") != expected_combined:
        errors.append("convex certificate combined quadratic is incorrect")
    expected_bound = fraction_text(lower_bound) if lower_bound is not None else None
    if certificate.get("global_lower_bound") != expected_bound:
        errors.append("convex certificate global lower bound is incorrect")
    return errors
