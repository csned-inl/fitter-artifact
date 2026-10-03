#!/usr/bin/env python3
"""Tests for the explicit logic meaning and structural proof-rule validation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.constraint_logic import (  # noqa: E402
    LOGIC_PROFILE,
    LogicSequent,
    LogicSort,
    apply,
    literal,
    lower_to_z3,
    symbol,
)
from clarity.certification.discretization_semantic_validation import (  # noqa: E402
    compile_structural_discretization_logic,
)
from clarity.certification.markov_logic import (  # noqa: E402
    build_markov_logic_query,
)
from clarity.certification.markov_rule_validation import (  # noqa: E402
    markov_rule_schemas,
)
from clarity.certification.ot_markov import compile_ot_model  # noqa: E402
from clarity.certification.structural_rule_validation import (  # noqa: E402
    structural_rule_schemas,
)


MODELS = (
    ROOT / "src/clarity/models/thermostat/model.sysml",
    ROOT / "src/clarity/models/mixing-sysml-model/model.sysml",
    ROOT / "tests/fixtures/standalone/cruise-controller-model.sysml",
)
HAS_Z3 = importlib.util.find_spec("z3") is not None

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

    def test_all_current_markov_obligations_have_explicit_logic(self):
        for path in MODELS:
            query = build_markov_logic_query(compile_ot_model(path))
            self.assertEqual(
                set(query.sequents()),
                {"initialization", "shield_totality", "shield_uniqueness", "markov"},
            )
            self.assertTrue(all(
                len(sequent.fingerprint()) == 64
                for sequent in query.sequents().values()
            ))

    def test_markov_rule_schema_inventory_covers_factorization(self):
        names = {schema.name for schema in markov_rule_schemas()}
        self.assertIn("keep-or-replace-execution-is-congruent", names)
        self.assertIn("successor-function-congruence", names)
        self.assertIn("paired-result-factorization", names)

    def test_model_entry_points_do_not_invoke_method_validation(self):
        paths = (
            ROOT / "scripts/certify_discretization.py",
            ROOT / "scripts/prove_markov.py",
            ROOT / "src/clarity/certification/ot_markov.py",
            ROOT / "src/clarity/certification/structural_markov.py",
        )
        for path in paths:
            with self.subTest(path=path.name):
                self.assertNotIn("validate_symbolic_proof_methods", path.read_text())

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

if __name__ == "__main__":
    unittest.main()
