#!/usr/bin/env bash
# Run every implemented finite-history gate. This does not claim an SMT proof.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$REPO_ROOT/.venv-finite-history/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

TIMEOUT_SECONDS="${FINITE_HISTORY_GATE_TIMEOUT_SECONDS:-300}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESULT_DIR="$REPO_ROOT/runs/finite-history-gates-$STAMP"
mkdir -p "$RESULT_DIR"

TESTS=(
  tests/certification/validate_markov_reference.py
  tests/certification/validate_thermostat_markov_contract.py
  tests/certification/validate_markov_extraction.py
  tests/certification/validate_markov_interval.py
  tests/certification/validate_markov_slice.py
  tests/certification/validate_markov_obligations.py
  tests/certification/validate_markov_native_semantics.py
  tests/certification/validate_markov_z3_expressions.py
  tests/certification/validate_markov_z3_control.py
  tests/certification/validate_markov_z3_events.py
  tests/certification/validate_markov_z3_backend.py
)

STATUS=0
for test_path in "${TESTS[@]}"; do
  name="$(basename "$test_path" .py)"
  if ! timeout --signal=TERM --kill-after=15s "$TIMEOUT_SECONDS" \
      env PYTHONPATH="$REPO_ROOT/src" "$PYTHON_BIN" "$REPO_ROOT/$test_path" \
      >"$RESULT_DIR/$name.out" 2>"$RESULT_DIR/$name.err"; then
    STATUS=1
  fi
done

if ! env PYTHONPATH="$REPO_ROOT/src" "$PYTHON_BIN" -m compileall -q \
    "$REPO_ROOT/src" "$REPO_ROOT/tests" \
    >"$RESULT_DIR/compileall.out" 2>"$RESULT_DIR/compileall.err"; then
  STATUS=1
fi

env PYTHONPATH="$REPO_ROOT/src" "$PYTHON_BIN" - "$RESULT_DIR/summary.json" \
  "$STATUS" <<'PY'
import importlib.util
import json
import sys
from pathlib import Path

path, status = sys.argv[1:]
try:
    import z3
    z3_version = z3.get_version_string()
except Exception:
    z3_version = None
backend = importlib.util.find_spec("clarity.certification.markov_z3") is not None
z3_fixtures = (
    "passed" if z3_version is not None and status == "0"
    else "not_run_z3_unavailable" if z3_version is None
    else "failed"
)
record = {
    "schema": "clarity.finite-history-workstation-gates",
    "version": 1,
    "implemented_gate_status": "passed" if status == "0" else "failed",
    "z3_python_version": z3_version,
    "markov_z3_module_present": backend,
    "z3_fixture_status": z3_fixtures,
    "solver_proof_status": (
        "not_run_backend_not_implemented" if not backend
        else "not_run_use_backend_certificate_runner"
    ),
    "certificate_claimed": False,
}
Path(path).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
print(json.dumps(record, indent=2, sort_keys=True))
PY

echo "Gate evidence: $RESULT_DIR"
exit "$STATUS"
