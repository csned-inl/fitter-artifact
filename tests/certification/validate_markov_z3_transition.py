#!/usr/bin/env python3
"""Composed control/event transition-relation checks."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_extract import extract_thermostat_markov_ir
from clarity.certification.markov_interval import derive_thermostat_decision_interval
from clarity.certification.markov_slice import build_thermostat_relevance_slice
from clarity.certification.markov_z3 import (
    SolverStatus, UnsupportedLoweringError, run_smt2_query,
)
from clarity.certification.markov_z3_expr import quoted_symbol
import clarity.certification.markov_z3_transition as transition_module
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
        self.assertEqual(self.encoding.state_term_count, 46)
        self.assertEqual(len(self.encoding.declarations), 635)
        self.assertEqual(sum("::boundary-entry::" in value
                             for value in self.encoding.declarations), 46)
        self.assertEqual(sum("::state-write::" in value
                             for value in self.encoding.declarations), 71)
        self.assertEqual(sum("::state-phi::" in value
                             for value in self.encoding.declarations), 105)
        self.assertEqual(len(self.encoding.edge_assertions), 281)
        self.assertEqual(len(self.encoding.bridge_assertions), 262)
        self.assertLess(len(self.encoding.smt2().encode()), 1_100_000)

    def test_every_event_update_is_control_guarded(self):
        self.assertEqual(len(self.encoding.event_assertions), 71)
        self.assertTrue(all(formula.startswith("(=> ")
                            for formula in self.encoding.event_assertions))

    def test_sparse_boundary_and_exit_interfaces_are_complete(self):
        self.assertEqual(
            {uid for uid, _ in self.encoding.boundary_entry_terms},
            {storage.identity.uid for storage in self.slice.storages},
        )
        self.assertEqual(len(self.encoding.exit_state_terms), 9)
        self.assertTrue(all(len(terms) == 46
                            for _, terms in self.encoding.exit_state_terms))
        self.assertNotIn("::instance::", self.encoding.smt2())

    def test_every_sparse_phi_has_all_distinct_incoming_values(self):
        phi_symbols = [
            declaration.split()[1]
            for declaration in self.encoding.declarations
            if "::state-phi::" in declaration
        ]
        for symbol in phi_symbols:
            with self.subTest(phi=symbol):
                defining_bridges = [
                    formula for formula in self.encoding.bridge_assertions
                    if f"(= {symbol} " in formula
                ]
                self.assertGreaterEqual(len(defining_bridges), 2)

    def test_failure_edges_preserve_pre_event_state(self):
        edge = next(
            edge for edge in self.interval.edges
            if edge.source.node_id == "system::environment/step/1"
            and edge.label == "exception"
        )
        selector = dict(self.encoding.control_edge_symbols)[edge]
        uid = "semantic:physical_temperature"
        write_symbol = quoted_symbol(
            "fixture", "state-write", hashlib.sha256(edge.source.key.encode()).hexdigest(), uid
        )
        matching = [
            formula for formula in self.encoding.bridge_assertions
            if selector in formula and uid in formula
        ]
        self.assertEqual(len(matching), 1)
        self.assertNotIn(write_symbol, matching[0])

    def test_sparse_composition_rejects_incomplete_local_ssa(self):
        original = transition_module.compile_scalar_event

        def missing_frame(*args, **kwargs):
            encoding = original(*args, **kwargs)
            return replace(encoding, frames=encoding.frames[:-1])

        def missing_update(*args, **kwargs):
            encoding = original(*args, **kwargs)
            return replace(encoding, updates=encoding.updates[:-1])

        for mutation in (missing_frame, missing_update):
            with self.subTest(mutation=mutation.__name__), patch.object(
                transition_module, "compile_scalar_event", side_effect=mutation
            ):
                with self.assertRaises(UnsupportedLoweringError):
                    compile_transition_relation(
                        self.slice, self.ir, self.interval, namespace="mutated"
                    )

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
