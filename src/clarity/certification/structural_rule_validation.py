"""Semantic validation of the structural checker's proof-rule schemas.

The production checker is intentionally solver-free.  This module states the
logical implications on which its positive rules rely and asks an independent
SMT backend to find a counterexample.  A rule is validated only when the
counterexample is UNSAT; UNKNOWN and timeout never count as success.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .constraint_logic import (
    LOGIC_PROFILE,
    LogicSequent,
    LogicSort,
    LogicTerm,
    apply,
    literal,
    lower_to_z3,
    symbol,
)


RULE_PROFILE = "structural-proof-rules-0.1"


@dataclass(frozen=True, slots=True)
class RuleSchema:
    name: str
    explanation: str
    sequent: LogicSequent


@dataclass(frozen=True, slots=True)
class RuleValidationResult:
    name: str
    result: str
    logic_fingerprint: str
    smt2_bytes: int
    reason_unknown: str | None


def _sum(values: tuple[LogicTerm, ...]) -> LogicTerm:
    if not values:
        return literal(0)
    result = values[0]
    for value in values[1:]:
        result = apply("add", result, value)
    return result


def _farkas_schema(width: int, *, strict: bool) -> RuleSchema:
    rows = tuple(symbol(f"f{width}_row_{index}", LogicSort.REAL)
                 for index in range(width))
    bounds = tuple(symbol(f"f{width}_bound_{index}", LogicSort.REAL)
                   for index in range(width))
    row_bounds = tuple(
        apply("lt" if strict and index == 0 else "le", row, bound)
        for index, (row, bound) in enumerate(zip(rows, bounds))
    )
    premises = row_bounds + (
        apply("eq", _sum(rows), literal(0)),
        apply("le" if strict else "lt", _sum(bounds), literal(0)),
    )
    kind = "strict" if strict else "negative-sum"
    return RuleSchema(
        name=f"unit-farkas-{kind}-width-{width}",
        explanation=(
            "adding individually valid affine bounds whose left sides cancel "
            "cannot produce a negative bound, or a nonpositive bound when one "
            "source row is strict"
        ),
        sequent=LogicSequent(premises, literal(False)),
    )


def structural_rule_schemas() -> tuple[RuleSchema, ...]:
    """Return the solver-independent semantic claims used by positive rules."""

    p = symbol("bool_p", LogicSort.BOOL)
    q = symbol("bool_q", LogicSort.BOOL)
    guard = symbol("bool_guard", LogicSort.BOOL)
    x = symbol("interval_x", LogicSort.REAL)
    low = symbol("interval_low", LogicSort.REAL)
    high = symbol("interval_high", LogicSort.REAL)
    replacement = symbol("substitution_replacement", LogicSort.REAL)
    original = symbol("substitution_original", LogicSort.REAL)
    threshold = symbol("substitution_threshold", LogicSort.REAL)

    schemas = [
        RuleSchema(
            "implication-counterexample-equivalence",
            "P implies Q is equivalent to the absence of P and not Q",
            LogicSequent((), apply(
                "eq",
                apply("implies", p, q),
                apply("not", apply("and", p, apply("not", q))),
            )),
        ),
        RuleSchema(
            "proved-consequent-discharges-guard",
            "a proved consequent establishes any implication with that consequent",
            LogicSequent((q,), apply("implies", guard, q)),
        ),
        RuleSchema(
            "conjunction-introduction",
            "two proved conjuncts establish their conjunction",
            LogicSequent((p, q), apply("and", p, q)),
        ),
        RuleSchema(
            "equality-substitution-in-affine-predicate",
            "equal numeric terms are interchangeable in a comparison",
            LogicSequent(
                (apply("eq", original, replacement),),
                apply(
                    "eq",
                    apply("le", original, threshold),
                    apply("le", replacement, threshold),
                ),
            ),
        ),
        RuleSchema(
            "inconsistent-closed-interval",
            "a lower bound above an upper bound is contradictory",
            LogicSequent((
                apply("gt", low, high),
                apply("ge", x, low),
                apply("le", x, high),
            ), literal(False)),
        ),
        RuleSchema(
            "inconsistent-strict-lower-bound",
            "a strict lower bound at or above an upper bound is contradictory",
            LogicSequent((
                apply("ge", low, high),
                apply("gt", x, low),
                apply("le", x, high),
            ), literal(False)),
        ),
        RuleSchema(
            "inconsistent-strict-upper-bound",
            "a lower bound at or above a strict upper bound is contradictory",
            LogicSequent((
                apply("ge", low, high),
                apply("ge", x, low),
                apply("lt", x, high),
            ), literal(False)),
        ),
    ]
    schemas.extend(
        _farkas_schema(width, strict=strict)
        for width in (2, 3, 4)
        for strict in (False, True)
    )
    return tuple(schemas)


def validate_structural_rule_schemas(
    *, timeout_ms: int = 5_000,
) -> dict[str, object]:
    """Ask Z3 to refute a counterexample to every proof-rule schema."""

    try:
        import z3
    except ImportError as exc:  # pragma: no cover - runner dependency
        return {
            "classification": "NOT_VALIDATED",
            "profile": RULE_PROFILE,
            "logic_profile": LOGIC_PROFILE,
            "reason": "solver_unavailable",
            "detail": str(exc),
        }

    results: list[RuleValidationResult] = []
    for schema in structural_rule_schemas():
        solver = z3.Solver()
        solver.set(timeout=timeout_ms)
        solver.add(lower_to_z3(schema.sequent.counterexample(), z3))
        smt2_bytes = len(solver.to_smt2().encode("utf-8"))
        outcome = solver.check()
        result = str(outcome)
        results.append(RuleValidationResult(
            name=schema.name,
            result=result,
            logic_fingerprint=schema.sequent.fingerprint(),
            smt2_bytes=smt2_bytes,
            reason_unknown=(solver.reason_unknown() if result == "unknown" else None),
        ))
    passed = bool(results) and all(item.result == "unsat" for item in results)
    return {
        "classification": "VALIDATED" if passed else "NOT_VALIDATED",
        "profile": RULE_PROFILE,
        "logic_profile": LOGIC_PROFILE,
        "meaning": (
            "no semantic counterexample exists for any stated structural "
            "proof-rule schema in the typed constraint logic"
        ),
        "rules": tuple(asdict(item) for item in results),
    }


__all__ = [
    "RULE_PROFILE",
    "RuleSchema",
    "RuleValidationResult",
    "structural_rule_schemas",
    "validate_structural_rule_schemas",
]
