#!/usr/bin/env python3
"""Validation for direct analytical controller-class selection."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.controller_class_analysis import (  # noqa: E402
    analyze_controller_class,
)


MODELS = {
    "thermostat": ROOT / "src/clarity/models/thermostat/model.sysml",
    "mixing": ROOT / "src/clarity/models/mixing-sysml-model/model.sysml",
    "cruise": ROOT / "tests/fixtures/standalone/cruise-controller-model.sysml",
}


class ControllerClassAnalysisTests(unittest.TestCase):
    def test_all_three_models_are_analyzed_without_solver_or_simulator(self):
        reports = {name: analyze_controller_class(path)
                   for name, path in MODELS.items()}
        self.assertEqual(reports["thermostat"].buffer, {"b_obs": 0, "b_act": 1})
        self.assertEqual(reports["mixing"].buffer, {"b_obs": 0, "b_act": 0})
        self.assertEqual(reports["cruise"].buffer, {"b_obs": 0, "b_act": 1})
        self.assertEqual(
            reports["thermostat"].action_relation,
            "independent_or_aliased_affine_thresholds",
        )
        self.assertEqual(
            reports["mixing"].action_relation,
            "independent_or_aliased_affine_thresholds",
        )
        self.assertEqual(reports["cruise"].action_relation, "polyhedral_boolean_partition")
        self.assertTrue(all(
            report.action_uniqueness
            == "at_most_one_action_proved_by_source_definitional_equations"
            for report in reports.values()
        ))
        self.assertEqual(
            reports["mixing"].action_availability,
            "proved_by_total_source_definitions",
        )
        self.assertEqual(
            reports["thermostat"].action_availability,
            "requires_markov_shield_totality_obligation",
        )
        self.assertEqual(
            reports["cruise"].action_availability,
            "requires_markov_shield_totality_obligation",
        )
        self.assertIn("polynomial_degree_2", reports["cruise"].plant_dynamics)

    def test_analysis_is_a_separate_direct_source_module(self):
        source = (ROOT / "src/clarity/certification/controller_class_analysis.py").read_text()
        self.assertNotIn("clarity.sysml.simulator", source)
        self.assertNotIn("thermostat_symbolic_machine", source)
        self.assertNotIn("mixing_symbolic_machine", source)
        self.assertNotIn("import z3", source)
        self.assertNotIn("package_name ==", source)


if __name__ == "__main__":
    unittest.main()
