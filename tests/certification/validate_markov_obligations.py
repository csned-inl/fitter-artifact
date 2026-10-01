#!/usr/bin/env python3
"""Mutation tests for the exhaustive pre-SMT obligation manifest."""

from __future__ import annotations

from dataclasses import replace
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_extract import extract_thermostat_markov_ir
from clarity.certification.markov_interval import derive_thermostat_decision_interval
from clarity.certification.markov_metrics import FormulaBudget, check_formula_budget, formula_metrics
from clarity.certification.markov_obligations import (
    EqualitySemantics,
    ObligationStatus,
    build_obligation_manifest,
    validate_obligation_manifest,
)
from clarity.certification.markov_slice import build_thermostat_relevance_slice


class ThermostatObligationManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir = extract_thermostat_markov_ir()
        cls.interval = derive_thermostat_decision_interval(cls.ir)
        cls.slice = build_thermostat_relevance_slice(cls.ir, cls.interval)

    def manifest(self, b_obs=2, b_act=1):
        return build_obligation_manifest(
            self.slice, self.interval, b_obs=b_obs, b_act=b_act
        )

    def test_complete_manifest_is_valid_but_not_a_certificate(self):
        manifest = self.manifest()
        self.assertEqual(validate_obligation_manifest(
            manifest, self.slice, self.interval), [])
        self.assertFalse(manifest.certificate_ready)
        self.assertEqual([case.name for case in manifest.history_cases], [
            "reset_prefix_0", "reset_prefix_1", "steady_state",
        ])
        # 16 solver-visible differences + 3 progress/representation queries,
        # for each of three complete history cases.
        self.assertEqual(len(manifest.queries), 57)
        self.assertEqual(len(manifest.structural_obligations), 3)
        self.assertTrue(all(item.status is ObligationStatus.NOT_RUN
                            for item in manifest.structural_obligations))

    def test_visible_signature_is_covered_exactly(self):
        manifest = self.manifest()
        visible = {term.name for term in self.slice.visible_terms}
        covered = {term for predicate in manifest.difference_predicates
                   for term in predicate.visible_terms}
        self.assertEqual(covered, visible)
        self.assertEqual(len(manifest.difference_predicates), len(visible))

    def test_float_outputs_use_ieee_bit_equality(self):
        manifest = self.manifest()
        equalities = {predicate.visible_terms[0]: predicate.equality
                      for predicate in manifest.difference_predicates}
        for name in (
            "next_observation.setPoint.float32_bits",
            "next_observation.temperatureCelcius.float32_bits",
            "reward", "elapsed_time",
        ):
            self.assertIs(equalities[name], EqualitySemantics.IEEE_BITS)

    def test_removed_query_is_rejected(self):
        manifest = self.manifest()
        mutated = replace(manifest, queries=manifest.queries[1:])
        self.assertIn("solver query obligation coverage mismatch",
                      validate_obligation_manifest(mutated, self.slice, self.interval))

    def test_removed_structural_obligation_is_rejected(self):
        manifest = self.manifest()
        mutated = replace(
            manifest, structural_obligations=manifest.structural_obligations[1:]
        )
        self.assertIn("structural obligation coverage/replay mismatch",
                      validate_obligation_manifest(mutated, self.slice, self.interval))

    def test_changed_float_equality_is_rejected(self):
        manifest = self.manifest()
        predicates = list(manifest.difference_predicates)
        index = next(i for i, predicate in enumerate(predicates)
                     if predicate.visible_terms == ("reward",))
        predicates[index] = replace(predicates[index], equality=EqualitySemantics.NATIVE)
        errors = validate_obligation_manifest(
            replace(manifest, difference_predicates=tuple(predicates)),
            self.slice, self.interval,
        )
        self.assertIn("visible-difference predicate coverage/equality mismatch", errors)

    def test_unknown_timeout_error_or_sat_never_becomes_ready(self):
        for status in (ObligationStatus.UNKNOWN, ObligationStatus.TIMEOUT,
                       ObligationStatus.ERROR, ObligationStatus.SAT):
            manifest = self.manifest(0, 0)
            queries = tuple(replace(query, status=status,
                                    query_sha256="a" * 64, solver_identity="z3")
                            for query in manifest.queries)
            premises = tuple(replace(premise, status=ObligationStatus.CHECKED)
                             for premise in manifest.premises)
            mutated = replace(manifest, queries=queries, premises=premises)
            self.assertFalse(mutated.certificate_ready, status)

    def test_formula_metrics_and_default_budget(self):
        manifest = self.manifest()
        metrics = formula_metrics(self.slice, self.interval, manifest)
        self.assertEqual(metrics.history_length, 2)
        self.assertEqual(metrics.history_cases, 3)
        self.assertEqual(metrics.solver_queries, 57)
        self.assertEqual(metrics.structural_predicates, 3)
        self.assertEqual(metrics.retained_state_terms_per_run, 47)
        self.assertEqual(metrics.shared_buffer_slots, 10)
        self.assertEqual(metrics.generic_runtime_sorts, 0)
        self.assertTrue(check_formula_budget(metrics).accepted)

    def test_budget_exhaustion_fails_closed(self):
        metrics = formula_metrics(self.slice, self.interval, self.manifest())
        report = check_formula_budget(metrics, FormulaBudget(
            max_solver_queries=1,
            max_state_terms_per_query=1,
            max_event_instances_per_query=1,
            max_control_selectors_per_query=1,
        ))
        self.assertFalse(report.accepted)
        self.assertEqual(len(report.violations), 4)


if __name__ == "__main__":
    unittest.main()
