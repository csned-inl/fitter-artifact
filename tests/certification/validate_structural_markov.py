#!/usr/bin/env python3
"""Validation for the one-way structural Markov fast path."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.structural_markov import (  # noqa: E402
    prove_markov_with_fallback,
    try_prove_markov_structurally,
)


MODELS = {
    "thermostat": ROOT / "src/clarity/models/thermostat/model.sysml",
    "mixing": ROOT / "src/clarity/models/mixing-sysml-model/model.sysml",
    "cruise": ROOT / "tests/fixtures/standalone/cruise-controller-model.sysml",
}
EXPECTED = {
    "thermostat": {"b_obs": 0, "b_act": 1},
    "mixing": {"b_obs": 0, "b_act": 0},
    "cruise": {"b_obs": 0, "b_act": 1},
}


def _remove_neural_requirement(source: str) -> str:
    start = source.index("        #NeuralRequirement requirement def")
    end = source.index("\n\n        action step {", start)
    return source[:start] + source[end + 2:]


class StructuralMarkovTests(unittest.TestCase):
    def test_all_three_models_receive_positive_structural_certificates(self):
        for name, path in MODELS.items():
            with self.subTest(name=name):
                certificate = try_prove_markov_structurally(path)
                self.assertIsNotNone(certificate)
                assert certificate is not None
                self.assertEqual(certificate.buffer, EXPECTED[name])
                self.assertTrue(certificate.dependency_witness)
                self.assertTrue(certificate.reconstruction_evidence)
                self.assertTrue(certificate.discharged_obligations)

    def test_fast_path_does_not_execute_z3(self):
        with patch(
            "clarity.certification.structural_markov.prove_markov",
            side_effect=AssertionError("semantic fallback must not execute"),
        ):
            for path in MODELS.values():
                result = prove_markov_with_fallback(path)
                self.assertEqual(result["classification"], "CERTIFIED_STRUCTURALLY")

    def test_absent_shield_uses_identity_execution_relation(self):
        source = _remove_neural_requirement(MODELS["thermostat"].read_text())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.sysml"
            path.write_text(source)
            certificate = try_prove_markov_structurally(path)
            self.assertIsNotNone(certificate)
            assert certificate is not None
            self.assertEqual(certificate.action_execution, "identity")
            self.assertEqual(certificate.action_definitions, ())
            with patch(
                "clarity.certification.structural_markov.prove_markov",
                side_effect=AssertionError("identity fast path must not execute Z3"),
            ):
                result = prove_markov_with_fallback(path)
            self.assertEqual(result["classification"], "CERTIFIED_STRUCTURALLY")
            self.assertEqual(
                result["certificate"]["action_execution"], "identity"
            )

    def test_negative_tolerance_is_not_unsoundly_certified(self):
        source = MODELS["thermostat"].read_text()
        changed = source.replace(
            "attribute :>> toleranceCelcius = 1;",
            "attribute :>> toleranceCelcius = -1;",
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.sysml"
            path.write_text(changed)
            self.assertIsNone(try_prove_markov_structurally(path))

    def test_zero_tolerance_boundary_overlap_is_not_certified(self):
        source = MODELS["thermostat"].read_text()
        changed = source.replace(
            "attribute :>> toleranceCelcius = 1;",
            "attribute :>> toleranceCelcius = 0;",
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.sysml"
            path.write_text(changed)
            self.assertIsNone(try_prove_markov_structurally(path))

    def test_scenario_varying_tolerance_is_not_treated_as_its_default(self):
        source = MODELS["thermostat"].read_text()
        changed = source.replace(
            "attribute :>> toleranceCelcius = 1;",
            "#ScenarioInput attribute :>> toleranceCelcius = 1;",
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.sysml"
            path.write_text(changed)
            self.assertIsNone(try_prove_markov_structurally(path))

    def test_unrecognized_residual_constraint_passes_to_fallback(self):
        source = MODELS["thermostat"].read_text()
        changed = source.replace(
            "not (p.heaterState and p.acState)",
            "(p.heaterState or p.acState)",
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.sysml"
            path.write_text(changed)
            sentinel = {"classification": "FALLBACK_CALLED"}
            with patch(
                "clarity.certification.structural_markov.prove_markov",
                return_value=sentinel,
            ) as fallback:
                self.assertEqual(prove_markov_with_fallback(path), sentinel)
                fallback.assert_called_once()

    def test_unrecognized_observation_reconstruction_passes_through(self):
        source = MODELS["mixing"].read_text()
        changed = source.replace(
            "volume1Res.response - toleranceMl",
            "volume1Res.response * volume1Res.response",
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.sysml"
            path.write_text(changed)
            self.assertIsNone(try_prove_markov_structurally(path))

    def test_checker_has_no_solver_simulator_or_model_dispatch(self):
        source = (
            ROOT / "src/clarity/certification/structural_markov.py"
        ).read_text()
        self.assertNotIn("import z3", source)
        self.assertNotIn("clarity.sysml.simulator", source)
        self.assertNotIn("package_name ==", source)
        for package_name in ("Thermostat", "TankFillingSystem", "CruiseControl"):
            self.assertNotIn(package_name, source)


if __name__ == "__main__":
    unittest.main()
