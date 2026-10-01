#!/usr/bin/env python3
"""Thermostat-only paired buffered-Markov prototype checks."""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_extract import extract_thermostat_markov_ir
from clarity.certification.markov_interval import derive_thermostat_decision_interval
from clarity.certification.markov_slice import build_thermostat_relevance_slice
from clarity.certification.markov_z3 import SOLVER_PIPELINE, UnsupportedLoweringError
from clarity.certification.markov_z3_pair import (
    _validate_alpha_copies,
    compile_executed_action_alias_witness,
    compile_executed_action_overapproximation,
    compile_thermostat_paired_query,
)


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

    def test_fixed_mdp_parameters_are_shared_separately(self):
        expected = {
            "semantic:outside_temperature",
            "semantic:set_point",
            "semantic:tolerance",
            "slice:configured_dt",
        }
        for case, query in self.queries.items():
            with self.subTest(case=case):
                self.assertEqual(len(query.fixed_parameter_equalities), 4)
                joined = "\n".join(query.fixed_parameter_equalities)
                self.assertEqual(joined.count("fp.to_ieee_bv"), 8)
                for uid in expected:
                    self.assertIn(uid, joined)
                self.assertNotIn("slice:policy_proposal", joined)

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
                    + len(query.current_equalities)
                    + len(query.fixed_parameter_equalities) + 1,
                )

    def test_paired_bases_are_exact_namespace_renamed_copies(self):
        for case, query in self.queries.items():
            with self.subTest(case=case):
                left_prefix = query.namespace + "::left"
                right_prefix = query.namespace + "::right"

                def rename(items):
                    return tuple(
                        item.replace(left_prefix, right_prefix) for item in items
                    )

                self.assertEqual(
                    rename(query.left.declarations), query.right.declarations,
                )
                self.assertEqual(
                    rename(query.left.definitions), query.right.definitions,
                )
                self.assertEqual(
                    rename(query.left.assertions), query.right.assertions,
                )

    def test_alpha_copy_check_fails_closed_on_a_changed_assertion(self):
        query = self.queries["steady_state"]
        changed_right = replace(
            query.right,
            step_assertions=query.right.step_assertions[:-1],
        )
        with self.assertRaises(UnsupportedLoweringError):
            _validate_alpha_copies(
                query.left,
                changed_right,
                left_prefix=query.namespace + "::left",
                right_prefix=query.namespace + "::right",
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

    def test_executed_action_query_is_a_strict_source_premise_subset(self):
        for case, query in self.queries.items():
            with self.subTest(case=case):
                target = compile_executed_action_overapproximation(query)
                full = set(
                    query.left.assertions + query.right.assertions
                    + query.current_equalities
                    + query.fixed_parameter_equalities
                )
                self.assertEqual(target.name, "executed_action")
                self.assertTrue(set(target.premise_assertions).issubset(full))
                self.assertTrue(set(target.declarations).issubset(
                    query.left.declarations + query.right.declarations
                ))
                self.assertTrue(set(target.definitions).issubset(
                    query.left.definitions + query.right.definitions
                ))
                self.assertEqual(target.source_assertion_count, len(full))
                self.assertEqual(
                    target.difference_assertion,
                    next(item.assertion for item in query.differences
                         if item.name == "executed_action"),
                )
                self.assertGreater(target.omitted_assertion_count, 0)
                self.assertLess(len(target.smt2().encode()), 20_000)
                self.assertNotIn("::flow::control-state::", target.smt2())
                self.assertNotIn("guarded", target.smt2())

    def test_executed_action_query_fails_closed_without_shared_tolerance(self):
        query = self.queries["steady_state"]
        mutated = replace(
            query,
            fixed_parameter_equalities=tuple(
                item for item in query.fixed_parameter_equalities
                if "semantic:tolerance" not in item
            ),
        )
        with self.assertRaises(UnsupportedLoweringError):
            compile_executed_action_overapproximation(mutated)

    def test_executed_action_query_rejects_a_weakened_difference(self):
        query = self.queries["steady_state"]
        differences = tuple(
            replace(item, assertion="false")
            if item.name == "executed_action" else item
            for item in query.differences
        )
        with self.assertRaises(UnsupportedLoweringError):
            compile_executed_action_overapproximation(
                replace(query, differences=differences)
            )

    def test_executed_action_alias_witness_uses_exact_checked_bits(self):
        query = self.queries["reset_prefix_0"]
        witness = compile_executed_action_alias_witness(query)
        joined = "\n".join(witness.assertions)
        self.assertEqual(len(witness.assertions), 22)
        self.assertIn("#x4035000000000000", witness.left_temperature)
        self.assertIn("#x4035000000000001", witness.right_temperature)
        self.assertEqual(witness.normalized_temperature_bits, "#x3f60f1d2")
        self.assertIn("action_1", joined)
        self.assertIn("action_0", joined)
        self.assertEqual(joined.count("#x3f60f1d2"), 2)
        self.assertIn("executed_action", query.target_smt2(
            "executed_action", *witness.assertions,
        ))
        with self.assertRaises(UnsupportedLoweringError):
            compile_executed_action_alias_witness(
                self.queries["steady_state"]
            )

    @unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
    def test_z3_replays_executed_action_alias_before_proof(self):
        import z3

        query = self.queries["reset_prefix_0"]
        target = compile_executed_action_overapproximation(query)
        witness = compile_executed_action_alias_witness(query)

        reduced_solver = z3.Then(*(
            z3.Tactic(name) for name in SOLVER_PIPELINE
        )).solver()
        reduced_solver.set(timeout=5_000)
        reduced_solver.add(*list(z3.parse_smt2_string(
            target.smt2(*witness.assertions)
        )))
        reduced_answer = reduced_solver.check()
        self.assertEqual(
            reduced_answer, z3.sat,
            "exact alias witness was not accepted by the reduced cone: "
            f"{reduced_answer}; reason={reduced_solver.reason_unknown()}",
        )

        full_solver = z3.Then(*(
            z3.Tactic(name) for name in SOLVER_PIPELINE
        )).solver()
        full_solver.set(timeout=30_000)
        full_solver.add(*list(z3.parse_smt2_string(query.target_smt2(
            "executed_action", *witness.assertions,
        ))))
        full_answer = full_solver.check()
        if full_answer == z3.sat:
            self.fail(
                "confirmed full reset_prefix_0 executed_action counterexample: "
                "Float64 temperatures #x4035000000000000 and "
                "#x4035000000000001 both normalize to Float32 #x3f60f1d2 "
                "but select action_1 and action_0"
            )
        if full_answer == z3.unknown:
            self.fail(
                "full reset_prefix_0 alias replay was inconclusive: "
                + full_solver.reason_unknown()
            )

        unconstrained_solver = z3.Then(*(
            z3.Tactic(name) for name in SOLVER_PIPELINE
        )).solver()
        unconstrained_solver.set(timeout=30_000)
        unconstrained_solver.add(*list(z3.parse_smt2_string(target.smt2())))
        answer = unconstrained_solver.check()
        self.assertEqual(
            answer, z3.unsat,
            "the concrete alias was rejected by full replay, but the sound "
            f"over-approximation still returned {answer}; "
            f"reason={unconstrained_solver.reason_unknown()}",
        )


if __name__ == "__main__":
    unittest.main()
