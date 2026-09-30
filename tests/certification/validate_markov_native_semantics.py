#!/usr/bin/env python3
"""Native-operation/interface contract gate and mutation tests."""

from __future__ import annotations

from copy import deepcopy
import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_contract import repository_root
from clarity.certification.markov_native_semantics import (
    load_native_semantics_contract,
    observation_float32_bits,
    phase2_outcome,
    thermostat_shield_action,
    validate_thermostat_native_semantics,
)
from clarity.runtime.shield import SpecShield


class NativeSemanticsTests(unittest.TestCase):
    def test_checked_contract_has_complete_slice_coverage(self):
        self.assertEqual(validate_thermostat_native_semantics(), [])

    def test_shield_equation_matches_runtime_at_regions_and_boundaries(self):
        model = repository_root() / "src/clarity/models/thermostat/model.sysml"
        shield = SpecShield(str(model))
        set_point = 18.3
        tolerance = 1.0
        for temperature in (10.0, 17.3, 17.3000001, 18.3, 19.2999999, 19.3, 30.0):
            raw = {
                "setPoint": set_point,
                "temperatureCelcius": temperature,
                "done": False,
            }
            for proposal in range(4):
                expected = thermostat_shield_action(
                    proposal, set_point=set_point,
                    temperature=temperature, tolerance=tolerance,
                )
                self.assertEqual(shield(proposal, raw), expected)

    def test_observation_encoding_matches_numpy_float32_bits(self):
        scale = load_native_semantics_contract()["observation"]["scale"]
        for value in (-10.0, 0.0, 13.0, 18.3, 23.9, 33.0, 50.0):
            expected = int(np.asarray([float(value) / scale], dtype=np.float32).view(np.uint32)[0])
            self.assertEqual(observation_float32_bits(value, scale), expected)

    def test_phase2_outcome_priority_is_exact(self):
        self.assertEqual(phase2_outcome(execution_error=True).constructor, "error")
        self.assertEqual(
            phase2_outcome(requirement_evaluation_error=True,
                           false_required_property=True,
                           intrinsic_terminal=True).reward,
            0.0,
        )
        self.assertEqual(
            phase2_outcome(false_required_property=True,
                           intrinsic_terminal=True).reward,
            -1.0,
        )
        success = phase2_outcome(intrinsic_terminal=True)
        self.assertEqual((success.constructor, success.reward), ("terminal", 1.0))
        running = phase2_outcome()
        self.assertEqual((running.constructor, running.reward), ("continue", -0.01))

    def test_source_hash_mutation_is_rejected(self):
        contract = deepcopy(load_native_semantics_contract())
        key = next(iter(contract["source_files"]))
        contract["source_files"][key] = "0" * 64
        self.assertTrue(any("source hash mismatch" in error
                            for error in validate_thermostat_native_semantics(contract)))

    def test_removed_operation_rule_is_rejected(self):
        contract = deepcopy(load_native_semantics_contract())
        contract["operation_rules"].pop("assign")
        self.assertIn(
            "native-semantics sliced-operation coverage mismatch",
            validate_thermostat_native_semantics(contract),
        )

    def test_changed_operation_rule_is_rejected(self):
        contract = deepcopy(load_native_semantics_contract())
        contract["operation_rules"]["assign"] = "mathematical_real_assignment"
        self.assertIn(
            "native-semantics operation rule mismatch",
            validate_thermostat_native_semantics(contract),
        )

    def test_observation_rounding_mutation_is_rejected(self):
        contract = deepcopy(load_native_semantics_contract())
        contract["observation"]["operation_order"] = "real_divide_without_rounding"
        self.assertIn(
            "native-semantics observation operation-order mismatch",
            validate_thermostat_native_semantics(contract),
        )

    def test_action_history_mutation_is_rejected(self):
        contract = deepcopy(load_native_semantics_contract())
        contract["shield"]["history_records"] = "proposed_action"
        self.assertIn(
            "native-semantics action-history convention mismatch",
            validate_thermostat_native_semantics(contract),
        )


if __name__ == "__main__":
    unittest.main()
