#!/usr/bin/env python3
"""Linear finite predecessor-window lowering and mutation checks."""

from __future__ import annotations

from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_extract import extract_thermostat_markov_ir
from clarity.certification.markov_interval import derive_thermostat_decision_interval
from clarity.certification.markov_slice import build_thermostat_relevance_slice
from clarity.certification.markov_z3 import (
    SolverStatus, UnsupportedLoweringError, run_smt2_query,
)
from clarity.certification.markov_z3_history import compile_buffer_history
from clarity.certification.markov_z3_interface import compile_thermostat_interface
from clarity.certification.markov_z3_transition import compile_transition_relation
from clarity.certification.markov_z3_window import (
    INITIAL_STATE_PREMISE, compile_history_window,
)


HAS_Z3 = importlib.util.find_spec("z3") is not None


class HistoryWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir = extract_thermostat_markov_ir()
        cls.interval = derive_thermostat_decision_interval(cls.ir)
        cls.slice = build_thermostat_relevance_slice(cls.ir, cls.interval)
        cls.transition = compile_transition_relation(
            cls.slice, cls.ir, cls.interval, namespace="candidate"
        )
        cls.interface = compile_thermostat_interface(
            cls.slice, cls.ir, cls.interval, cls.transition,
            namespace="candidate-interface",
        )
        cls.candidate = compile_buffer_history(
            cls.interface, cls.transition, b_obs=2, b_act=1,
            namespace="candidate-history",
        )
        cls.windows = {
            case: compile_history_window(
                cls.slice, cls.ir, cls.interval,
                cls.transition, cls.candidate,
                history_case=case, namespace="window-" + case,
            )
            for case in ("reset_prefix_0", "reset_prefix_1", "steady_state")
        }

    def test_case_step_counts_are_exact(self):
        self.assertEqual(
            {name: len(window.steps) for name, window in self.windows.items()},
            {"reset_prefix_0": 0, "reset_prefix_1": 1, "steady_state": 2},
        )
        self.assertEqual(
            {name: window.transition_copy_count
             for name, window in self.windows.items()},
            {"reset_prefix_0": 1, "reset_prefix_1": 2, "steady_state": 3},
        )
        self.assertEqual(
            [step.decision_offset for step in self.windows["steady_state"].steps],
            [-2, -1],
        )

    def test_each_step_is_one_sparse_transition_not_path_enumeration(self):
        for name, window in self.windows.items():
            step_count = len(window.steps)
            with self.subTest(case=name):
                self.assertEqual(len(window.declarations), 646 + 638 * step_count)
                self.assertEqual(len(window.definitions), 38 + 25 * step_count)
                self.assertEqual(len(window.state_bridges), 36 * step_count)
                self.assertEqual(len(set(window.declarations)),
                                 len(window.declarations))
                self.assertTrue(all(
                    step.transition.state_term_count == 47
                    and len(step.transition.declarations) == 638
                    for step in window.steps
                ))

    def test_all_historical_steps_require_continue_and_next_decision(self):
        for window in self.windows.values():
            for step in window.steps:
                with self.subTest(case=window.history_case.name, step=step.index):
                    self.assertIn("outcome_continue", step.continue_assertion)
                    self.assertIn("shield-error", step.continue_assertion)
                    request_states = [
                        state for state, symbol in step.transition.control_state_symbols
                        if symbol == step.request_selector
                    ]
                    self.assertEqual(len(request_states), 1)
                    self.assertEqual(
                        request_states[0].node_id,
                        self.ir.decision_boundary.request_node,
                    )

    def test_state_bridges_cover_every_retained_storage_once_per_step(self):
        for window in self.windows.values():
            carried = set(window.carried_state_uids)
            reinitialized = set(window.reinitialized_uids)
            self.assertEqual(len(carried), 36)
            self.assertEqual(len(reinitialized), 11)
            self.assertFalse(carried.intersection(reinitialized))
            for index, step in enumerate(window.steps):
                target = (
                    window.steps[index + 1].transition
                    if index + 1 < len(window.steps) else self.transition
                )
                target_symbols = dict(target.boundary_entry_terms)
                matching = window.state_bridges[
                    index * 36:(index + 1) * 36
                ]
                self.assertEqual(len(matching), len(carried))
                self.assertTrue(all(
                    sum(symbol in assertion for assertion in matching) == 1
                    for uid, symbol in target_symbols.items() if uid in carried
                ))
                self.assertTrue(all(
                    symbol not in "\n".join(matching)
                    for uid, symbol in target_symbols.items()
                    if uid in reinitialized
                ))

    def test_next_decision_reinitializes_action_shield_and_property_cells(self):
        steady = self.windows["steady_state"]
        self.assertIn("slice:executed_action", steady.reinitialized_uids)
        self.assertIn("slice:policy_proposal", steady.reinitialized_uids)
        self.assertIn("slice:shield:setPoint", steady.reinitialized_uids)
        self.assertIn("slice:shield:temperatureCelcius", steady.reinitialized_uids)
        self.assertIn("slice:shield:done", steady.reinitialized_uids)
        self.assertEqual(sum(":property:" in uid
                             for uid in steady.reinitialized_uids), 6)
        bridges = "\n".join(steady.state_bridges)
        for step in steady.steps:
            target = dict(
                self.transition.boundary_entry_terms
                if step.index == len(steady.steps) - 1
                else steady.steps[step.index + 1].transition.boundary_entry_terms
            )
            self.assertNotIn(target["slice:executed_action"], bridges)
            self.assertNotIn(target["slice:policy_proposal"], bridges)

    def test_direct_lag_projection_matches_repeated_shift(self):
        steady = self.windows["steady_state"]
        obs = [item for item in steady.correspondences
               if item.source_kind == "boundary_observation"]
        actions = [item for item in steady.correspondences
                   if item.source_kind == "executed_action_one_hot"]
        self.assertEqual(len(obs), 4)
        self.assertEqual(len(actions), 4)
        self.assertEqual(
            {(item.target_lag, item.source_step) for item in obs},
            {(1, 1), (2, 0)},
        )
        self.assertEqual(
            {(item.target_lag, item.source_step) for item in actions},
            {(1, 1)},
        )
        joined = "\n".join(item.assertion for item in steady.correspondences)
        self.assertIn("ExecutedAction", joined)
        self.assertNotIn("PolicyProposal", joined)
        self.assertNotIn("policy_proposal", joined)

    def test_reset_prefix_populates_only_elapsed_lags(self):
        reset_zero = self.windows["reset_prefix_0"]
        reset_one = self.windows["reset_prefix_1"]
        self.assertEqual(len(reset_zero.correspondences), 0)
        self.assertEqual(len(reset_zero.history_case.padding_assertions), 8)
        self.assertEqual(len(reset_one.correspondences), 6)
        self.assertEqual(len(reset_one.history_case.padding_assertions), 2)
        self.assertEqual(
            {(item.target_section, item.target_lag)
             for item in reset_one.correspondences},
            {("past_observation", 1), ("past_executed_action", 1)},
        )

    def test_unavailable_reset_anchor_is_explicit_and_never_overclaimed(self):
        for window in self.windows.values():
            self.assertEqual(window.initial_state_premise, INITIAL_STATE_PREMISE)
            self.assertEqual(
                window.initial_state_premise,
                "true_type_domain_overapproximation",
            )
            self.assertFalse(window.reset_anchor_available)

    def test_candidate_mutations_fail_closed(self):
        mutations = (
            replace(self.candidate, observation_scale=2.0),
            replace(self.candidate,
                    declarations=self.candidate.declarations[:-1]),
            replace(self.candidate,
                    history_cases=self.candidate.history_cases[:-1]),
        )
        for candidate in mutations:
            with self.subTest(candidate=candidate):
                with self.assertRaises(UnsupportedLoweringError):
                    compile_history_window(
                        self.slice, self.ir, self.interval,
                        self.transition, candidate,
                        history_case="steady_state",
                        namespace="mutated-window",
                    )

    def test_unknown_case_and_empty_namespace_fail_closed(self):
        with self.assertRaises(UnsupportedLoweringError):
            compile_history_window(
                self.slice, self.ir, self.interval,
                self.transition, self.candidate,
                history_case="reset_prefix_2",
            )
        with self.assertRaises(ValueError):
            compile_history_window(
                self.slice, self.ir, self.interval,
                self.transition, self.candidate,
                history_case="steady_state", namespace="",
            )

    def test_base_formula_is_shared_across_predicate_queries(self):
        window = self.windows["steady_state"]
        base = window.smt2()
        left = window.smt2("true")
        right = window.smt2("false")
        self.assertTrue(left.startswith(base[:-1]))
        self.assertTrue(right.startswith(base[:-1]))
        self.assertEqual(left.count("declare-const"), base.count("declare-const"))
        self.assertEqual(right.count("declare-const"), base.count("declare-const"))

    @unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
    def test_each_overapproximating_history_case_is_nonvacuously_satisfiable(self):
        for name, window in self.windows.items():
            with self.subTest(case=name):
                result = run_smt2_query(window.smt2(), timeout_ms=120_000)
                self.assertIs(result.status, SolverStatus.SAT, result.reason)

    @unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
    def test_z3_rejects_a_buffer_correspondence_violation(self):
        window = self.windows["steady_state"]
        assertion = window.correspondences[0].assertion
        self.assertTrue(assertion.startswith("(= "))
        result = run_smt2_query(
            window.smt2(f"(not {assertion})"), timeout_ms=120_000,
        )
        self.assertIs(result.status, SolverStatus.UNSAT, result.reason)


if __name__ == "__main__":
    unittest.main()
