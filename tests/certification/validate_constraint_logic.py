#!/usr/bin/env python3
"""Tests for the explicit logic meaning and structural proof-rule validation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.sysml.parser import (  # noqa: E402
    BinaryExpr,
    LiteralExpr,
    RefExpr,
    UnaryExpr,
)
from clarity.certification.constraint_logic import (  # noqa: E402
    LOGIC_PROFILE,
    LogicSequent,
    LogicSort,
    apply,
    compile_expression,
    conjunction,
    literal,
    lower_to_z3,
    symbol,
)
from clarity.certification.discretization_semantic_validation import (  # noqa: E402
    compile_structural_discretization_logic,
)
from clarity.certification.ot_markov import compile_ot_model  # noqa: E402
from clarity.certification.structural_discretization import (  # noqa: E402
    _unit_farkas_contradiction,
)
from clarity.certification.structural_markov import (  # noqa: E402
    _clause_contradiction,
    _dnf,
)
from clarity.certification.structural_rule_validation import (  # noqa: E402
    structural_rule_schemas,
    validate_structural_rule_schemas,
)


MODELS = (
    ROOT / "src/clarity/models/thermostat/model.sysml",
    ROOT / "src/clarity/models/mixing-sysml-model/model.sysml",
    ROOT / "tests/fixtures/standalone/cruise-controller-model.sysml",
)
HAS_Z3 = importlib.util.find_spec("z3") is not None


def _logic_environment(model):
    observation = {
        field.name: symbol("test::obs::" + field.name, LogicSort.REAL)
        for field in model.observation
    }
    return observation


def _compile_formula(model, expression):
    return compile_expression(
        model,
        expression,
        observation=_logic_environment(model),
        action={},
        prior_action={},
        scenario={},
        context=model.controller_fqn,
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


class ConstraintLogicTests(unittest.TestCase):
    def test_logic_ir_is_typed_and_rejects_invalid_terms(self):
        self.assertEqual(LOGIC_PROFILE, "ot-constraint-logic-0.1")
        with self.assertRaises(ValueError):
            apply(
                "and",
                symbol("numeric", LogicSort.REAL),
                symbol("boolean", LogicSort.BOOL),
            )
        with self.assertRaises(ValueError):
            LogicSequent((), symbol("not_a_formula", LogicSort.REAL))

    def test_all_current_safety_obligations_have_explicit_logic(self):
        counts = []
        fingerprints = set()
        for path in MODELS:
            model, obligations = compile_structural_discretization_logic(path)
            counts.append(len(obligations))
            self.assertEqual(model.source_sha256, compile_ot_model(path).source_sha256)
            for obligation in obligations:
                self.assertEqual(obligation.sequent.profile, LOGIC_PROFILE)
                fingerprint = obligation.sequent.fingerprint()
                self.assertEqual(len(fingerprint), 64)
                fingerprints.add(fingerprint)
        self.assertEqual(counts, [3, 4, 5])
        self.assertEqual(len(fingerprints), 12)

    def test_rule_schema_inventory_covers_boolean_bounds_and_farkas(self):
        names = {schema.name for schema in structural_rule_schemas()}
        self.assertIn("implication-counterexample-equivalence", names)
        self.assertIn("inconsistent-closed-interval", names)
        self.assertIn("unit-farkas-strict-width-4", names)
        self.assertEqual(len(names), len(structural_rule_schemas()))

    @unittest.skipUnless(HAS_Z3, "pinned z3-solver unavailable")
    def test_z3_refutes_every_rule_schema_counterexample(self):
        result = validate_structural_rule_schemas()
        self.assertEqual(result["classification"], "VALIDATED", result)
        self.assertTrue(result["rules"])
        self.assertTrue(all(item["result"] == "unsat" for item in result["rules"]))

    @unittest.skipUnless(HAS_Z3, "pinned z3-solver unavailable")
    def test_rule_validation_is_not_vacuous(self):
        import z3

        x = symbol("broken_x", LogicSort.REAL)
        low = symbol("broken_low", LogicSort.REAL)
        high = symbol("broken_high", LogicSort.REAL)
        broken = LogicSequent((
            apply("ge", x, low),
            apply("le", x, high),
        ), literal(False))
        solver = z3.Solver()
        solver.add(lower_to_z3(broken.counterexample(), z3))
        self.assertEqual(str(solver.check()), "sat")

    @unittest.skipUnless(HAS_Z3, "pinned z3-solver unavailable")
    def test_dnf_implementation_preserves_logical_meaning(self):
        import z3

        model = compile_ot_model(MODELS[0])
        temperature = RefExpr([model.policy_subject, "temperatureCelcius"])
        set_point = RefExpr([model.policy_subject, "setPoint"])
        atoms = (
            BinaryExpr("<", temperature, LiteralExpr(0.0)),
            BinaryExpr(">=", temperature, set_point),
            BinaryExpr("<=", set_point, LiteralExpr(25.0)),
        )
        formulas = (
            BinaryExpr("implies", atoms[0], BinaryExpr("or", atoms[1], atoms[2])),
            UnaryExpr("not", BinaryExpr("and", atoms[0], atoms[1])),
            BinaryExpr(
                "and",
                BinaryExpr("or", atoms[0], atoms[1]),
                BinaryExpr("or", UnaryExpr("not", atoms[0]), atoms[2]),
            ),
        )
        for formula in formulas:
            with self.subTest(formula=repr(formula)):
                clauses = _dnf(formula)
                self.assertIsNotNone(clauses)
                rebuilt = _from_dnf(clauses)
                equivalent = LogicSequent((), apply(
                    "eq", _compile_formula(model, formula),
                    _compile_formula(model, rebuilt),
                ))
                solver = z3.Solver()
                solver.add(lower_to_z3(equivalent.counterexample(), z3))
                self.assertEqual(str(solver.check()), "unsat")

    @unittest.skipUnless(HAS_Z3, "pinned z3-solver unavailable")
    def test_every_accepted_interval_contradiction_is_semantically_impossible(self):
        import z3

        model = compile_ot_model(MODELS[0])
        variable = RefExpr([model.policy_subject, "temperatureCelcius"])
        accepted = 0
        atoms = [
            BinaryExpr(operation, variable, LiteralExpr(float(threshold)))
            for operation in (">", ">=", "<", "<=", "==")
            for threshold in (-1, 0, 1)
        ]
        for index, left in enumerate(atoms):
            for right in atoms[index + 1:]:
                clause = ((left, False), (right, False))
                if not _clause_contradiction(model, clause, model.controller_fqn):
                    continue
                accepted += 1
                sequent = LogicSequent(
                    (_compile_formula(model, left), _compile_formula(model, right)),
                    literal(False),
                )
                solver = z3.Solver()
                solver.add(lower_to_z3(sequent.counterexample(), z3))
                self.assertEqual(str(solver.check()), "unsat", clause)
        self.assertGreater(accepted, 0)

    @unittest.skipUnless(HAS_Z3, "pinned z3-solver unavailable")
    def test_accepted_unit_farkas_applications_are_semantically_impossible(self):
        import z3

        model = compile_ot_model(MODELS[0])
        x = RefExpr([model.policy_subject, "temperatureCelcius"])
        y = RefExpr([model.policy_subject, "setPoint"])
        neg_x = BinaryExpr("*", LiteralExpr(-1), x)
        neg_y = BinaryExpr("*", LiteralExpr(-1), y)
        clauses = (
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
        for clause in clauses:
            self.assertTrue(
                _unit_farkas_contradiction(model, clause, model.controller_fqn)
            )
            premises = tuple(_compile_formula(model, predicate)
                             for predicate, _negate in clause)
            sequent = LogicSequent(premises, literal(False))
            solver = z3.Solver()
            solver.add(lower_to_z3(sequent.counterexample(), z3))
            self.assertEqual(str(solver.check()), "unsat")


if __name__ == "__main__":
    unittest.main()
