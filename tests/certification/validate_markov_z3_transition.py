#!/usr/bin/env python3
"""Composed control/event transition-relation checks."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_extract import extract_thermostat_markov_ir
from clarity.certification.markov_interval import derive_thermostat_decision_interval
from clarity.certification.markov_slice import build_thermostat_relevance_slice
from clarity.certification.markov_z3 import SolverStatus, run_smt2_query
from clarity.certification.markov_z3_transition import compile_transition_relation


HAS_Z3 = importlib.util.find_spec("z3") is not None


class TransitionRelationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir = extract_thermostat_markov_ir()
        cls.interval = derive_thermostat_decision_interval(cls.ir)
        cls.slice = build_thermostat_relevance_slice(cls.ir, cls.interval)
        cls.encoding = compile_transition_relation(
            cls.slice, cls.ir, cls.interval, namespace="fixture"
        )

    def test_complete_composition_metrics(self):
        self.assertEqual(self.encoding.control_state_count, 132)
        self.assertEqual(self.encoding.control_edge_count, 281)
        self.assertEqual(self.encoding.state_term_count, 47)
        self.assertEqual(len(self.encoding.declarations), 12821)
        self.assertEqual(len(self.encoding.edge_assertions), 281)
        self.assertEqual(len(self.encoding.bridge_assertions), 281 * 47)

    def test_every_event_update_is_control_guarded(self):
        self.assertEqual(len(self.encoding.event_assertions), 132 * 47)
        self.assertTrue(all(formula.startswith("(=> ")
                            for formula in self.encoding.event_assertions))

    def test_branch_trigger_and_transport_edges_are_attached(self):
        joined = "\n".join(self.encoding.edge_assertions)
        self.assertIn("slice:command:heater:on", joined)
        self.assertIn("semantic:thermometer_payload_present", joined)
        self.assertIn("semantic:latched_completion", joined)

    def test_source_division_zero_routes_to_exception(self):
        joined = "\n".join(self.encoding.edge_assertions)
        self.assertIn("fp.isZero", joined)

    def test_encoding_is_deterministic(self):
        replay = compile_transition_relation(
            self.slice, self.ir, self.interval, namespace="fixture"
        )
        self.assertEqual(self.encoding, replay)

    @unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
    def test_composed_relation_is_satisfiable_in_pinned_z3(self):
        result = run_smt2_query(self.encoding.smt2(), timeout_ms=120_000)
        self.assertIs(result.status, SolverStatus.SAT, result.reason)


if __name__ == "__main__":
    unittest.main()
