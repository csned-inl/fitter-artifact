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
CHECK_INDEX="$RESULT_DIR/checks.tsv"
: >"$CHECK_INDEX"

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
  tests/certification/validate_markov_z3_transition.py
  tests/certification/validate_markov_z3_interface.py
  tests/certification/validate_markov_z3_history.py
  tests/certification/validate_markov_z3_window.py
  tests/certification/validate_markov_z3_pair.py
  tests/certification/validate_markov_z3_backend.py
  tests/certification/validate_gate_reporting.py
)

STATUS=0
for test_path in "${TESTS[@]}"; do
  name="$(basename "$test_path" .py)"
  started_ns="$(date +%s%N)"
  if timeout --signal=TERM --kill-after=15s "$TIMEOUT_SECONDS" \
      env PYTHONPATH="$REPO_ROOT/src" "$PYTHON_BIN" "$REPO_ROOT/$test_path" \
      >"$RESULT_DIR/$name.out" 2>"$RESULT_DIR/$name.err"; then
    check_status=0
  else
    check_status=$?
    STATUS=1
  fi
  ended_ns="$(date +%s%N)"
  duration_ms=$(((ended_ns - started_ns) / 1000000))
  printf '%s\t%s\t%s\t%s\n' \
    "$name" "$test_path" "$check_status" "$duration_ms" >>"$CHECK_INDEX"
done

started_ns="$(date +%s%N)"
if env PYTHONPATH="$REPO_ROOT/src" "$PYTHON_BIN" -m compileall -q \
    "$REPO_ROOT/src" "$REPO_ROOT/tests" \
    >"$RESULT_DIR/compileall.out" 2>"$RESULT_DIR/compileall.err"; then
  compile_status=0
else
  compile_status=$?
  STATUS=1
fi
ended_ns="$(date +%s%N)"
duration_ms=$(((ended_ns - started_ns) / 1000000))
printf '%s\t%s\t%s\t%s\n' \
  "compileall" "python_compileall" "$compile_status" "$duration_ms" >>"$CHECK_INDEX"

env PYTHONPATH="$REPO_ROOT/src" "$PYTHON_BIN" \
  "$REPO_ROOT/scripts/finite_history_gate_report.py" \
  --index "$CHECK_INDEX" \
  --result-dir "$RESULT_DIR" \
  --repo-root "$REPO_ROOT" \
  --output "$RESULT_DIR/summary.json"

echo "Gate evidence: $RESULT_DIR"
exit "$STATUS"
