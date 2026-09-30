#!/usr/bin/env python3
"""Phase-4 solver-boundary fixtures; production lowering remains fail-closed."""

from __future__ import annotations

from dataclasses import replace
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_ir import NativeSort
from clarity.certification.markov_z3 import (
    QueryResult,
    SOLVER_PIPELINE,
    SolverStatus,
    UnsupportedLoweringError,
    canonical_smt2,
    compile_manifest_obligation,
    native_z3_sort,
    query_sha256,
    replay_query,
    run_smt2_query,
)


HAS_Z3 = importlib.util.find_spec("z3") is not None

DIRECT_OBSERVATION = """
(set-logic QF_LIA)
(declare-const observation_left Int)
(declare-const observation_right Int)
(declare-const action Int)
(declare-const next_left Int)
(declare-const next_right Int)
(assert (= observation_left observation_right))
(assert (= next_left (+ observation_left action)))
(assert (= next_right (+ observation_right action)))
(assert (not (= next_left next_right)))
"""

HIDDEN_STATE_COUNTEREXAMPLE = """
(set-logic QF_LIA)
(declare-const observation_left Int)
(declare-const observation_right Int)
(declare-const hidden_left Int)
(declare-const hidden_right Int)
(declare-const next_left Int)
(declare-const next_right Int)
(assert (= observation_left observation_right))
(assert (= next_left hidden_left))
(assert (= next_right hidden_right))
(assert (not (= next_left next_right)))
"""


class SolverIndependentBoundaryTests(unittest.TestCase):
    def test_solver_pipeline_is_fixed_and_evidence_visible(self):
        self.assertEqual(
            SOLVER_PIPELINE,
            ("simplify", "propagate-values", "solve-eqs", "smt"),
        )

    def test_query_hash_is_whitespace_stable_at_line_ends(self):
        mutated = "\n".join(line + "   " for line in DIRECT_OBSERVATION.splitlines())
        self.assertEqual(query_sha256(DIRECT_OBSERVATION), query_sha256(mutated))

    def test_solver_control_commands_are_rejected(self):
        for command in ("(check-sat)", "(get-model)", "(set-option :timeout 1)"):
            with self.assertRaises(ValueError):
                canonical_smt2(DIRECT_OBSERVATION + command)

    def test_production_lowering_is_explicitly_unsupported(self):
        with self.assertRaises(UnsupportedLoweringError):
            compile_manifest_obligation(None)

    def test_query_result_rejects_false_sat_evidence(self):
        with self.assertRaises(ValueError):
            QueryResult("0" * 64, SolverStatus.SAT, "Z3", 0.0)

    def test_nonproof_result_cannot_be_replayed_as_evidence(self):
        recorded = QueryResult(
            query_sha256(DIRECT_OBSERVATION), SolverStatus.UNKNOWN,
            "Z3", 0.0, reason="incomplete",
        )
        with self.assertRaises(ValueError):
            replay_query(DIRECT_OBSERVATION, recorded)


@unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
class Z3FixtureTests(unittest.TestCase):
    def test_native_sort_boundary_rejects_containers_and_untyped_enums(self):
        for native in (NativeSort.RECORD, NativeSort.QUEUE, NativeSort.STACK,
                       NativeSort.ENUM):
            with self.assertRaises(UnsupportedLoweringError):
                native_z3_sort(native)

    def test_direct_observation_fixture_is_unsat_and_replays(self):
        result = run_smt2_query(DIRECT_OBSERVATION, timeout_ms=30_000)
        self.assertIs(result.status, SolverStatus.UNSAT, result.reason)
        self.assertIn("tactic=simplify>propagate-values>solve-eqs>smt",
                      result.solver_identity)
        replay = replay_query(DIRECT_OBSERVATION, result, timeout_ms=30_000)
        self.assertIs(replay.status, SolverStatus.UNSAT, replay.reason)
        self.assertEqual(result.query_sha256, replay.query_sha256)

    def test_hidden_state_fixture_is_sat_with_retained_model(self):
        result = run_smt2_query(HIDDEN_STATE_COUNTEREXAMPLE, timeout_ms=30_000)
        self.assertIs(result.status, SolverStatus.SAT, result.reason)
        self.assertTrue(result.model_smt2)

    def test_replay_rejects_changed_query(self):
        result = run_smt2_query(DIRECT_OBSERVATION, timeout_ms=30_000)
        mutated = DIRECT_OBSERVATION.replace("(+ observation_left action)",
                                             "(+ observation_left action 1)")
        with self.assertRaises(ValueError):
            replay_query(mutated, result, timeout_ms=30_000)


if __name__ == "__main__":
    unittest.main()
