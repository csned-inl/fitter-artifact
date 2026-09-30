#!/usr/bin/env python3
"""Event-local SSA lowering, frame, mutation, and pinned-Z3 tests."""

from __future__ import annotations

from dataclasses import replace
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_extract import extract_thermostat_markov_ir
from clarity.certification.markov_interval import derive_thermostat_decision_interval
from clarity.certification.markov_slice import build_thermostat_relevance_slice
from clarity.certification.markov_z3 import SolverStatus, UnsupportedLoweringError, run_smt2_query
from clarity.certification.markov_z3_event import SCALAR_SORTS, SUPPORTED_OPERATIONS, compile_scalar_event


HAS_Z3 = importlib.util.find_spec("z3") is not None


class ScalarEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir = extract_thermostat_markov_ir()
        cls.slice = build_thermostat_relevance_slice(
            cls.ir, derive_thermostat_decision_interval(cls.ir)
        )
        cls.source = {event.node_id: event for event in cls.ir.events}
        cls.sliced = {event.node_id: event for event in cls.slice.events}
        cls.supported = tuple(
            event for event in cls.slice.events
            if event.operation in SUPPORTED_OPERATIONS
        )
        cls.scalar_count = sum(
            storage.identity.native_sort in SCALAR_SORTS
            for storage in cls.slice.storages
        )

    def encoding(self, node_id):
        return compile_scalar_event(
            self.slice, self.source[node_id], self.sliced[node_id], namespace="fixture"
        )

    def test_complete_supported_event_inventory(self):
        self.assertEqual(len(self.supported), 16)
        self.assertEqual(
            {event.operation for event in self.supported}, SUPPORTED_OPERATIONS
        )
        for event in self.supported:
            encoding = self.encoding(event.node_id)
            self.assertEqual(len(encoding.declarations), 2 * self.scalar_count)
            self.assertEqual(
                len(encoding.frames) + len(event.writes), self.scalar_count
            )

    def test_environment_assignment_uses_ieee_update_and_frames(self):
        encoding = self.encoding("system::environment/step/1")
        self.assertEqual(len(encoding.updates), 1)
        update = encoding.updates[0]
        self.assertIn("semantic:physical_temperature", update)
        self.assertIn("fp.add RNE", update)
        self.assertIn("fp.div RNE", update)
        self.assertFalse(any(
            "exit::semantic:physical_temperature" in frame
            for frame in encoding.frames
        ))

    def test_branch_retains_guard_and_frames_every_scalar(self):
        encoding = self.encoding("system::controller/step/6")
        self.assertIsNotNone(encoding.branch_condition)
        self.assertIn("(not ", encoding.branch_condition.text)
        self.assertEqual(len(encoding.updates), 0)
        self.assertEqual(len(encoding.frames), self.scalar_count)

    def test_dt_and_engine_time_updates_are_exact(self):
        dt = self.encoding("cycle/dt").updates[0]
        clock = self.encoding("cycle/time").updates[0]
        self.assertIn("entry::slice:configured_dt", dt)
        self.assertIn("fp.add RNE", clock)
        self.assertIn("entry::semantic:engine_time", clock)

    def test_write_set_mutation_fails_closed(self):
        event = replace(self.sliced["system/step/1"], writes=())
        with self.assertRaises(UnsupportedLoweringError):
            compile_scalar_event(self.slice, self.source[event.node_id], event)

    def test_unsupported_operation_fails_closed(self):
        event = self.sliced["system::thermometer/step/4"]
        with self.assertRaises(UnsupportedLoweringError):
            compile_scalar_event(self.slice, self.source[event.node_id], event)

    @unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
    def test_every_supported_relation_is_satisfiable_in_pinned_z3(self):
        for event in self.supported:
            encoding = self.encoding(event.node_id)
            result = run_smt2_query(encoding.smt2(), timeout_ms=30_000)
            self.assertIs(result.status, SolverStatus.SAT,
                          f"{event.node_id}: {result.reason}")


if __name__ == "__main__":
    unittest.main()
