#!/usr/bin/env python3
"""Acceptance tests for the single model-argument OT Markov pipeline."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.ot_markov import (  # noqa: E402
    PROFILE,
    UnsupportedOTProfile,
    compile_ot_model,
    prove_markov,
)


MODELS = {
    "thermostat": ROOT / "src/clarity/models/thermostat/model.sysml",
    "mixing": ROOT / "src/clarity/models/mixing-sysml-model/model.sysml",
    "cruise": ROOT / "tests/fixtures/standalone/cruise-controller-model.sysml",
}
EXPECTED = {
    "thermostat": (0, 1),
    "mixing": (0, 0),
    "cruise": (0, 1),
}
HAS_Z3 = importlib.util.find_spec("z3") is not None


def _remove_neural_requirement(source: str) -> str:
    start = source.index("        #NeuralRequirement requirement def")
    end = source.index("\n\n        action step {", start)
    return source[:start] + source[end + 2:]


class OTMarkovPipelineTests(unittest.TestCase):
    def test_one_compiler_derives_all_three_candidates(self):
        for name, path in MODELS.items():
            with self.subTest(name=name):
                compiled = compile_ot_model(path)
                self.assertEqual(compiled.summary()["profile"], PROFILE)
                self.assertEqual(
                    (compiled.candidate.b_obs, compiled.candidate.b_act),
                    EXPECTED[name],
                )
                self.assertTrue(compiled.candidate.evidence)
                self.assertTrue(compiled.dependency_witness)

    def test_cruise_fixture_is_exact_standalone_blob(self):
        data = MODELS["cruise"].read_bytes()
        blob = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()  # nosec B324
        self.assertEqual(blob, "a789060d978dd52331f7b1f9485233518fa63e6a")
        self.assertEqual(
            hashlib.sha256(data).hexdigest(),
            "3fd949d55c97de73946be48f803f982eecbb2fee78e568843d77ef4964403254",
        )

    def test_noninvertible_observation_is_rejected(self):
        source = MODELS["mixing"].read_text()
        changed = source.replace(
            "volume1Res.response - toleranceMl",
            "volume1Res.response * volume1Res.response",
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.sysml"
            path.write_text(changed)
            with self.assertRaisesRegex(
                UnsupportedOTProfile, "not an invertible affine sample"
            ):
                compile_ot_model(path)

    def test_pipeline_has_no_model_dispatch_or_simulator_dependency(self):
        source = (ROOT / "src/clarity/certification/ot_markov.py").read_text()
        self.assertNotIn("thermostat_symbolic_machine", source)
        self.assertNotIn("mixing_symbolic_machine", source)
        self.assertNotIn("clarity.sysml.simulator", source)
        self.assertNotIn("package_name ==", source)
        for package_name in ("Thermostat", "TankFillingSystem", "CruiseControl"):
            self.assertNotIn(package_name, source)

    @unittest.skipUnless(HAS_Z3, "pinned z3-solver unavailable")
    def test_one_entry_point_certifies_all_three_models(self):
        for name, path in MODELS.items():
            with self.subTest(name=name):
                result = prove_markov(path)
                self.assertEqual(
                    result["classification"], "CERTIFIED_UNDER_PROFILE", result
                )
                candidate = result["candidate"]
                self.assertEqual(
                    (candidate["b_obs"], candidate["b_act"]), EXPECTED[name]
                )
                self.assertTrue(all(
                    value == "unsat" for value in result["obligations"].values()
                ))

    @unittest.skipUnless(HAS_Z3, "pinned z3-solver unavailable")
    def test_z3_backend_certifies_identity_execution_without_shield(self):
        source = _remove_neural_requirement(MODELS["thermostat"].read_text())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.sysml"
            path.write_text(source)
            result = prove_markov(path)
        self.assertEqual(result["classification"], "CERTIFIED_UNDER_PROFILE", result)
        self.assertEqual(result["model"]["action_execution"], "identity")
        self.assertTrue(all(
            value == "unsat" for value in result["obligations"].values()
        ))


if __name__ == "__main__":
    unittest.main()
