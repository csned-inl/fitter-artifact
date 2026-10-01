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
from clarity.certification.markov_z3 import SOLVER_PIPELINE
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
                structural = set(query.structural_discharges)
                self.assertEqual(names | structural, expected)
                self.assertFalse(names & structural)
                self.assertEqual(len(query.differences), 15)
                self.assertEqual(structural, {
                    "action_availability_mask", "next_buffer_shift",
                })
                self.assertTrue(query.counterexample_assertion.startswith("(or "))

    def test_structural_discharges_have_exact_definitions(self):
        for case, query in self.queries.items():
            with self.subTest(case=case):
                for side in (query.left, query.right):
                    mask = side.candidate.interface.visible(
                        "action_availability_mask"
                    )
                    self.assertEqual((mask.text, mask.availability), ("15", "true"))
                    self.assertTrue(all(
                        slot.source.startswith("interface:next_observation.")
                        or slot.source.startswith("current-slot:")
                        or slot.source == "interface:executed_action_one_hot"
                        for slot in side.candidate.next_slots
                    ))

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

    def test_incremental_query_guards_each_complete_difference_once(self):
        for case, query in self.queries.items():
            with self.subTest(case=case):
                incremental = query.incremental_smt2()
                selectors = query.difference_selectors
                self.assertEqual(len(selectors), len(query.differences))
                self.assertEqual(len(selectors), len(set(selectors)))
                self.assertEqual(
                    incremental.count("(declare-const ")
                    - query.base_smt2().count("(declare-const "),
                    len(selectors),
                )
                for selector, difference in zip(selectors, query.differences):
                    self.assertIn(
                        f"(assert (=> {selector} {difference.assertion}))",
                        incremental,
                    )
                self.assertNotIn(
                    f"(assert {query.counterexample_assertion})", incremental,
                )

    @unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
    def test_z3_finds_no_paired_markov_counterexample(self):
        import z3

        for case, query in self.queries.items():
            with self.subTest(case=case):
                expressions = z3.parse_smt2_string(query.incremental_smt2())
                solver = z3.Then(*(
                    z3.Tactic(name) for name in SOLVER_PIPELINE
                )).solver()
                solver.set(timeout=30_000)
                solver.add(*list(expressions))
                base = solver.check()
                self.assertEqual(
                    base, z3.sat,
                    f"{case}: paired base must be SAT, found {base}; "
                    f"reason={solver.reason_unknown()}",
                )
                for selector, difference in zip(
                    query.difference_selectors, query.differences,
                ):
                    answer = solver.check(z3.Bool(selector))
                    self.assertEqual(
                        answer, z3.unsat,
                        f"{case}/{difference.name}: expected unsat, found "
                        f"{answer}; reason={solver.reason_unknown()}; "
                        f"model_retained={answer == z3.sat}",
                    )


if __name__ == "__main__":
    unittest.main()
