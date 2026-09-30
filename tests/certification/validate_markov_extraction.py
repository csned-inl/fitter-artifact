#!/usr/bin/env python3
"""Mutation tests for the thermostat typed extraction gate."""

from __future__ import annotations

from dataclasses import replace
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_extract import extract_thermostat_markov_ir
from clarity.certification.markov_ir import NativeSort
from clarity.certification.markov_validate import validate_thermostat_markov_ir


class ThermostatMarkovExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir = extract_thermostat_markov_ir()

    def validate(self, ir):
        return validate_thermostat_markov_ir(ir)

    def test_checked_extraction(self):
        self.assertEqual(self.validate(self.ir), [])
        self.assertEqual(len(self.ir.events), 80)
        self.assertEqual(len(self.ir.source_events), 31)
        self.assertEqual(len(self.ir.storages), 17 + 119)
        self.assertEqual(len(self.ir.local_obligations), 80)

    def test_physical_held_sent_received_are_distinct(self):
        selected = [storage.identity for storage in self.ir.storages
                    if storage.identity.uid in {
                        "semantic:physical_temperature", "semantic:held_temperature",
                        "semantic:sent_temperature_payload",
                        "semantic:received_temperature_payload",
                    }]
        self.assertEqual(len(set(selected)), 4)

    def test_constraint_solve_includes_all_flow_sources(self):
        solve = next(event for event in self.ir.events if event.node_id == "cycle/solve")
        reads = {access.storage_uid for access in solve.graph_reads}
        self.assertTrue({
            "graph:system::environment::temperatureCelcius",
            "graph:system::heater::heatOut::heat::rateWatts",
            "graph:system::ac::heatOut::heat::rateWatts",
        }.issubset(reads))

    def test_mutated_native_sort_is_rejected(self):
        storage = list(self.ir.storages)
        index = next(i for i, item in enumerate(storage)
                     if item.identity.uid == "semantic:physical_temperature")
        storage[index] = replace(storage[index], identity=replace(
            storage[index].identity, native_sort=NativeSort.BOOL
        ))
        errors = self.validate(replace(self.ir, storages=tuple(storage)))
        self.assertTrue(any("semantic identity/type mismatch" in error for error in errors))

    def test_merged_physical_and_held_identity_is_rejected(self):
        storage = list(self.ir.storages)
        physical = next(item for item in storage
                        if item.identity.uid == "semantic:physical_temperature")
        index = next(i for i, item in enumerate(storage)
                     if item.identity.uid == "semantic:held_temperature")
        held = storage[index]
        storage[index] = replace(held, identity=replace(
            held.identity, owner=physical.identity.owner, path=physical.identity.path,
            role=physical.identity.role,
        ))
        errors = self.validate(replace(self.ir, storages=tuple(storage)))
        self.assertTrue(any("semantic identity/type mismatch" in error or
                            "aliased semantic storage identity" in error for error in errors))

    def test_removed_source_event_is_rejected(self):
        events = tuple(event for event in self.ir.events
                       if event.node_id != "system::environment/step/1")
        # Bypass construction-time invariant only by changing the inventory too;
        # the independent source reconstruction must still reject the mutation.
        sources = tuple(item for item in self.ir.source_events
                        if item[0] != "system::environment/step/1")
        mutated = replace(self.ir, events=events, source_events=sources)
        errors = self.validate(mutated)
        self.assertTrue(any("coverage mismatch" in error or "inventory mismatch" in error
                            for error in errors))

    def test_successor_reordering_is_rejected(self):
        events = list(self.ir.events)
        index = next(i for i, event in enumerate(events)
                     if event.node_id == "cycle/terminal")
        events[index] = replace(events[index], successors=tuple(reversed(events[index].successors)))
        errors = self.validate(replace(self.ir, events=tuple(events)))
        self.assertTrue(any("successor/order mismatch" in error for error in errors))

    def test_proposed_action_convention_is_rejected(self):
        events = list(self.ir.events)
        index = next(i for i, event in enumerate(events)
                     if event.node_id == "system::controller/step/3/resume")
        events[index] = replace(
            events[index],
            data_json=events[index].data_json.replace('"executed"', '"proposed"'),
        )
        errors = self.validate(replace(self.ir, events=tuple(events)))
        self.assertTrue(any("operation data mismatch" in error or
                            "does not install the executed action" in error for error in errors))

    def test_missing_copy_witness_is_rejected(self):
        events = list(self.ir.events)
        index = next(i for i, event in enumerate(events)
                     if event.node_id == "system::thermometer/step/4")
        events[index] = replace(events[index], semantic_writes=())
        errors = self.validate(replace(self.ir, events=tuple(events)))
        self.assertTrue(any("send lacks" in error for error in errors))

    def test_send_copy_records_the_local_payload_read(self):
        send = next(event for event in self.ir.events
                    if event.node_id == "system::thermometer/step/4")
        reads = {access.storage_uid for access in send.graph_reads}
        self.assertIn(
            "graph:system::thermometer::temperatureReading::temperatureCelcius",
            reads,
        )

    def test_missing_boundary_is_rejected(self):
        boundary = replace(self.ir.decision_boundary, request_node="missing")
        errors = self.validate(replace(self.ir, decision_boundary=boundary))
        self.assertTrue(any("decision boundary mismatch" in error for error in errors))

    def test_removed_local_simulation_obligation_is_rejected(self):
        obligations = self.ir.local_obligations[1:]
        errors = self.validate(replace(self.ir, local_obligations=obligations))
        self.assertTrue(any("obligation coverage mismatch" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
