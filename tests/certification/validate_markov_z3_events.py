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
from clarity.certification.markov_z3_event import (
    SUPPORTED_OPERATIONS, compile_scalar_event, supports_scalar_event,
)


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
            if supports_scalar_event(event)
        )
        cls.state_count = len(cls.slice.storages)

    def encoding(self, node_id):
        return compile_scalar_event(
            self.slice, self.source[node_id], self.sliced[node_id], namespace="fixture"
        )

    def test_complete_supported_event_inventory(self):
        self.assertEqual(len(self.supported), 51)
        self.assertEqual(
            {event.operation for event in self.supported}, SUPPORTED_OPERATIONS
        )
        for event in self.supported:
            encoding = self.encoding(event.node_id)
            self.assertEqual(len(encoding.declarations), 2 * self.state_count)
            self.assertEqual(
                len(encoding.frames) + len(event.writes), self.state_count
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
        self.assertEqual(len(encoding.frames), self.state_count)

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

    def test_unknown_operation_fails_closed(self):
        event = replace(self.sliced["cycle/check"], operation="unknown")
        with self.assertRaises(UnsupportedLoweringError):
            compile_scalar_event(self.slice, self.source[event.node_id], event)

    def test_constraint_solve_is_four_exact_copies(self):
        encoding = self.encoding("cycle/solve")
        self.assertEqual(len(encoding.updates), 4)
        joined = "\n".join(encoding.updates)
        for source in ("semantic:ac_output", "semantic:heater_output",
                       "semantic:held_temperature", "semantic:physical_temperature"):
            self.assertIn(source, joined)

    def test_thermometer_send_and_accept_are_distinct_copies(self):
        send = self.encoding("system::thermometer/step/4")
        accept = self.encoding("system::controller/step/2")
        self.assertIn("temperatureReading::temperatureCelcius", send.updates[0])
        self.assertIn(" true)", send.updates[1])
        self.assertIsNotNone(accept.precondition)
        self.assertIn("thermometer_payload_present", accept.precondition.text)
        self.assertIn("received_temperature_payload", accept.updates[0])
        self.assertIn(" false)", accept.updates[2])

    def test_controller_commands_match_and_are_consumed_exactly(self):
        send = self.encoding("system::controller/step/8/true/1")
        entry = self.encoding("system::heater/machine/entry")
        match = self.encoding("system::heater/machine/0/trigger")
        finish = self.encoding("system::heater/machine/0/finish")
        self.assertIn("slice:command:heater:on", send.updates[0])
        self.assertIn("slice:machine:heater:saved_mode", entry.updates[0])
        self.assertIn("slice:command:heater:on", match.branch_condition.text)
        self.assertTrue(any("slice:command:heater:on" in update and "false" in update
                            for update in finish.updates))

    def test_requirements_accumulate_false_and_preserve_prior_error(self):
        encoding = self.encoding("cycle/check")
        self.assertEqual(len(encoding.updates), 6)
        joined = "\n".join(encoding.updates)
        self.assertIn("property_true", joined)
        self.assertIn("property_false", joined)
        self.assertEqual(joined.count(":error| |"), 3)

    def test_executed_action_is_decoded_by_exact_constructors(self):
        encoding = self.encoding("system::controller/step/3/resume")
        self.assertEqual(len(encoding.updates), 2)
        joined = "\n".join(encoding.updates)
        for constructor in ("action_1", "action_2", "action_3"):
            self.assertIn(constructor, joined)
        self.assertTrue(any("ExecutedAction" in item
                            for item in encoding.sort_declarations))

    def test_machine_modes_use_saved_local_and_exact_constructor(self):
        source_test = self.encoding("system::heater/machine/0/from")
        finish = self.encoding("system::heater/machine/0/finish")
        self.assertIn("slice:machine:heater:saved_mode", source_test.branch_condition.text)
        self.assertIn("::reset", source_test.branch_condition.text)
        self.assertIn("::on", finish.updates[0])

    def test_decision_latches_and_completion_reads_boolean(self):
        decision = self.encoding("system::controller/step/3")
        completion = self.encoding("cycle/terminal")
        self.assertIn("semantic:latched_completion", decision.updates[0])
        self.assertIsNotNone(completion.branch_condition)
        self.assertIn("semantic:latched_completion", completion.branch_condition.text)

    @unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
    def test_every_supported_relation_is_satisfiable_in_pinned_z3(self):
        for event in self.supported:
            encoding = self.encoding(event.node_id)
            result = run_smt2_query(encoding.smt2(), timeout_ms=30_000)
            self.assertIs(result.status, SolverStatus.SAT,
                          f"{event.node_id}: {result.reason}")


if __name__ == "__main__":
    unittest.main()
