"""Exact-number and linear-elimination primitives owned by the verifier."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any


class ProofDeferred(ValueError):
    """The verifier cannot apply its exact arithmetic rule."""

    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def fraction_text(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def parse_fraction(value: Any) -> Fraction:
    if not isinstance(value, str):
        raise ValueError("exact number must be a string")
    return Fraction(value)


class LinearInequality:
    """A linear inequality represented as coefficients times variables <= bound."""

    coefficients: tuple[tuple[str, Fraction], ...]
    bound: Fraction
    strict: bool = False

    @staticmethod
    def make(
        coefficients: dict[str, Fraction],
        bound: Fraction,
        strict: bool = False,
    ) -> "LinearInequality":
        return LinearInequality(
            tuple(sorted((name, value) for name, value in coefficients.items() if value)),
            bound,
            strict,
        )

    def coeff_dict(self) -> dict[str, Fraction]:
        return dict(self.coefficients)



def _constant_contradiction(inequality: LinearInequality) -> bool:
    if inequality.coefficients:
        return False
    return inequality.bound < 0 or (inequality.bound == 0 and inequality.strict)


def exact_linear_infeasible(
    inequalities: list[LinearInequality],
    *,
    elimination_limit: int = 50000,
) -> tuple[bool, dict[str, Any]]:
    work = list(dict.fromkeys(inequalities))
    peak = len(work)
    if any(_constant_contradiction(item) for item in work):
        return True, {"variables_eliminated": 0, "peak_inequalities": peak}

    variables = sorted({name for item in work for name, _value in item.coefficients})
    eliminated = 0
    for variable in variables:
        positive: list[tuple[LinearInequality, Fraction]] = []
        negative: list[tuple[LinearInequality, Fraction]] = []
        zero: list[LinearInequality] = []
        for item in work:
            coeff = item.coeff_dict().get(variable, Fraction(0))
            if coeff > 0:
                positive.append((item, coeff))
            elif coeff < 0:
                negative.append((item, coeff))
            else:
                zero.append(item)

        combined: list[LinearInequality] = list(zero)
        if positive and negative:
            for upper, upper_coeff in positive:
                upper_terms = upper.coeff_dict()
                upper_terms.pop(variable, None)
                for lower, lower_coeff in negative:
                    lower_terms = lower.coeff_dict()
                    lower_terms.pop(variable, None)
                    coefficients: dict[str, Fraction] = {}
                    for name, value in upper_terms.items():
                        coefficients[name] = coefficients.get(name, Fraction(0)) + (-lower_coeff) * value
                    for name, value in lower_terms.items():
                        coefficients[name] = coefficients.get(name, Fraction(0)) + upper_coeff * value
                    combined.append(LinearInequality.make(
                        coefficients,
                        (-lower_coeff) * upper.bound + upper_coeff * lower.bound,
                        upper.strict or lower.strict,
                    ))
                    if len(combined) > elimination_limit:
                        raise ProofDeferred(
                            "NUMERIC_BOUND_INCONCLUSIVE",
                            f"exact elimination exceeded {elimination_limit} inequalities",
                        )
        work = list(dict.fromkeys(combined))
        peak = max(peak, len(work))
        eliminated += 1
        if any(_constant_contradiction(item) for item in work):
            return True, {
                "variables_eliminated": eliminated,
                "peak_inequalities": peak,
            }

    return False, {
        "variables_eliminated": eliminated,
        "peak_inequalities": peak,
    }
