#!/usr/bin/env python3
"""Thermostat-only paired buffered-Markov prototype checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_extract import extract_thermostat_markov_ir
from clarity.certification.markov_interval import derive_thermostat_decision_interval
from clarity.certification.markov_slice import build_thermostat_relevance_slice
from clarity.certification.markov_z3 import SolverStatus, run_smt2_query
from clarity.certification.markov_z3_pair import compile_thermostat_paired_query


HAS_Z3 = importlib.util.find_spec("z3") is not None
CASES = ("reset_prefix_0", "reset_prefix_1", "steady_state")


class ThermostatPairedPrototypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir = extract_thermostat_markov_ir()
        cls.interval = derive_thermostat_decision_interval(cls.ir)
        cls.slice = build_thermostat_relevance_slice(cls.ir, cls.interval)
        cls.queries = {
            case: compile_thermostat_paired_query(
                cls.slice, cls.ir, cls.interval,
                history_case=case, namespace="prototype-" + case,
            )
            for case in CASES
        }

    def test_current_buffer_and_proposal_are_the_only_paired_inputs(self):
        for case, query in self.queries.items():
            with self.subTest(case=case):
                self.assertEqual(len(query.current_equalities), 11)
                joined = "\n".join(query.current_equalities)
                self.assertEqual(joined.count("fp.to_ieee_bv"), 20)
                self.assertIn("slice:policy_proposal", joined)
                self.assertNotIn("semantic:physical_temperature", joined)

    def test_one_aggregate_root_covers_every_visible_result(self):
        expected = {
            term.name for term in self.slice.visible_terms
        }
        for case, query in self.queries.items():
            with self.subTest(case=case):
                names = {item.name for item in query.differences}
                self.assertEqual(names, expected)
                self.assertEqual(len(query.differences), 17)
                self.assertTrue(query.counterexample_assertion.startswith("(or "))

    def test_left_and_right_declarations_are_disjoint(self):
        for case, query in self.queries.items():
            with self.subTest(case=case):
                left = set(query.left.declarations)
                right = set(query.right.declarations)
                self.assertFalse(left.intersection(right))
                smt2 = query.smt2()
                self.assertNotIn("(check-sat", smt2)
                self.assertEqual(
                    smt2.count("(assert "),
                    len(query.left.assertions) + len(query.right.assertions)
                    + len(query.current_equalities) + 1,
                )

    @unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
    def test_z3_finds_no_paired_markov_counterexample(self):
        for case, query in self.queries.items():
            with self.subTest(case=case):
                result = run_smt2_query(query.smt2(), timeout_ms=90_000)
                self.assertIs(
                    result.status,
                    SolverStatus.UNSAT,
                    f"{case}: status={result.status.value}; "
                    f"query={result.query_sha256}; reason={result.reason}; "
                    f"model_retained={result.model_smt2 is not None}",
                )


if __name__ == "__main__":
    unittest.main()
