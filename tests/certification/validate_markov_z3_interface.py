#!/usr/bin/env python3
"""Sparse thermostat controller-interface lowering and mutation checks."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_extract import extract_thermostat_markov_ir
from clarity.certification.markov_interval import derive_thermostat_decision_interval
from clarity.certification.markov_native_semantics import load_native_semantics_contract
from clarity.certification.markov_slice import build_thermostat_relevance_slice
from clarity.certification.markov_z3 import (
    SolverStatus, UnsupportedLoweringError, run_smt2_query,
)
from clarity.certification.markov_z3_expr import fp_literal
from clarity.certification.markov_z3_interface import compile_thermostat_interface
from clarity.certification.markov_z3_transition import compile_transition_relation
from clarity.certification.markov_ir import NativeSort


HAS_Z3 = importlib.util.find_spec("z3") is not None


class ThermostatInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir = extract_thermostat_markov_ir()
        cls.interval = derive_thermostat_decision_interval(cls.ir)
        cls.slice = build_thermostat_relevance_slice(cls.ir, cls.interval)
        cls.transition = compile_transition_relation(
            cls.slice, cls.ir, cls.interval, namespace="fixture"
        )
        cls.encoding = compile_thermostat_interface(
            cls.slice, cls.ir, cls.interval, cls.transition,
            namespace="fixture-interface",
        )

    def test_visible_inventory_is_exact_and_structural_shift_stays_separate(self):
        expected = [
            term.name for term in self.slice.visible_terms
            if term.name != "next_buffer_shift"
        ]
        self.assertEqual([term.name for term in self.encoding.visible_terms], expected)
        self.assertEqual(self.encoding.structural_terms, ("next_buffer_shift",))
        self.assertEqual(len(self.encoding.visible_terms), 16)
        self.assertEqual(
            self.encoding.visible("outcome_constructor").declared_type,
            "OutcomeConstructor",
        )
        self.assertEqual(
            self.encoding.visible("execution_error").declared_type,
            "ExecutionError",
        )

    def test_interface_adds_definitions_but_no_duplicate_state(self):
        self.assertEqual(self.encoding.declarations, self.transition.declarations)
        self.assertEqual(len(self.encoding.declarations), 635)
        self.assertEqual(len(self.encoding.declarations),
                         len(set(self.encoding.declarations)))
        self.assertEqual(len(self.encoding.definitions), 25)
        self.assertEqual(len(self.encoding.definitions),
                         len(set(self.encoding.definitions)))
        self.assertTrue(all(item.startswith("(define-fun ")
                            for item in self.encoding.definitions))
        self.assertFalse(any("declare-const" in item
                             for item in self.encoding.definitions))
        self.assertFalse(any("::visible::" in item
                             for item in self.encoding.declarations))

    def test_boundary_initializes_only_raw_inputs_and_requirement_accumulators(self):
        joined = "\n".join(self.encoding.boundary_assertions)
        self.assertEqual(len(self.encoding.boundary_assertions), 10)
        self.assertIn("slice:shield:setPoint", joined)
        self.assertIn("semantic:set_point", joined)
        self.assertIn("slice:shield:temperatureCelcius", joined)
        self.assertIn("semantic:received_temperature_payload", joined)
        self.assertIn("slice:shield:done", joined)
        self.assertIn("semantic:latched_completion", joined)
        self.assertEqual(joined.count("property_true"), 3)
        self.assertEqual(joined.count("no_error"), 3)
        self.assertNotIn("slice:command:", joined)
        self.assertNotIn("saved_mode", joined)

    def test_shield_keeps_source_ieee_operation_order_and_pretransition_error(self):
        definitions = "\n".join(self.encoding.definitions)
        self.assertIn("fp.geq", definitions)
        self.assertIn("fp.add RNE", definitions)
        self.assertIn("fp.leq", definitions)
        self.assertIn("fp.sub RNE", definitions)
        self.assertIn("shield:temperatureCelcius", definitions)
        self.assertIn("semantic:tolerance", definitions)
        boundary = "\n".join(self.encoding.boundary_assertions)
        self.assertIn("(=> (not |fixture-interface::definition::shield-error|)", boundary)
        self.assertTrue(self.encoding.guarded_transition.startswith(
            "(or |fixture-interface::definition::shield-error| (and "
        ))
        self.assertEqual(
            self.encoding.transition_assertion_count,
            len(self.transition.assertions),
        )

    def test_exit_selection_groups_identical_terms_once(self):
        selected = [item for item in self.encoding.definitions
                    if item.startswith(
                        "(define-fun |fixture-interface::definition::selected::"
                    )]
        self.assertEqual(len(selected), 8)
        self.assertEqual(sum("selected::semantic:engine_time" in item
                             for item in selected), 1)
        self.assertFalse(any("selected::semantic:set_point" in item
                             for item in selected))

    def test_source_error_is_all_seven_error_control_configurations(self):
        definition = next(item for item in self.encoding.definitions
                          if "::definition::source-error|" in item)
        error_symbols = [
            symbol for state, symbol in self.transition.control_state_symbols
            if state.node_id == "execution_error"
        ]
        self.assertEqual(len(error_symbols), 7)
        self.assertTrue(all(symbol in definition for symbol in error_symbols))
        self.assertEqual(sum(definition.count(symbol) for symbol in error_symbols), 7)

    def test_reward_outcome_and_error_priority_are_explicit(self):
        definitions = "\n".join(self.encoding.definitions)
        reward = next(item for item in self.encoding.definitions
                      if "::definition::reward|" in item)
        outcome = next(item for item in self.encoding.definitions
                       if "::definition::outcome|" in item)
        self.assertLess(reward.index("interface-error"),
                        reward.index("any-property-false"))
        self.assertIn(fp_literal(0.0, NativeSort.FLOAT64), reward)
        self.assertIn(fp_literal(-1.0, NativeSort.FLOAT64), reward)
        self.assertIn(fp_literal(1.0, NativeSort.FLOAT64), reward)
        self.assertIn(fp_literal(-0.01, NativeSort.FLOAT64), reward)
        self.assertLess(outcome.index("interface-error"),
                        outcome.index("any-property-false"))
        for constructor in ("outcome_error", "outcome_terminal", "outcome_continue"):
            self.assertIn(constructor, outcome)
        self.assertIn("no_execution_error", definitions)

    def test_observation_duration_and_availability_are_exact_expressions(self):
        definitions = "\n".join(self.encoding.definitions)
        self.assertEqual(definitions.count("((_ to_fp 8 24) RNE (fp.div RNE"), 2)
        self.assertIn("#x4037e6330c12f39e", definitions)
        elapsed_ticks = next(item for item in self.encoding.definitions
                             if "::definition::elapsed-ticks|" in item)
        cycle_time_symbols = [
            symbol for state, symbol in self.transition.control_state_symbols
            if state.node_id == "cycle/time"
        ]
        self.assertEqual(len(cycle_time_symbols), 1)
        self.assertIn(cycle_time_symbols[0], elapsed_ticks)
        self.assertIn("fp.sub RNE", definitions)
        self.assertEqual(
            self.encoding.visible("action_availability_mask").text, "15"
        )
        self.assertIn("outcome_continue", self.encoding.visible(
            "next_observation.setPoint.float32_bits"
        ).availability)
        self.assertIn("property-results-available", self.encoding.visible(
            "property_status:Heat When Cold"
        ).availability)

    def test_mutated_visible_or_exit_inventory_fails_closed(self):
        missing_visible = replace(
            self.slice, visible_terms=self.slice.visible_terms[:-1]
        )
        missing_exit = replace(
            self.transition, exit_state_terms=self.transition.exit_state_terms[:-1]
        )
        first_key, first_terms = self.transition.exit_state_terms[0]
        incomplete_exit = replace(
            self.transition,
            exit_state_terms=((first_key, first_terms[:-1]),)
                             + self.transition.exit_state_terms[1:],
        )
        for slice_, transition in (
            (missing_visible, self.transition),
            (self.slice, missing_exit),
            (self.slice, incomplete_exit),
        ):
            with self.subTest(slice_=slice_, transition=transition):
                with self.assertRaises(UnsupportedLoweringError):
                    compile_thermostat_interface(
                        slice_, self.ir, self.interval, transition,
                        namespace="mutated-interface",
                    )

    def test_mutated_native_shield_order_fails_closed(self):
        contract = deepcopy(load_native_semantics_contract())
        contract["shield"]["required_action_order"][0] = (
            "temperatureCelcius <= setPoint - tolerance => 1"
        )
        with self.assertRaises(UnsupportedLoweringError):
            compile_thermostat_interface(
                self.slice, self.ir, self.interval, self.transition,
                namespace="mutated-interface", native_contract=contract,
            )

    def test_encoding_is_deterministic_and_has_a_small_interface_delta(self):
        replay = compile_thermostat_interface(
            self.slice, self.ir, self.interval, self.transition,
            namespace="fixture-interface",
        )
        self.assertEqual(self.encoding, replay)
        delta = len(self.encoding.smt2().encode()) - len(self.transition.smt2().encode())
        # One guarded conjunction replaces hundreds of repeated ``assert``
        # wrappers, so the exact interface can be slightly smaller overall.
        self.assertLess(abs(delta), 25_000)

    @unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
    def test_shield_error_does_not_require_a_spurious_simulator_path(self):
        boundary = dict(self.transition.boundary_entry_terms)
        assertions = [
            f"(= {boundary['semantic:set_point']} "
            f"{fp_literal(0.0, NativeSort.FLOAT64)})",
            f"(= {boundary['semantic:received_temperature_payload']} "
            f"{fp_literal(0.0, NativeSort.FLOAT64)})",
            f"(= {boundary['semantic:tolerance']} "
            f"{fp_literal(-1.0, NativeSort.FLOAT64)})",
            *(f"(not {symbol})" for _, symbol in self.transition.control_state_symbols),
        ]
        result = run_smt2_query(self.encoding.smt2(*assertions), timeout_ms=120_000)
        self.assertIs(result.status, SolverStatus.SAT, result.reason)

    @unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
    def test_successful_shield_requires_the_existing_transition_relation(self):
        boundary = dict(self.transition.boundary_entry_terms)
        assertions = [
            f"(= {boundary['semantic:set_point']} "
            f"{fp_literal(0.0, NativeSort.FLOAT64)})",
            f"(= {boundary['semantic:received_temperature_payload']} "
            f"{fp_literal(0.0, NativeSort.FLOAT64)})",
            f"(= {boundary['semantic:tolerance']} "
            f"{fp_literal(1.0, NativeSort.FLOAT64)})",
            *(f"(not {symbol})" for _, symbol in self.transition.control_state_symbols),
        ]
        result = run_smt2_query(self.encoding.smt2(*assertions), timeout_ms=120_000)
        self.assertIs(result.status, SolverStatus.UNSAT, result.reason)


if __name__ == "__main__":
    unittest.main()
