#!/usr/bin/env python3
"""Soundness-oriented tests for direct structural discretization safety."""

from __future__ import annotations

import importlib.util
import hashlib
from pathlib import Path
import tempfile
import unittest
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.discretization_semantic_validation import (  # noqa: E402
    validate_structural_discretization_semantically,
)
from clarity.certification.ot_markov import compile_ot_model  # noqa: E402
from clarity.certification.structural_discretization import (  # noqa: E402
    try_prove_discretization_structurally,
)


MODELS = {
    "thermostat": ROOT / "src/clarity/models/thermostat/model.sysml",
    "mixing": ROOT / "src/clarity/models/mixing-sysml-model/model.sysml",
    "cruise": ROOT / "tests/fixtures/standalone/cruise-controller-model.sysml",
}
EXPECTED_PROPERTIES = {
    "thermostat": {
        "No Simultaneous Heating and Cooling",
        "Heat When Cold",
        "Cool When Hot",
    },
    "mixing": {
        "No Dry Running",
        "No Dead Heading",
        "Fluid Transfer Termination Safety",
        "Fluid Transfer Liveness",
    },
    "cruise": {
        "No Simultaneous Throttle and Brake",
        "No Throttle When Too Close",
        "No Throttle When Overspeeding",
        "Accelerate When Below Target",
        "Brake When Above Target Or Too Close",
    },
}
HAS_Z3 = importlib.util.find_spec("z3") is not None
SOURCE_IDENTITIES = {
    "thermostat": (
        "820df2a56af2d3a9fe54855e15a4da034ae3750b",
        "44dec2333511c5cf7a40ad1828b612a043f9c7c71f6b960c276ce866f4a521aa",
    ),
    "mixing": (
        "20ef2ed6dd55ab083ce8b1a403beab17c58dbdff",
        "09d59723435da435325299934ed8472bbac3e89c6d9840df892b9f7e1c912f8f",
    ),
    "cruise": (
        "a789060d978dd52331f7b1f9485233518fa63e6a",
        "3fd949d55c97de73946be48f803f982eecbb2fee78e568843d77ef4964403254",
    ),
}


def _certificate_for_text(source: str):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "model.sysml"
        path.write_text(source)
        return try_prove_discretization_structurally(path)


class StructuralDiscretizationTests(unittest.TestCase):
    def test_model_fixtures_match_reviewed_standalone_blobs(self):
        for name, path in MODELS.items():
            with self.subTest(name=name):
                data = path.read_bytes()
                blob = hashlib.sha1(
                    f"blob {len(data)}\0".encode() + data
                ).hexdigest()  # nosec B324 -- Git object identity
                self.assertEqual(blob, SOURCE_IDENTITIES[name][0])
                self.assertEqual(
                    hashlib.sha256(data).hexdigest(), SOURCE_IDENTITIES[name][1]
                )

    def test_all_literal_safety_requirements_are_certified(self):
        for name, path in MODELS.items():
            with self.subTest(name=name):
                certificate = try_prove_discretization_structurally(path)
                self.assertIsNotNone(certificate)
                assert certificate is not None
                self.assertEqual(
                    {item.name for item in certificate.properties},
                    EXPECTED_PROPERTIES[name],
                )
                self.assertTrue(all(item.mappings for item in certificate.properties))
                self.assertTrue(all(item.held_symbols for item in certificate.properties))

    def test_mixing_actuator_effects_are_derived_from_command_payloads(self):
        model = compile_ot_model(MODELS["mixing"])
        effects = model.dependency_model.action_effects
        self.assertEqual(
            repr(effects["pump1_isRunning"]),
            "RefExpr(path=['policyCall', 'shouldTurnOnPump1'])",
        )
        self.assertIn("shouldOpenValve1", repr(effects["valve1_isOpen"]))
        self.assertIn("shouldTurnOnPump1", repr(effects["valve1_isOpen"]))

    def test_inverted_actuator_receive_assignment_is_not_certified(self):
        source = MODELS["mixing"].read_text().replace(
            "assign isRunning := coilWriteRequest.coilValue;",
            "assign isRunning := not coilWriteRequest.coilValue;",
            1,
        )
        self.assertIsNone(_certificate_for_text(source))

    def test_wrong_on_command_payload_is_not_certified(self):
        source = MODELS["mixing"].read_text().replace(
            "assign pump1CoilReq.coilValue := true;",
            "assign pump1CoilReq.coilValue := false;",
            1,
        )
        self.assertIsNone(_certificate_for_text(source))

    def test_wrong_off_command_payload_is_not_certified(self):
        source = MODELS["mixing"].read_text().replace(
            "assign pump1CoilReq.coilValue := false;",
            "assign pump1CoilReq.coilValue := true;",
            1,
        )
        self.assertIsNone(_certificate_for_text(source))

    def test_command_rejected_by_actuator_guard_is_not_certified(self):
        source = MODELS["mixing"].read_text().replace(
            "assign pump1CoilReq.coilAddress := 0;",
            "assign pump1CoilReq.coilAddress := 1;",
            1,
        )
        self.assertIsNone(_certificate_for_text(source))

    def test_actuator_command_outside_policy_cycle_is_not_ignored(self):
        source = MODELS["mixing"].read_text().replace(
            "perform action ScanCycle;",
            "perform action ScanCycle;\n                perform action turnOnPump1;",
            1,
        )
        self.assertIsNone(_certificate_for_text(source))

    def test_missing_scenario_inequality_is_not_silently_assumed(self):
        source = MODELS["mixing"].read_text().replace(
            "controller.tank1OriginalLevelMl >= controller.tank1TransferMl and",
            "controller.tank1OriginalLevelMl <= controller.tank1TransferMl and",
            1,
        )
        self.assertIsNone(_certificate_for_text(source))

    def test_unmapped_physical_property_passes_through(self):
        source = MODELS["mixing"].read_text().replace(
            "s.controller.observedLevel1 <= 0",
            "s.feederTank1.currentLevelMl <= 0",
            1,
        )
        self.assertIsNone(_certificate_for_text(source))

    def test_false_source_requirement_is_not_certified(self):
        source = MODELS["thermostat"].read_text().replace(
            "not (s.controller.heaterOn and s.controller.acOn)",
            "s.controller.heaterOn and s.controller.acOn",
            1,
        )
        self.assertIsNone(_certificate_for_text(source))

    def test_structural_checker_has_no_solver_or_simulator_dependency(self):
        source = (
            ROOT / "src/clarity/certification/structural_discretization.py"
        ).read_text()
        self.assertNotIn("import z3", source)
        self.assertNotIn("clarity.sysml.simulator", source)
        self.assertNotIn("package_name ==", source)
        for package_name in ("Thermostat", "TankFillingSystem", "CruiseControl"):
            self.assertNotIn(package_name, source)

    @unittest.skipUnless(HAS_Z3, "pinned z3-solver unavailable")
    def test_z3_refutes_every_structural_property_counterexample(self):
        for name, path in MODELS.items():
            with self.subTest(name=name):
                result = validate_structural_discretization_semantically(path)
                self.assertEqual(result["classification"], "VALIDATED", result)
                self.assertTrue(result["obligations"])
                self.assertTrue(all(
                    item["result"] == "unsat" for item in result["obligations"]
                ))


if __name__ == "__main__":
    unittest.main()
