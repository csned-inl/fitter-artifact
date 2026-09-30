#!/usr/bin/env python3
"""Static safety checks for the WSL workstation entry points."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (
    ROOT / "scripts/finite_history_workstation_preflight.sh",
    ROOT / "scripts/finite_history_workstation_gates.sh",
)


class WorkstationScriptTests(unittest.TestCase):
    def test_shell_syntax(self):
        for script in SCRIPTS:
            result = subprocess.run(
                ["bash", "-n", str(script)], text=True, capture_output=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_runner_never_claims_a_certificate(self):
        text = SCRIPTS[1].read_text()
        self.assertIn('"certificate_claimed": False', text)
        self.assertIn("not_run_backend_not_implemented", text)
        self.assertNotIn('"certificate_claimed": True', text)

    def test_preflight_preserves_the_pinned_z3_version(self):
        text = SCRIPTS[0].read_text()
        self.assertIn('"z3_required_version": "5.0.0.0"', text)
        self.assertIn('importlib.metadata.version("z3-solver")', text)
        self.assertIn("z3_distribution == report", text)
        self.assertIn('pip install -r "$REPO_ROOT/requirements.txt"', text)

    def test_preflight_reuses_an_existing_environment(self):
        text = SCRIPTS[0].read_text()
        self.assertIn('if [[ ! -x "$VENV/bin/python" ]]', text)
        self.assertIn('elif [[ -x "$REPO_ROOT/.venv-finite-history/bin/python" ]]', text)
        self.assertIn('PYTHON_BIN="$REPO_ROOT/.venv-finite-history/bin/python"', text)
        self.assertIn("environment-action.txt", text)
        self.assertIn("ready_for_solver_backend", text)

    def test_generated_workstation_state_is_git_ignored(self):
        ignore = (ROOT / ".gitignore").read_text().splitlines()
        self.assertIn("/.venv-finite-history/", ignore)
        self.assertIn("/runs/", ignore)


if __name__ == "__main__":
    unittest.main()
