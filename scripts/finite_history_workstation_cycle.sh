#!/usr/bin/env bash
# Pull, prepare, validate, and publish sanitized workstation evidence in one run.
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_BRANCH="${FINITE_HISTORY_BRANCH:-codex/finite-history-mdp-proof}"
STATE_DIR="$REPO_ROOT/.codex-workstation"
LATEST_RESULT="$STATE_DIR/latest.json"
DO_PULL=1
DO_INVENTORY=1
ORIGINAL_ARGS=("$@")

usage() {
  cat <<'EOF'
Usage: finite_history_workstation_cycle.sh [--offline] [--no-inventory]

  --offline       Run the checked-out revision without contacting GitHub.
  --no-inventory  Do not invoke wsl-inventory after recording the result.
EOF
}

while (($#)); do
  case "$1" in
    --offline) DO_PULL=0 ;;
    --no-inventory) DO_INVENTORY=0 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$STATE_DIR"
exec 9>"$STATE_DIR/cycle.lock"
if ! flock -n 9; then
  echo "A finite-history workstation cycle is already running." >&2
  exit 0
fi

cd "$REPO_ROOT"

SYNC_STATUS="offline"
if ((DO_PULL)) && [[ "${FINITE_HISTORY_AFTER_PULL:-0}" != "1" ]]; then
  current_branch="$(git branch --show-current)"
  if [[ "$current_branch" != "$EXPECTED_BRANCH" ]]; then
    echo "Expected branch $EXPECTED_BRANCH; currently on $current_branch." >&2
    exit 1
  fi

  before="$(git rev-parse HEAD)"
  fetched=0
  for delay in 0 2 5; do
    ((delay == 0)) || sleep "$delay"
    if git fetch origin "$EXPECTED_BRANCH" \
        && git merge --ff-only "origin/$EXPECTED_BRANCH"; then
      fetched=1
      break
    fi
    echo "GitHub sync attempt failed; retrying." >&2
  done
  if ((fetched == 0)); then
    echo "Could not fast-forward from GitHub. Use --offline only if the local revision is intentional." >&2
    exit 1
  fi
  after="$(git rev-parse HEAD)"
  SYNC_STATUS="current"
  if [[ "$before" != "$after" ]]; then
    exec env FINITE_HISTORY_AFTER_PULL=1 \
      "$REPO_ROOT/scripts/finite_history_workstation_cycle.sh" "${ORIGINAL_ARGS[@]}"
  fi
elif ((DO_PULL)); then
  SYNC_STATUS="updated"
fi

PYTHON_BIN="$REPO_ROOT/.venv-finite-history/bin/python"
if [[ -x "$PYTHON_BIN" ]] && "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import importlib.metadata
raise SystemExit(importlib.metadata.version("z3-solver") != "5.0.0.0")
PY
then
  PREFLIGHT_ARGS=()
else
  PREFLIGHT_ARGS=(--create-venv)
fi

set +e
preflight_output="$(bash "$REPO_ROOT/scripts/finite_history_workstation_preflight.sh" "${PREFLIGHT_ARGS[@]}" 2>&1)"
preflight_status=$?
printf '%s\n' "$preflight_output"
gate_output="$(bash "$REPO_ROOT/scripts/finite_history_workstation_gates.sh" 2>&1)"
gate_status=$?
printf '%s\n' "$gate_output"
set -e

preflight_dir="$(printf '%s\n' "$preflight_output" | sed -n 's/^Preflight evidence: //p' | tail -n 1)"
gate_dir="$(printf '%s\n' "$gate_output" | sed -n 's/^Gate evidence: //p' | tail -n 1)"
preflight_report="${preflight_dir:+$preflight_dir/report.json}"
gate_report="${gate_dir:+$gate_dir/summary.json}"

PYTHON_FOR_REPORT="$PYTHON_BIN"
[[ -x "$PYTHON_FOR_REPORT" ]] || PYTHON_FOR_REPORT=python3
"$PYTHON_FOR_REPORT" - \
  "$LATEST_RESULT" "$SYNC_STATUS" "$(git branch --show-current)" \
  "$(git rev-parse HEAD)" "$preflight_status" "$gate_status" \
  "$preflight_report" "$gate_report" <<'PY'
import json
import sys
from pathlib import Path

(destination, sync_status, branch, commit, preflight_status, gate_status,
 preflight_path, gate_path) = sys.argv[1:]

def load_report(path):
    candidate = Path(path) if path else None
    if candidate is None or not candidate.is_file():
        return None
    return json.loads(candidate.read_text())

record = {
    "schema": "clarity.finite-history-workstation-cycle",
    "version": 1,
    "repository": "fitter-artifact",
    "source_branch": branch,
    "source_commit": commit,
    "sync_status": sync_status,
    "preflight_exit_status": int(preflight_status),
    "gate_exit_status": int(gate_status),
    "preflight": load_report(preflight_path),
    "gates": load_report(gate_path),
}
Path(destination).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
print(json.dumps(record, indent=2, sort_keys=True))
print(f"Latest workstation result: {destination}")
PY

inventory_status=0
if ((DO_INVENTORY)); then
  if command -v wsl-inventory >/dev/null 2>&1; then
    echo "Publishing the sanitized result through WSL inventory..."
    wsl-inventory --quiet || inventory_status=$?
    if ((inventory_status)); then
      echo "WSL inventory publication failed; the local result was retained." >&2
    fi
  else
    echo "wsl-inventory is not installed; the local result was retained." >&2
    inventory_status=1
  fi
fi

if ((preflight_status || gate_status || inventory_status)); then
  exit 1
fi
