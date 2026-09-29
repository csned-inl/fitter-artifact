"""Positive and adversarial tests for source values and held/physical separation."""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

from clarity.certification.equations import Const, Equation, EquationModel, Op, Var
from clarity.certification.strict_extract import CertificationExtractor
from clarity.discretization.analysis import analyze_model
from clarity.discretization.model.physical_reduction import _expand_post_update
from clarity.discretization.model.proof_rules import expand_definitions, substitute
from clarity.discretization.certificates.verification.source_reconstruction import (
    _expand_post_update as verify_post_update,
)
from clarity.sysml.parser import SysMLParser
from clarity.sysml.simulator import SimulationEngine, resolve_value
from clarity.models import models_root
from clarity.certification.certificate import build_certificate_for_path, check_certificate
from clarity.discretization.certificates.verification.source import verify_analysis_source
from clarity.discretization.timing import canonical_dt


class ValueStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.positive_path = Path(__file__).parent / "fixtures" / "held_constant.sysml"
        cls.positive_certificate = build_certificate_for_path(str(cls.positive_path), dt=.1)
        cls.positive_analysis = analyze_model(str(cls.positive_path), cls.positive_certificate, dt_text="0.1")

    def test_valid_paired_model_certifies_and_source_replays(self):
        self.assertEqual(self.positive_certificate["result"], "PASS")
        self.assertEqual(check_certificate(self.positive_certificate), [])
        self.assertEqual(self.positive_analysis["result"], "CERTIFIED")
        self.assertEqual(verify_analysis_source(str(self.positive_path), self.positive_certificate,
                                               canonical_dt("0.1"), self.positive_analysis), [])

    def test_existing_mutations_against_a_valid_paired_certificate(self):
        # An invalid base certificate would make rejection tests meaningless.
        self.assertEqual(check_certificate(self.positive_certificate), [])
        sys.path.insert(0, str(Path(__file__).parents[1] / "certification"))
        from battery.mutation_cases import run_mutation_cases
        from battery.support import Battery
        with tempfile.TemporaryDirectory() as directory:
            battery = Battery(Path(directory))
            run_mutation_cases(battery, self.positive_certificate)
            self.assertEqual(battery.failures, [])

    def test_markov_checker_rejects_swapped_value_roles(self):
        certificate = copy.deepcopy(self.positive_certificate)
        pair = certificate["value_semantics"]["state_value_pairs"][0]
        pair["physical_value"], pair["sampled_value"] = pair["sampled_value"], pair["physical_value"]
        self.assertTrue(any("value semantics" in e for e in check_certificate(certificate)))

    def test_saved_pipeline_artifacts_preserve_both_values(self):
        from clarity.certification.certificate import write_certificate
        from clarity.certification.reduced_mdp_spec import (
            build_reduced_mdp_spec, write_reduced_mdp_spec, check_reduced_mdp_spec, spec_hash)
        from clarity.discretization.certificates.generation import build_certificate
        from clarity.discretization.certificates.checker import verify_certificate
        from clarity.discretization.certificates.io import certificate_hash
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_certificate(self.positive_certificate, root / "markov.json")
            spec = build_reduced_mdp_spec(
                str(self.positive_path), certificate=self.positive_certificate,
                certificate_path=root / "markov.json", dt=.1)
            write_reduced_mdp_spec(spec, root / "spec.json")
            self.assertEqual(check_reduced_mdp_spec(spec), [])
            safety = build_certificate(self.positive_path, root / "markov.json",
                                       root / "spec.json", dt_text="0.1")
            self.assertTrue(verify_certificate(safety)["safety_certified"])
            # Recompute hashes: rejection must concern meaning, not stale hashes.
            safety["analysis"]["value_semantics"]["state_value_pairs"] = []
            safety["self_sha256"] = certificate_hash(safety)
            self.assertFalse(verify_certificate(safety)["safety_certified"])
            spec["value_semantics"]["state_value_pairs"] = []
            spec["self_sha256"] = spec_hash(spec)
            self.assertTrue(check_reduced_mdp_spec(spec))

    def test_interval_checker_rejects_reintroduced_alias(self):
        analysis = copy.deepcopy(self.positive_analysis)
        reduction = analysis["properties"][0]["reduction"]
        reduction["sensor_to_physical_mappings"] = [{
            "sampled_value": "controller_sampled", "physical_value": "controller_physical"}]
        errors = verify_analysis_source(str(self.positive_path), self.positive_certificate,
                                        canonical_dt("0.1"), analysis)
        self.assertTrue(any("forbidden sample-to-current substitution" in e for e in errors))

    def test_interval_checker_rejects_missing_pair(self):
        analysis = copy.deepcopy(self.positive_analysis)
        analysis["value_semantics"]["state_value_pairs"] = []
        errors = verify_analysis_source(str(self.positive_path), self.positive_certificate,
                                        canonical_dt("0.1"), analysis)
        self.assertTrue(any("value semantics" in e for e in errors))

    def test_false_source_requirement_never_certifies(self):
        text = self.positive_path.read_text().replace(
            "s.controller.sampled < 0.0 implies s.controller.active", "false")
        parser = self.fixture(text)
        certificate = build_certificate_for_path(parser.file_path, dt=.1)
        analysis = analyze_model(parser.file_path, certificate, dt_text="0.1")
        self.assertNotEqual(analysis["result"], "CERTIFIED")

    def fixture(self, source):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "model.sysml"
        path.write_text(source)
        parser = SysMLParser(str(path))
        parser.parse()
        return parser

    def test_initial_value_and_live_binding_have_different_updates(self):
        parser = self.fixture('''package Example {
          part def System {
            attribute x : Real := 2;
            attribute sampled : Real := x;
            attribute bound : Real = x * 2;
            action step { in dt : Real; assign x := x + 1; }
          }
          part system : System;
        }''')
        engine = SimulationEngine(parser); engine.initialize()
        self.assertEqual(resolve_value(engine.state, "system::sampled"), 2)
        self.assertEqual(resolve_value(engine.state, "system::bound"), 4)
        engine.step(.1)
        self.assertEqual(resolve_value(engine.state, "system::x"), 3)
        self.assertEqual(resolve_value(engine.state, "system::sampled"), 2)
        self.assertEqual(resolve_value(engine.state, "system::bound"), 6)
        engine.solver.solve(engine.current_sm_state)
        self.assertEqual(resolve_value(engine.state, "system::sampled"), 2)

    def test_bound_mutable_declaration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "assignment to bound feature"):
            self.fixture('''package Example { part def System {
              attribute clock : Real = 0;
              action step { in dt : Real; assign clock := clock + dt; }
            } part system : System; }''')

    def test_initial_dependency_order_does_not_change_values(self):
        parser = self.fixture('''package Example { part def System {
            attribute a : Real := b + 1;
            attribute b : Real := 2;
        } part system : System; }''')
        engine = SimulationEngine(parser); engine.initialize()
        self.assertEqual(resolve_value(engine.state, "system::a"), 3)

    def test_bound_local_value_cannot_be_overwritten(self):
        with self.assertRaisesRegex(ValueError, "assignment to bound feature"):
            self.fixture('''package Example { part def System {
                action step { in dt : Real;
                    attribute sample : Real = dt;
                    assign sample := 2;
                }
            } part system : System; }''')

    def test_assignment_through_alias_cannot_overwrite_binding(self):
        with self.assertRaisesRegex(ValueError, "assignment to bound feature"):
            self.fixture('''package Example {
                part def Plant {
                    attribute fixed : Real = 2;
                    attribute alias : Real;
                    bind alias = fixed;
                    action step { in dt : Real; assign alias := 3; }
                }
                part def System { part plant : Plant; }
                part system : System;
            }''')

    def test_cyclic_initial_values_are_rejected(self):
        parser = self.fixture('''package Example { part def System {
            attribute a : Real := b;
            attribute b : Real := a;
        } part system : System; }''')
        with self.assertRaisesRegex(ValueError, "cyclic initial values"):
            SimulationEngine(parser).initialize()

    def test_cycle_in_live_binding_is_rejected(self):
        parser = self.fixture('''package Example { part def System {
            attribute a : Real = b;
            attribute b : Real = a;
        } part system : System; }''')
        engine = SimulationEngine(parser); engine.initialize()
        with self.assertRaisesRegex(ValueError, "cyclic source binding"):
            resolve_value(engine.state, "system::a")

    def test_held_value_is_not_its_current_or_transformed_source(self):
        x, sampled = Var("x"), Var("sampled")
        model = EquationModel("fixture", {"x", "sampled"}, set())
        model.transitions["sampled"] = Equation("sampled", Op("*", (Const(2), x)), "transition")
        model.definitions["observed"] = Equation("observed", sampled, "definition")
        model.sampled_state.add("sampled")
        self.assertEqual(expand_definitions(model, Var("observed")), sampled)
        for expand in (_expand_post_update, verify_post_update):
            value = expand(model, Var("observed"), {"x"})
            self.assertEqual(value, sampled)
            self.assertEqual(substitute(value, {"x": Const(99)}), sampled)

    def test_bundled_source_declarations_and_pairs(self):
        expected_observations = {
            "thermostat": {"controller_reading_temperatureCelcius"},
            "cruise-controller-model": {"controller_reading_speedMps", "controller_reading_gapMeters"},
            "mixing-sysml-model": {"controller_volume1Res_response", "controller_volume2Res_response"},
        }
        for path in sorted(Path(models_root()).glob("*/model.sysml")):
            with self.subTest(model=path.parent.name):
                extractor = CertificationExtractor(str(path)); model = extractor.extract()
                self.assertFalse(model.value_semantics["implicit_sample_to_physical_equality"])
                self.assertTrue(model.state_value_pairs)
                observations = set().union(*(eq.refs() for eq in model.observations.values()))
                self.assertTrue(expected_observations[path.parent.name] <= observations)
                for pair in model.state_value_pairs:
                    self.assertNotEqual(pair["physical_value"], pair["sampled_value"])
                    self.assertIn(pair["physical_value"], model.state)
                    self.assertIn(pair["sampled_value"], model.state)
                    self.assertTrue(pair["physical_runtime_key"])
                    self.assertTrue(pair["sampled_runtime_key"])
                engine = SimulationEngine(extractor.parser); engine.initialize()
                # This test exercises storage separation, not controller safety.
                neural = extractor.neural_action_definition()
                engine.model = lambda inputs: {p.name: False for p in neural.out_params}
                engine.step(.1)
                for pair in model.state_value_pairs:
                    key = pair["sampled_runtime_key"]
                    before = resolve_value(engine.state, key)
                    physical_key = pair["physical_runtime_key"]
                    engine.state[physical_key] = resolve_value(engine.state, physical_key) + 10
                    self.assertEqual(resolve_value(engine.state, key), before)
                snapshot = engine.state_value_pairs()
                self.assertEqual(len(snapshot), len(model.state_value_pairs))

    def test_observation_binding_tracks_sensor_without_a_solve(self):
        path = Path(models_root()) / "cruise-controller-model" / "model.sysml"
        parser = SysMLParser(str(path)); parser.parse()
        engine = SimulationEngine(parser); engine.initialize()
        engine.state["system::vehicle::speedMps"] = 99
        engine.state["system::speedSensor::lastReadingMps"] = 12
        self.assertEqual(resolve_value(engine.state, "system::lastObservedSpeed"), 12)
        engine.state["system::speedSensor::lastReadingMps"] = 13
        self.assertEqual(resolve_value(engine.state, "system::lastObservedSpeed"), 13)
        self.assertEqual(resolve_value(engine.state, "system::vehicle::speedMps"), 99)

    def test_original_requirement_uses_held_reading(self):
        path = Path(models_root()) / "cruise-controller-model" / "model.sysml"
        parser = SysMLParser(str(path)); parser.parse()
        engine = SimulationEngine(parser); engine.initialize()
        engine.state.update({"system::currentTime": 1,
                             "system::vehicle::speedMps": 99,
                             "system::speedSensor::lastReadingMps": 12,
                             "system::speedSensor::lastGapMeters": 100,
                             "system::controller::throttleOn": False})
        self.assertFalse(engine.requirement_statuses()["Accelerate When Below Target"]["status"])

    def test_unreceived_message_is_unavailable_not_current_physics(self):
        path = Path(models_root()) / "cruise-controller-model" / "model.sysml"
        parser = SysMLParser(str(path)); parser.parse()
        engine = SimulationEngine(parser); engine.initialize()
        received = [p for p in engine.state_value_pairs()
                    if p["sampled"]["state_variable"] == "controller_reading_speedMps"]
        self.assertEqual(len(received), 1)
        self.assertFalse(received[0]["sampled"]["available"])
        self.assertIsNone(received[0]["sampled"]["value"])

    def test_incomplete_event_composition_cannot_certify(self):
        for path in sorted(Path(models_root()).glob("*/model.sysml")):
            with self.subTest(model=path.parent.name):
                analysis = analyze_model(str(path), {"mdp_obligations": {"shield": {}}}, dt_text="0.1")
                self.assertEqual(analysis["result"], "NOT_CERTIFIED")
                self.assertTrue(analysis["value_semantics"]["state_value_pairs"])
                self.assertTrue(any("sample_event_composition_required" in d
                                    for d in analysis["blocking_diagnostics"]))


def validate_value_states():
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ValueStateTests))
    if not result.wasSuccessful():
        raise AssertionError("value-state validation failed")


if __name__ == "__main__":
    validate_value_states()
