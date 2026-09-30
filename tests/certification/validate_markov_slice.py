#!/usr/bin/env python3
"""Thermostat relevance-slice gate and mutation tests."""

from __future__ import annotations

from dataclasses import replace
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_extract import extract_thermostat_markov_ir
from clarity.certification.markov_interval import derive_thermostat_decision_interval
from clarity.certification.markov_ir import IRStorage, NativeSort, StorageId, StorageLayer
from clarity.certification.markov_slice import (
    build_thermostat_relevance_slice,
    slice_metrics,
    validate_thermostat_relevance_slice,
)


class ThermostatRelevanceSliceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir = extract_thermostat_markov_ir()
        cls.interval = derive_thermostat_decision_interval(cls.ir)
        cls.slice = build_thermostat_relevance_slice(cls.ir, cls.interval)

    def validate(self, value):
        return validate_thermostat_relevance_slice(value, self.ir, self.interval)

    def test_checked_slice_and_metrics(self):
        self.assertEqual(self.validate(self.slice), [])
        metrics = slice_metrics(self.slice, self.ir, self.interval)
        self.assertEqual(metrics["ir_storages"], 136)
        self.assertEqual(metrics["interval_control_states"], 132)
        self.assertEqual(metrics["interval_source_nodes"], 78)
        self.assertEqual(metrics["forbidden_sorts"], 0)
        self.assertLess(metrics["retained_storages"], metrics["ir_storages"])
        self.assertLess(metrics["retained_events"], metrics["interval_source_nodes"])

    def test_physical_state_survives_flow_dependency_closure(self):
        retained = {item.identity.uid for item in self.slice.storages}
        self.assertIn("semantic:physical_temperature", retained)
        self.assertIn("semantic:held_temperature", retained)
        self.assertIn("semantic:sent_temperature_payload", retained)
        self.assertIn("semantic:received_temperature_payload", retained)

    def test_no_generic_runtime_sort_survives(self):
        self.assertFalse(any(item.identity.native_sort in {
            NativeSort.RECORD, NativeSort.QUEUE, NativeSort.STACK,
        } for item in self.slice.storages))

    def test_removed_relevant_storage_is_rejected(self):
        storages = tuple(item for item in self.slice.storages
                         if item.identity.uid != "semantic:physical_temperature")
        errors = self.validate(replace(self.slice, storages=storages))
        self.assertIn("slice storage relevance closure mismatch", errors)

    def test_injected_generic_container_is_rejected_at_construction(self):
        bad = IRStorage(StorageId(
            uid="mutation:heap", owner="mutation", path=("heap",), role="runtime",
            declared_type="RuntimeRecord", native_sort=NativeSort.RECORD,
            layer=StorageLayer.GRAPH,
        ))
        with self.assertRaises(ValueError):
            replace(self.slice, storages=self.slice.storages + (bad,))

    def test_removed_reduction_is_rejected(self):
        errors = self.validate(replace(self.slice, reductions=self.slice.reductions[1:]))
        self.assertIn("slice reduction coverage/replay mismatch", errors)

    def test_reversed_reduction_direction_is_rejected(self):
        reductions = list(self.slice.reductions)
        reductions[0] = replace(reductions[0], direction="result_subset_source_behaviors")
        errors = self.validate(replace(self.slice, reductions=tuple(reductions)))
        self.assertIn("slice reduction has an unsound implication direction", errors)

    def test_removed_relevant_event_is_rejected(self):
        errors = self.validate(replace(self.slice, events=self.slice.events[1:]))
        self.assertIn("slice event relevance closure mismatch", errors)


if __name__ == "__main__":
    unittest.main()
