#!/usr/bin/env python3
"""Focused validation for the direct thermostat symbolic-machine prototype."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.thermostat_symbolic_machine import (  # noqa: E402
    APPROVED_SOURCE_SHA256,
    DEFAULT_MODEL_PATH,
    MAX_SMT2_BYTES,
    RESULT_SCHEMA,
    build_fixed_buffer_markov_query,
    extract_thermostat_relation,
    validate_thermostat_source,
)


HAS_Z3 = importlib.util.find_spec("z3") is not None


class ThermostatSymbolicMachineTests(unittest.TestCase):
    def test_source_profile_validates_without_solver(self):
        source_hash = validate_thermostat_source()
        self.assertEqual(source_hash, APPROVED_SOURCE_SHA256)

    def test_thermal_equation_drift_is_rejected_without_solver(self):
        source = DEFAULT_MODEL_PATH.read_text()
        changed = source.replace(
            "heatLossCoefficientWattsPerCelcius * (temperatureCelcius - outsideTemperatureCelcius)",
            "11 * (temperatureCelcius - outsideTemperatureCelcius)",
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / "model.sysml"
            model.write_text(changed)
            with self.assertRaisesRegex(ValueError, "approved thermostat source hash"):
                validate_thermostat_source(model)

    def test_prototype_does_not_import_legacy_certification_or_simulator(self):
        source = (ROOT / "src" / "clarity" / "certification" / "thermostat_symbolic_machine.py").read_text()
        self.assertNotIn("from .markov_", source)
        self.assertNotIn("clarity.runtime", source)
        self.assertNotIn("clarity.sysml.simulator", source)
        self.assertNotIn("continue_relation", source)
        # A source-profile validator is part of the soundness boundary.  Keep
        # the whole prototype below this hard limit rather than growing a new
        # framework around it.
        self.assertLessEqual(len(source.splitlines()), 720)

    @unittest.skipUnless(HAS_Z3, "pinned z3-solver unavailable")
    def test_fixed_buffer_query_is_compact_and_certifies_under_contract(self):
        relation = extract_thermostat_relation()
        self.assertEqual(relation.summary()["result_schema"], RESULT_SCHEMA)
        self.assertEqual(len(relation.properties), 3)
        self.assertTrue(all(item.role == "monitored" for item in relation.properties))
        query = build_fixed_buffer_markov_query()
        self.assertLessEqual(len(query.smt2().encode("utf-8")), MAX_SMT2_BYTES)
        result = query.run()
        self.assertEqual(result["classification"], "CERTIFIED_UNDER_CONTRACT", result)


if __name__ == "__main__":
    unittest.main()
