"""Canonical fixed-discretization timing records."""

from __future__ import annotations

from fractions import Fraction
from typing import Any


def canonical_dt(text: str | float) -> dict[str, Any]:
    """Return one exact, positive representation of a fixed time step."""

    raw = str(text).strip()
    value = Fraction(raw)
    if value <= 0:
        raise ValueError("dt must be positive")
    return {
        "input": raw,
        "numerator": value.numerator,
        "denominator": value.denominator,
        "canonical": f"{value.numerator}/{value.denominator}",
        "decimal": float(value),
    }
