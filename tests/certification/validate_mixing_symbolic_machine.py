#!/usr/bin/env python3
"""Focused validation for the direct Mixing Machine symbolic prototype."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.mixing_symbolic_machine import (  # noqa: E402
    APPROVED_GIT_BLOB_SHA,
    APPROVED_SOURCE_SHA256,
    AUTHORITATIVE_PATH,
    AUTHORITATIVE_REPOSITORY,
    DEFAULT_MODEL_PATH,
    MAX_SMT2_BYTES,
    RESULT_SCHEMA,
    build_fixed_buffer_markov_query,
    extract_mixing_relation,
    validate_mixing_source,
)


HAS_Z3 = importlib.util.find_spec("z3") is not None


class MixingSymbolicMachineTests(unittest.TestCase):
    def test_source_is_pinned_to_standalone_identity(self):
        self.assertEqual(AUTHORITATIVE_REPOSITORY, "csned-inl/clarity-standalone")
        self.assertEqual(AUTHORITATIVE_PATH, "sysml-models/mixing-sysml-model/model.sysml")
        self.assertEqual(validate_mixing_source(), APPROVED_SOURCE_SHA256)
        data = DEFAULT_MODEL_PATH.read_bytes()
        import hashlib
        blob = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()  # nosec B324
        self.assertEqual(blob, APPROVED_GIT_BLOB_SHA)

    def test_tolerance_drift_is_rejected_without_solver(self):
        changed = DEFAULT_MODEL_PATH.read_text().replace(
            "attribute toleranceMl : Integer = 2;",
            "attribute toleranceMl : Integer = 3;",
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / "model.sysml"
            model.write_text(changed)
            with self.assertRaisesRegex(ValueError, "authoritative standalone Git blob"):
                validate_mixing_source(model)

    def test_prototype_does_not_import_legacy_certification_or_simulator(self):
        source = (ROOT / "src" / "clarity" / "certification" /
                  "mixing_symbolic_machine.py").read_text()
        self.assertNotIn("from .markov_", source)
        self.assertNotIn("clarity.runtime", source)
        self.assertNotIn("clarity.sysml.simulator", source)
        self.assertLessEqual(len(source.splitlines()), 740)

    @unittest.skipUnless(HAS_Z3, "pinned z3-solver unavailable")
    def test_physical_and_sampled_values_are_distinct_with_explicit_tolerance(self):
        import z3

        relation = extract_mixing_relation(prefix="sensor_split_")
        self.assertNotEqual(str(relation.current.physical_level_1),
                            str(relation.current.sampled_level_1))
        solver = z3.Solver()
        solver.add(relation.domain, relation.sample_relation)
        solver.add(relation.current.sampled_level_1 !=
                   relation.current.physical_level_1 - 2)
        self.assertEqual(str(solver.check()), "unsat")

    @unittest.skipUnless(HAS_Z3, "pinned z3-solver unavailable")
    def test_fixed_context_is_shared_and_scenario_fields_are_observed(self):
        query = build_fixed_buffer_markov_query()
        self.assertIs(query.left.fixed, query.right.fixed)
        for name in ("target_transfer_1", "target_transfer_2",
                     "original_level_1", "original_level_2", "tolerance_ml"):
            self.assertIn(name, query.left.fixed.names())
        self.assertIn(query.left.fixed["target_transfer_1"], query.left.observation)
        self.assertNotIn(query.left.fixed["tolerance_ml"], query.left.observation)

    @unittest.skipUnless(HAS_Z3, "pinned z3-solver unavailable")
    def test_symbolic_shield_matches_clarity_keep_or_replace_boundary(self):
        import z3
        from clarity.runtime.shield import SpecShield

        relation = extract_mixing_relation(prefix="shield_test_")
        shield = SpecShield(str(DEFAULT_MODEL_PATH))
        scenarios = (
            (148, 148, 50, 100, 150, 150),
            (98, 48, 50, 100, 150, 150),
            (102, 48, 50, 100, 150, 150),
        )
        for level1, level2, target1, target2, original1, original2 in scenarios:
            observation = {
                "tank1VolumeMl": level1,
                "tank2VolumeMl": level2,
                "tank1TargetTransferMl": target1,
                "tank2TargetTransferMl": target2,
                "tank1OriginalMl": original1,
                "tank2OriginalMl": original2,
                "done": (original1 - level1 >= target1 and
                         original2 - level2 >= target2),
            }
            for proposal in range(16):
                expected = shield(proposal, observation)
                solver = z3.Solver()
                solver.add(relation.fixed["target_transfer_1"] == target1)
                solver.add(relation.fixed["target_transfer_2"] == target2)
                solver.add(relation.fixed["original_level_1"] == original1)
                solver.add(relation.fixed["original_level_2"] == original2)
                solver.add(relation.current.sampled_level_1 == level1)
                solver.add(relation.current.sampled_level_2 == level2)
                solver.add(relation.proposal == proposal, relation.enabled)
                self.assertEqual(str(solver.check()), "sat")
                self.assertEqual(solver.model().eval(relation.executed_action).as_long(), expected)

    @unittest.skipUnless(HAS_Z3, "pinned z3-solver unavailable")
    def test_fixed_buffer_query_is_compact_and_certifies_under_contract(self):
        relation = extract_mixing_relation()
        self.assertEqual(relation.summary()["result_schema"], RESULT_SCHEMA)
        self.assertEqual(len(relation.properties), 4)
        query = build_fixed_buffer_markov_query()
        self.assertLessEqual(len(query.smt2().encode("utf-8")), MAX_SMT2_BYTES)
        result = query.run()
        self.assertEqual(result["classification"], "CERTIFIED_UNDER_CONTRACT", result)


if __name__ == "__main__":
    unittest.main()
