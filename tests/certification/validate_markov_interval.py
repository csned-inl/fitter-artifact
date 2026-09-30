#!/usr/bin/env python3
"""Finite first-outcome interval derivation and mutation tests."""

from __future__ import annotations

from dataclasses import replace
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_extract import extract_thermostat_markov_ir
from clarity.certification.markov_interval import (
    ControlEdge,
    derive_thermostat_decision_interval,
    interval_metrics,
    validate_thermostat_decision_interval,
)


class ThermostatDecisionIntervalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir = extract_thermostat_markov_ir()
        cls.interval = derive_thermostat_decision_interval(cls.ir)

    def test_checked_finite_interval(self):
        self.assertEqual(validate_thermostat_decision_interval(
            self.interval, self.ir), [])
        self.assertEqual(interval_metrics(self.interval), {
            "control_states": 132,
            "control_edges": 281,
            "source_nodes": 78,
            "max_node_visits_to_first_outcome": 102,
            "max_return_stack_depth": 1,
            "first_outcome_structural": True,
            "progress_status": "finite_acyclic_control_relation_checked",
        })

    def test_each_outcome_is_a_sink(self):
        sources = {edge.source for edge in self.interval.edges}
        self.assertTrue(all(exit_state not in sources for exit_state in self.interval.exits))
        self.assertEqual({state.node_id for state in self.interval.exits}, {
            "system::controller/step/3", "terminal", "execution_error",
        })

    def test_bound_mutation_is_rejected(self):
        mutated = replace(
            self.interval,
            max_node_visits_to_first_outcome=
                self.interval.max_node_visits_to_first_outcome - 1,
        )
        self.assertIn("finite interval bound mismatch",
                      validate_thermostat_decision_interval(mutated, self.ir))

    def test_removed_edge_is_rejected(self):
        mutated = replace(self.interval, edges=self.interval.edges[1:])
        self.assertIn("finite interval edge/order mismatch",
                      validate_thermostat_decision_interval(mutated, self.ir))

    def test_inserted_cycle_is_rejected_as_edge_mismatch(self):
        state = self.interval.entry
        mutated = replace(self.interval, edges=self.interval.edges + (
            ControlEdge(state, "mutation_cycle", state),
        ))
        self.assertIn("finite interval edge/order mismatch",
                      validate_thermostat_decision_interval(mutated, self.ir))

    def test_ir_identity_mutation_is_rejected(self):
        mutated = replace(self.interval, ir_sha256="0" * 64)
        self.assertIn("finite interval IR identity mismatch",
                      validate_thermostat_decision_interval(mutated, self.ir))


if __name__ == "__main__":
    unittest.main()
