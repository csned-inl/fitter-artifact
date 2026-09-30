#!/usr/bin/env bash
# Record the actual WSL allocation and verify finite-history proof prerequisites.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CREATE_VENV=0
if [[ "${1:-}" == "--create-venv" ]]; then
  CREATE_VENV=1
elif [[ $# -ne 0 ]]; then
  echo "usage: $0 [--create-venv]" >&2
  exit 2
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESULT_DIR="$REPO_ROOT/runs/finite-history-preflight-$STAMP"
mkdir -p "$RESULT_DIR"

{
  uname -a
  printf '\nWSL_INTEROP=%s\n' "${WSL_INTEROP:-}"
  printf 'WSL_DISTRO_NAME=%s\n' "${WSL_DISTRO_NAME:-}"
} >"$RESULT_DIR/platform.txt"
lscpu >"$RESULT_DIR/lscpu.txt" 2>&1 || true
free -b >"$RESULT_DIR/memory.txt" 2>&1 || true
df -B1 "$REPO_ROOT" >"$RESULT_DIR/disk.txt" 2>&1 || true

PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" --version >"$RESULT_DIR/python-version.txt" 2>&1 || true

if [[ $CREATE_VENV -eq 1 ]]; then
  VENV="$REPO_ROOT/.venv-finite-history"
  if [[ ! -x "$VENV/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip \
      >"$RESULT_DIR/pip-bootstrap.log" 2>&1
    printf 'created\n' >"$RESULT_DIR/environment-action.txt"
  else
    printf 'reused\n' >"$RESULT_DIR/environment-action.txt"
  fi
  "$VENV/bin/python" -m pip install -r "$REPO_ROOT/requirements.txt" \
    >"$RESULT_DIR/pip-install.log" 2>&1
  PYTHON_BIN="$VENV/bin/python"
fi

RESOURCE_VALUES="$("$PYTHON_BIN" - "$REPO_ROOT" <<'PY'
import os
import shutil
import sys

page_size = os.sysconf("SC_PAGE_SIZE")
physical_pages = os.sysconf("SC_PHYS_PAGES")
memory_bytes = page_size * physical_pages
processors = os.cpu_count() or 1
free_bytes = shutil.disk_usage(sys.argv[1]).free
print(memory_bytes, processors, free_bytes)
PY
)"
read -r MEM_BYTES CPU_COUNT FREE_BYTES <<<"$RESOURCE_VALUES"

# Until the new backend has measured per-query peaks, reserve 8 GiB for WSL and
# budget 12 GiB per isolated solver process. Never exceed four concurrent jobs.
GIB=$((1024 * 1024 * 1024))
if (( MEM_BYTES > 8 * GIB )); then
  MEMORY_JOBS=$(((MEM_BYTES - 8 * GIB) / (12 * GIB)))
else
  MEMORY_JOBS=0
fi
CPU_JOBS=$((CPU_COUNT / 4))
(( CPU_JOBS < 1 )) && CPU_JOBS=1
RECOMMENDED_JOBS="$MEMORY_JOBS"
(( CPU_JOBS < RECOMMENDED_JOBS )) && RECOMMENDED_JOBS="$CPU_JOBS"
(( RECOMMENDED_JOBS > 4 )) && RECOMMENDED_JOBS=4
(( RECOMMENDED_JOBS < 1 )) && RECOMMENDED_JOBS=1

env PYTHONPATH="$REPO_ROOT/src" "$PYTHON_BIN" - \
  "$RESULT_DIR/report.json" "$MEM_BYTES" "$CPU_COUNT" \
  "$FREE_BYTES" "$RECOMMENDED_JOBS" <<'PY'
import importlib.util
import json
import platform
import subprocess
import sys
from pathlib import Path

report_path, mem_bytes, cpus, free_bytes, jobs = sys.argv[1:]
try:
    import z3
    z3_python = z3.get_version_string()
except Exception as exc:
    z3_python = None
    z3_import_error = f"{type(exc).__name__}: {exc}"
else:
    z3_import_error = None
try:
    binary = subprocess.run(
        ["z3", "--version"], text=True, capture_output=True, timeout=10
    )
    z3_binary = binary.stdout.strip() if binary.returncode == 0 else None
except Exception:
    z3_binary = None

report = {
    "schema": "clarity.finite-history-workstation-preflight",
    "version": 1,
    "platform": platform.platform(),
    "python": platform.python_version(),
    "python_supported": sys.version_info >= (3, 12),
    "wsl": bool(Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
                or "microsoft" in platform.release().lower()),
    "logical_processors_visible": int(cpus),
    "memory_bytes_visible": int(float(mem_bytes)),
    "repository_free_bytes": int(free_bytes),
    "recommended_parallel_solver_jobs": int(jobs),
    "provisional_solver_memory_bytes_per_job": 12 * 1024**3,
    "z3_python_version": z3_python,
    "z3_python_import_error": z3_import_error,
    "z3_binary_version": z3_binary,
    "z3_required_version": "5.0.0.0",
    "markov_z3_module_present": importlib.util.find_spec(
        "clarity.certification.markov_z3"
    ) is not None,
}
report["z3_version_matches_requirement"] = z3_python == report["z3_required_version"]
report["ready_for_solver_backend"] = bool(
    report["python_supported"]
    and report["wsl"]
    and report["z3_version_matches_requirement"]
)
Path(report_path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
print(json.dumps(report, indent=2, sort_keys=True))
PY

echo "Preflight evidence: $RESULT_DIR"
