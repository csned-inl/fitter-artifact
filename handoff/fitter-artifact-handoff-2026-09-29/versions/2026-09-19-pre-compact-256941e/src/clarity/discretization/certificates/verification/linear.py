"""Verify recorded linear certificates and exact counterexamples."""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from .numbers import fraction_text, parse_fraction


def _fraction_mapping(value: Any) -> dict[str, Fraction]:
    if not isinstance(value, dict):
        raise ValueError("coefficient mapping is malformed")
    return {str(name): parse_fraction(number) for name, number in value.items()}


def verify_recorded_linear_certificate(certificate: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if certificate.get("kind") != "linear_infeasibility_weights_v1":
        errors.append("linear certificate kind is invalid")
    try:
        constraints = certificate["constraints"]
        if not isinstance(constraints, list):
            raise ValueError
        coefficients = [_fraction_mapping(item["coefficients"]) for item in constraints]
        bounds = [parse_fraction(item["bound"]) for item in constraints]
        strict = [item.get("strict") is True for item in constraints]
        if any(item.get("strict") not in {True, False} for item in constraints):
            errors.append("linear certificate strictness is malformed")
        multipliers = [parse_fraction(value) for value in certificate["multipliers"]]
    except (AttributeError, KeyError, TypeError, ValueError, ZeroDivisionError):
        errors.append("linear certificate contents are malformed")
        return errors
    if len(multipliers) != len(constraints):
        errors.append("linear certificate multiplier count is incorrect")
        return errors
    if any(value < 0 for value in multipliers):
        errors.append("linear certificate contains a negative multiplier")
    combined: dict[str, Fraction] = {}
    combined_bound = Fraction(0)
    for row, bound, multiplier in zip(coefficients, bounds, multipliers):
        for name, value in row.items():
            combined[name] = combined.get(name, Fraction(0)) + multiplier * value
        combined_bound += multiplier * bound
    combined = {name: value for name, value in sorted(combined.items()) if value}
    combined_strict = any(
        is_strict and multiplier > 0
        for is_strict, multiplier in zip(strict, multipliers)
    )
    if combined:
        errors.append("linear weighted sum does not eliminate every variable")
    if combined_bound > 0 or (combined_bound == 0 and not combined_strict):
        errors.append("linear weighted sum does not produce a contradiction")
    if certificate.get("combined_coefficients") != {
        name: fraction_text(value) for name, value in combined.items()
    }:
        errors.append("linear certificate combined coefficients are incorrect")
    if certificate.get("combined_bound") != fraction_text(combined_bound):
        errors.append("linear certificate combined bound is incorrect")
    if certificate.get("combined_strict") is not combined_strict:
        errors.append("linear certificate combined strictness is incorrect")
    return errors


def verify_recorded_linear_counterexample(proof: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        witness = _fraction_mapping(proof["counterexample"])
        constraints = proof["constraints"]
        if not isinstance(constraints, list):
            raise ValueError
        for item in constraints:
            coefficients = _fraction_mapping(item["coefficients"])
            bound = parse_fraction(item["bound"])
            if item.get("strict") not in {True, False}:
                raise ValueError
            if any(name not in witness for name in coefficients):
                errors.append("linear counterexample omits a required variable")
                continue
            left = sum(
                coefficient * witness[name]
                for name, coefficient in coefficients.items()
            )
            if left > bound or (item["strict"] and left == bound):
                errors.append("linear counterexample violates a recorded constraint")
    except (AttributeError, KeyError, TypeError, ValueError, ZeroDivisionError):
        errors.append("linear counterexample contents are malformed")
    return errors
