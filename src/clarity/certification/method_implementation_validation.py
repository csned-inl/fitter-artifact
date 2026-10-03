"""One-time semantic conformance checks for proof-method implementations.

These checks use the reviewed model fixtures only as expression/type harnesses.
They are not part of ordinary model certification and are run by the combined
method validator when the logic or checker implementation changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from clarity.sysml.parser import BinaryExpr, LiteralExpr, RefExpr, UnaryExpr

from .constraint_logic import (
    LogicSequent,
    apply,
    compile_expression,
    literal,
    lower_to_z3,
    sort_from_sysml,
    symbol,
)
from .discretization_semantic_validation import (
    compile_structural_discretization_logic,
)
from .markov_logic import build_markov_logic_query
from .ot_markov import compile_ot_model
from .structural_discretization import _unit_farkas_contradiction
from .structural_markov import _clause_contradiction, _dnf


@dataclass(frozen=True, slots=True)
class ImplementationValidationResult:
    name: str
    result: str
    cases: int
    expected: str = "unsat"
    reason: str | None = None


def _models() -> tuple[Path, ...]:
    root = Path(__file__).resolve().parents[3]
    return (
        root / "src/clarity/models/thermostat/model.sysml",
        root / "src/clarity/models/mixing-sysml-model/model.sysml",
        root / "tests/fixtures/standalone/cruise-controller-model.sysml",
    )


def _environment(model):
    return {
        field.name: symbol(
            "method-check::obs::" + field.name,
            sort_from_sysml(field.type_name),
        )
        for field in model.observation
    }


def _compile(model, expression):
    return compile_expression(
        model, expression, observation=_environment(model), action={},
        prior_action={}, scenario={}, context=model.controller_fqn,
    )


def _from_dnf(clauses):
    if not clauses:
        return LiteralExpr(False)
    disjuncts = []
    for clause in clauses:
        if not clause:
            disjuncts.append(LiteralExpr(True))
            continue
        atoms = [UnaryExpr("not", item) if negate else item
                 for item, negate in clause]
        value = atoms[0]
        for atom in atoms[1:]:
            value = BinaryExpr("and", value, atom)
        disjuncts.append(value)
    result = disjuncts[0]
    for disjunct in disjuncts[1:]:
        result = BinaryExpr("or", result, disjunct)
    return result


def _unsat(z3, sequent: LogicSequent, timeout_ms: int) -> bool:
    solver = z3.Solver()
    solver.set(timeout=timeout_ms)
    solver.add(lower_to_z3(sequent.counterexample(), z3))
    return str(solver.check()) == "unsat"


def validate_method_implementations(
    *, timeout_ms: int = 5_000,
) -> dict[str, object]:
    """Compare accepted checker transformations with their logical meaning."""

    try:
        import z3
    except ImportError as exc:  # pragma: no cover - runner dependency
        return {
            "classification": "NOT_VALIDATED",
            "reason": "solver_unavailable",
            "detail": str(exc),
        }

    paths = _models()
    thermostat = compile_ot_model(paths[0])
    temperature = RefExpr([thermostat.policy_subject, "temperatureCelcius"])
    set_point = RefExpr([thermostat.policy_subject, "setPoint"])
    atoms = (
        BinaryExpr("<", temperature, LiteralExpr(0.0)),
        BinaryExpr(">=", temperature, set_point),
        BinaryExpr("<=", set_point, LiteralExpr(25.0)),
    )
    formulas = (
        BinaryExpr("implies", atoms[0], BinaryExpr("or", atoms[1], atoms[2])),
        UnaryExpr("not", BinaryExpr("and", atoms[0], atoms[1])),
        BinaryExpr(
            "and", BinaryExpr("or", atoms[0], atoms[1]),
            BinaryExpr("or", UnaryExpr("not", atoms[0]), atoms[2]),
        ),
    )
    dnf_ok = True
    for formula in formulas:
        clauses = _dnf(formula)
        if clauses is None:
            dnf_ok = False
            break
        rebuilt = _from_dnf(clauses)
        dnf_ok = dnf_ok and _unsat(z3, LogicSequent((), apply(
            "eq", _compile(thermostat, formula), _compile(thermostat, rebuilt)
        )), timeout_ms)

    bound_atoms = [
        BinaryExpr(operation, temperature, LiteralExpr(float(threshold)))
        for operation in (">", ">=", "<", "<=", "==")
        for threshold in (-1, 0, 1)
    ]
    interval_cases = 0
    interval_ok = True
    for index, left in enumerate(bound_atoms):
        for right in bound_atoms[index + 1:]:
            clause = ((left, False), (right, False))
            if not _clause_contradiction(
                thermostat, clause, thermostat.controller_fqn
            ):
                continue
            interval_cases += 1
            interval_ok = interval_ok and _unsat(
                z3,
                LogicSequent(
                    (_compile(thermostat, left), _compile(thermostat, right)),
                    literal(False),
                ),
                timeout_ms,
            )

    x = temperature
    y = set_point
    neg_x = BinaryExpr("*", LiteralExpr(-1), x)
    neg_y = BinaryExpr("*", LiteralExpr(-1), y)
    farkas_clauses = (
        (
            (BinaryExpr("<=", x, LiteralExpr(0)), False),
            (BinaryExpr("<", neg_x, LiteralExpr(0)), False),
        ),
        (
            (BinaryExpr("<=", BinaryExpr("+", x, y), LiteralExpr(1)), False),
            (BinaryExpr("<=", neg_x, LiteralExpr(-1)), False),
            (BinaryExpr("<", neg_y, LiteralExpr(0)), False),
        ),
    )
    farkas_ok = True
    for clause in farkas_clauses:
        accepted = _unit_farkas_contradiction(
            thermostat, clause, thermostat.controller_fqn
        )
        sequent = LogicSequent(
            tuple(_compile(thermostat, predicate) for predicate, _negate in clause),
            literal(False),
        )
        farkas_ok = farkas_ok and accepted and _unsat(z3, sequent, timeout_ms)

    safety_cases = 0
    safety_ok = True
    markov_cases = 0
    markov_ok = True
    for path in paths:
        _model, safety = compile_structural_discretization_logic(path)
        safety_cases += len(safety)
        safety_ok = safety_ok and all(
            _unsat(z3, item.sequent, timeout_ms) for item in safety
        )
        markov = build_markov_logic_query(compile_ot_model(path))
        markov_cases += len(markov.sequents())
        markov_ok = markov_ok and all(
            _unsat(z3, item, timeout_ms) for item in markov.sequents().values()
        )

    broken_x = symbol("negative_control_x", sort_from_sysml("Real"))
    broken_low = symbol("negative_control_low", sort_from_sysml("Real"))
    broken_high = symbol("negative_control_high", sort_from_sysml("Real"))
    broken = LogicSequent((
        apply("ge", broken_x, broken_low),
        apply("le", broken_x, broken_high),
    ), literal(False))
    broken_solver = z3.Solver()
    broken_solver.set(timeout=timeout_ms)
    broken_solver.add(lower_to_z3(broken.counterexample(), z3))
    negative_control = str(broken_solver.check())

    results = (
        ImplementationValidationResult("boolean-dnf-implementation", "unsat" if dnf_ok else "failed", len(formulas)),
        ImplementationValidationResult("interval-contradiction-implementation", "unsat" if interval_ok and interval_cases else "failed", interval_cases),
        ImplementationValidationResult("unit-farkas-implementation", "unsat" if farkas_ok else "failed", len(farkas_clauses)),
        ImplementationValidationResult("safety-logic-instances", "unsat" if safety_ok else "failed", safety_cases),
        ImplementationValidationResult("markov-logic-instances", "unsat" if markov_ok else "failed", markov_cases),
        ImplementationValidationResult(
            "weakened-interval-negative-control", negative_control, 1, expected="sat"
        ),
    )
    passed = all(item.result == item.expected for item in results)
    return {
        "classification": "VALIDATED" if passed else "NOT_VALIDATED",
        "checks": tuple(asdict(item) for item in results),
    }


__all__ = ["ImplementationValidationResult", "validate_method_implementations"]
