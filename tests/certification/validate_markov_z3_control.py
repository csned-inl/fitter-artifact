#!/usr/bin/env python3
"""Control-relation lowering, mutation, and pinned-Z3 checks."""

from __future__ import annotations

from dataclasses import replace
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_extract import extract_thermostat_markov_ir
from clarity.certification.markov_interval import derive_thermostat_decision_interval
from clarity.certification.markov_z3 import SolverStatus, UnsupportedLoweringError, run_smt2_query
from clarity.certification.markov_z3_control import compile_control_relation


HAS_Z3 = importlib.util.find_spec("z3") is not None


class ControlRelationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.interval = derive_thermostat_decision_interval(
            extract_thermostat_markov_ir()
        )

    def test_complete_deterministic_symbol_inventory(self):
        first = compile_control_relation(self.interval, namespace="fixture")
        second = compile_control_relation(self.interval, namespace="fixture")
        self.assertEqual(first, second)
        self.assertEqual(len(first.state_symbols), 132)
        self.assertEqual(len(first.edge_symbols), 281)
        # Three outcome constructors appear under nine finite return-stack
        # configurations; the control relation distinguishes every state.
        self.assertEqual(len(first.exit_symbols), 9)
        self.assertEqual(len({name for _, name in first.state_symbols}), 132)
        self.assertEqual(len({name for _, name in first.edge_symbols}), 281)

    def test_every_edge_is_guarded_by_its_source(self):
        encoding = compile_control_relation(self.interval, namespace="guard")
        for edge, edge_name in encoding.edge_symbols:
            source_name = dict(encoding.state_symbols)[edge.source]
            self.assertIn(f"(=> {edge_name} {source_name})", encoding.assertions)

    def test_mutated_back_edge_fails_closed(self):
        edge = self.interval.edges[0]
        backwards = replace(edge, source=edge.target, target=edge.source)
        mutated = replace(
            self.interval,
            edges=(backwards,) + self.interval.edges[1:],
        )
        with self.assertRaises(UnsupportedLoweringError):
            compile_control_relation(mutated)

    @unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
    def test_relation_is_satisfiable(self):
        encoding = compile_control_relation(self.interval, namespace="sat")
        result = run_smt2_query(encoding.smt2(), timeout_ms=30_000)
        self.assertIs(result.status, SolverStatus.SAT, result.reason)

    @unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
    def test_some_first_outcome_is_forced(self):
        encoding = compile_control_relation(self.interval, namespace="progress")
        no_exit = f"(not (or {' '.join(encoding.exit_symbols)}))"
        result = run_smt2_query(encoding.smt2(no_exit), timeout_ms=30_000)
        self.assertIs(result.status, SolverStatus.UNSAT, result.reason)

    @unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
    def test_multiple_first_outcomes_are_forbidden(self):
        encoding = compile_control_relation(self.interval, namespace="exclusive")
        pairs = [
            f"(and {encoding.exit_symbols[left]} {encoding.exit_symbols[right]})"
            for left in range(len(encoding.exit_symbols))
            for right in range(left + 1, len(encoding.exit_symbols))
        ]
        result = run_smt2_query(
            encoding.smt2(f"(or {' '.join(pairs)})"), timeout_ms=30_000
        )
        self.assertIs(result.status, SolverStatus.UNSAT, result.reason)


if __name__ == "__main__":
    unittest.main()
