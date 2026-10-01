#!/usr/bin/env python3
"""Exact finite-buffer layout, padding, shift, and mutation checks."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_contract import load_controller_step_contract
from clarity.certification.markov_extract import extract_thermostat_markov_ir
from clarity.certification.markov_interval import derive_thermostat_decision_interval
from clarity.certification.markov_ir import NativeSort
from clarity.certification.markov_obligations import (
    ObligationStatus, build_obligation_manifest,
)
from clarity.certification.markov_slice import build_thermostat_relevance_slice
from clarity.certification.markov_z3 import (
    SolverStatus, UnsupportedLoweringError, run_smt2_query,
)
from clarity.certification.markov_z3_event import enum_constructor
from clarity.certification.markov_z3_expr import fp_literal
from clarity.certification.markov_z3_history import (
    ACTION_COMPONENTS, OBSERVATION_FIELDS, compile_buffer_history,
)
from clarity.certification.markov_z3_interface import compile_thermostat_interface
from clarity.certification.markov_z3_transition import compile_transition_relation


HAS_Z3 = importlib.util.find_spec("z3") is not None


class BufferHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir = extract_thermostat_markov_ir()
        cls.interval = derive_thermostat_decision_interval(cls.ir)
        cls.slice = build_thermostat_relevance_slice(cls.ir, cls.interval)
        cls.transition = compile_transition_relation(
            cls.slice, cls.ir, cls.interval, namespace="fixture"
        )
        cls.interface = compile_thermostat_interface(
            cls.slice, cls.ir, cls.interval, cls.transition,
            namespace="fixture-interface",
        )
        cls.encoding = compile_buffer_history(
            cls.interface, cls.transition, b_obs=2, b_act=1,
            namespace="fixture-history",
        )

    def test_flattened_layout_is_exact(self):
        slots = self.encoding.current_slots
        self.assertEqual(self.encoding.scalar_width, 10)
        self.assertEqual([slot.index for slot in slots], list(range(10)))
        self.assertEqual(
            [(slot.section, slot.lag, slot.component) for slot in slots],
            [
                ("current_observation", 0, "setPoint"),
                ("current_observation", 0, "temperatureCelcius"),
                ("past_observation", 1, "setPoint"),
                ("past_observation", 1, "temperatureCelcius"),
                ("past_observation", 2, "setPoint"),
                ("past_observation", 2, "temperatureCelcius"),
                *(('past_executed_action', 1, item)
                  for item in ACTION_COMPONENTS),
            ],
        )
        self.assertEqual(
            [(slot.section, slot.lag, slot.component)
             for slot in self.encoding.next_slots],
            [(slot.section, slot.lag, slot.component) for slot in slots],
        )

    def test_current_observation_uses_exact_runtime_float_conversion(self):
        definitions = "\n".join(self.encoding.definitions)
        self.assertEqual(definitions.count(
            "(define-fun |fixture-history::definition::current-observation::"
        ), 2)
        self.assertEqual(definitions.count("((_ to_fp 8 24) RNE (fp.div RNE"), 2)
        self.assertIn("slice:shield:setPoint", definitions)
        self.assertIn("slice:shield:temperatureCelcius", definitions)
        self.assertIn("#x4037e6330c12f39e", definitions)

    def test_shift_is_newest_first_and_records_executed_action(self):
        for field in OBSERVATION_FIELDS:
            current = self.encoding.slot(
                "current", "current_observation", 0, field
            )
            newest = self.encoding.slot(
                "next", "past_observation", 1, field
            )
            older_current = self.encoding.slot(
                "current", "past_observation", 1, field
            )
            older_next = self.encoding.slot(
                "next", "past_observation", 2, field
            )
            self.assertEqual(newest.source, f"current-slot:{current.index}")
            self.assertEqual(older_next.source, f"current-slot:{older_current.index}")

        definitions = "\n".join(self.encoding.definitions)
        boundary = dict(self.transition.boundary_entry_terms)
        self.assertIn(boundary["slice:executed_action"], definitions)
        self.assertNotIn(boundary["slice:policy_proposal"], definitions)
        for component in ACTION_COMPONENTS:
            slot = self.encoding.slot(
                "next", "past_executed_action", 1, component
            )
            self.assertEqual(slot.source, "interface:executed_action_one_hot")
            self.assertIn(enum_constructor("ExecutedAction", component), definitions)

    def test_reset_prefix_padding_is_exact_and_complete(self):
        cases = self.encoding.history_cases
        self.assertEqual([case.name for case in cases], [
            "reset_prefix_0", "reset_prefix_1", "steady_state",
        ])
        self.assertEqual([len(case.padding_assertions) for case in cases], [8, 2, 0])
        zero = fp_literal(0.0, NativeSort.FLOAT32)
        self.assertTrue(all(assertion.endswith(f" {zero})")
                            for assertion in cases[0].padding_assertions))
        reset_one = "\n".join(cases[1].padding_assertions)
        self.assertIn("past-observation::2", reset_one)
        self.assertNotIn("past-observation::1", reset_one)
        self.assertNotIn("past-executed-action", reset_one)

    def test_next_buffer_has_explicit_continue_only_availability(self):
        definition = next(
            item for item in self.encoding.definitions
            if "next-buffer-available" in item
        )
        self.assertIn(self.interface.visible("executed_action").availability, definition)
        self.assertEqual(definition.count("outcome_continue"), 2)
        self.assertNotIn("outcome_terminal", definition)
        self.assertNotIn("outcome_error", definition)

    def test_history_adds_no_transition_copy(self):
        self.assertEqual(self.encoding.transition_declaration_count, 638)
        self.assertEqual(len(self.encoding.declarations), 8)
        self.assertEqual(len(self.encoding.definitions), 13)
        self.assertEqual(len(set(self.encoding.declarations)), 8)
        self.assertEqual(len(set(self.encoding.definitions)), 13)
        self.assertTrue(all("past-" in item for item in self.encoding.declarations))
        self.assertFalse(any(item in self.interface.declarations
                             for item in self.encoding.declarations))
        rendered = self.encoding.smt2()
        for declaration in self.interface.declarations:
            self.assertEqual(rendered.count(declaration), 1)

    def test_zero_length_and_growth_are_exactly_linear(self):
        for length in range(4):
            encoding = compile_buffer_history(
                self.interface, self.transition,
                b_obs=length, b_act=length,
                namespace=f"linear-{length}",
            )
            self.assertEqual(encoding.scalar_width, 2 + 6 * length)
            self.assertEqual(len(encoding.declarations), 6 * length)
            self.assertEqual(len(encoding.definitions), 5 + 6 * length)
            self.assertEqual(len(encoding.history_cases), max(1, length + 1))

    def test_mutated_contract_layout_and_action_history_fail_closed(self):
        mutations = []
        layout = deepcopy(load_controller_step_contract())
        layout["buffer"]["layout"][1:3] = reversed(
            layout["buffer"]["layout"][1:3]
        )
        mutations.append(layout)
        proposal = deepcopy(load_controller_step_contract())
        proposal["actions"]["history_records"] = "policy_proposal"
        mutations.append(proposal)
        width = deepcopy(load_controller_step_contract())
        width["buffer"]["action_width"] = 3
        mutations.append(width)
        padding = deepcopy(load_controller_step_contract())
        padding["buffer"]["observation_padding"] = "numeric_zero"
        mutations.append(padding)
        shift = deepcopy(load_controller_step_contract())
        shift["buffer"]["step_rule"] = "push proposal after augment"
        mutations.append(shift)
        for contract in mutations:
            with self.subTest(contract=contract):
                with self.assertRaises(UnsupportedLoweringError):
                    compile_buffer_history(
                        self.interface, self.transition, b_obs=2, b_act=1,
                        namespace="mutated-contract",
                        controller_contract=contract,
                    )

    def test_mutated_proposal_or_availability_interface_fails_closed(self):
        executed = self.interface.visible("executed_action")
        proposal_text = dict(self.transition.boundary_entry_terms)[
            "slice:policy_proposal"
        ]
        visible = tuple(
            replace(term, text=proposal_text) if term.name == executed.name else term
            for term in self.interface.visible_terms
        )
        wrong_action = replace(self.interface, visible_terms=visible)

        observation_name = "next_observation.setPoint.float32_bits"
        visible = tuple(
            replace(term, availability="true")
            if term.name == observation_name else term
            for term in self.interface.visible_terms
        )
        wrong_availability = replace(self.interface, visible_terms=visible)
        for interface in (wrong_action, wrong_availability):
            with self.subTest(interface=interface):
                with self.assertRaises(UnsupportedLoweringError):
                    compile_buffer_history(
                        interface, self.transition, b_obs=2, b_act=1,
                        namespace="mutated-interface",
                    )

    def test_compilation_is_deterministic_and_does_not_open_manifest(self):
        replay = compile_buffer_history(
            self.interface, self.transition, b_obs=2, b_act=1,
            namespace="fixture-history",
        )
        self.assertEqual(self.encoding, replay)
        manifest = build_obligation_manifest(
            self.slice, self.interval, b_obs=2, b_act=1
        )
        self.assertEqual(
            [case.name for case in self.encoding.history_cases],
            [case.name for case in manifest.history_cases],
        )
        self.assertTrue(all(
            item.status is ObligationStatus.NOT_RUN
            for item in manifest.structural_obligations
        ))
        self.assertFalse(manifest.certificate_ready)

    def test_invalid_lengths_and_empty_namespace_fail(self):
        for b_obs, b_act in ((-1, 0), (0, -1), (True, 0), (0, False)):
            with self.subTest(b_obs=b_obs, b_act=b_act):
                with self.assertRaises(ValueError):
                    compile_buffer_history(
                        self.interface, self.transition,
                        b_obs=b_obs, b_act=b_act,
                    )
        with self.assertRaises(ValueError):
            compile_buffer_history(
                self.interface, self.transition, b_obs=0, b_act=0,
                namespace="",
            )

    @unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
    def test_z3_rejects_shift_padding_or_terminal_availability_violation(self):
        next_obs = self.encoding.slot(
            "next", "past_observation", 1, "setPoint"
        )
        current_obs = self.encoding.slot(
            "current", "current_observation", 0, "setPoint"
        )
        padded = self.encoding.slot(
            "current", "past_executed_action", 1, "action_0"
        )
        zero = fp_literal(0.0, NativeSort.FLOAT32)
        outcome = self.interface.visible("outcome_constructor").text
        terminal = enum_constructor("OutcomeConstructor", "outcome_terminal")
        violation = (
            f"(or (not (= {next_obs.text} {current_obs.text})) "
            f"(not (= {padded.text} {zero})) "
            f"(and (= {outcome} {terminal}) {self.encoding.next_available}))"
        )
        result = run_smt2_query(
            self.encoding.smt2(violation, history_case="reset_prefix_0"),
            timeout_ms=120_000,
        )
        self.assertIs(result.status, SolverStatus.UNSAT, result.reason)


if __name__ == "__main__":
    unittest.main()
