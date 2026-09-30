#!/usr/bin/env python3
"""Structured workstation gate-report regression tests."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from finite_history_gate_report import build_report, sanitize_diagnostic


class GateReportTests(unittest.TestCase):
    def test_failure_is_structured_bounded_and_sanitized(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            result = base / "result"
            result.mkdir()
            index = result / "checks.tsv"
            index.write_text("validate_fixture\ttests/validate_fixture.py\t1\t27\n")
            (result / "validate_fixture.out").write_text("")
            (result / "validate_fixture.err").write_text(
                f'Traceback in {base}/repo/tests/validate_fixture.py\n'
                "API_TOKEN=do-not-publish\n"
                "AssertionError: expected unsat, found error\n"
            )
            report = build_report(
                index,
                result,
                base / "repo",
                z3_version="5.0.0",
                backend_present=True,
            )
        self.assertEqual(report["implemented_gate_status"], "failed")
        self.assertEqual(report["failed_check_count"], 1)
        failure = report["test_results"][0]
        self.assertEqual(failure["status"], "failed")
        self.assertIn("<repo>", failure["diagnostic"])
        self.assertNotIn(str(base), failure["diagnostic"])
        self.assertNotIn("do-not-publish", failure["diagnostic"])
        self.assertFalse(report["certificate_claimed"])

    def test_timeout_classification_and_z3_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            index = base / "checks.tsv"
            index.write_text(
                "validate_markov_z3_fixture\ttests/z3.py\t124\t300000\n"
            )
            (base / "validate_markov_z3_fixture.out").write_text("")
            (base / "validate_markov_z3_fixture.err").write_text("timed out\n")
            report = build_report(
                index,
                base,
                base,
                z3_version="5.0.0",
                backend_present=True,
            )
        self.assertEqual(report["timeout_check_count"], 1)
        self.assertEqual(report["z3_fixture_status"], "failed")

    def test_passing_report_has_no_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            index = base / "checks.tsv"
            index.write_text(
                "validate_markov_z3_fixture\ttests/z3.py\t0\t15\n"
            )
            (base / "validate_markov_z3_fixture.out").write_text("ok\n")
            (base / "validate_markov_z3_fixture.err").write_text("")
            report = build_report(
                index,
                base,
                base,
                z3_version="5.0.0",
                backend_present=True,
            )
        self.assertEqual(report["implemented_gate_status"], "passed")
        self.assertEqual(report["z3_fixture_status"], "passed")
        self.assertNotIn("diagnostic", report["test_results"][0])
        json.dumps(report)

    def test_standalone_sanitizer_removes_other_absolute_paths(self):
        value = sanitize_diagnostic(
            "failure in /opt/private/place.py",
            repo_root=Path("/different/repo"),
            home=Path("/different/home"),
        )
        self.assertNotIn("/opt/private/place.py", value)


if __name__ == "__main__":
    unittest.main()
