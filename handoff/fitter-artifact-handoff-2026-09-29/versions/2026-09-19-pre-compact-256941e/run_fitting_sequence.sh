#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

OUT_DIR="${OUT_DIR:-$SCRIPT_DIR/outputs/latest}"

cd "$SCRIPT_DIR"
PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
  PYTHONDONTWRITEBYTECODE=1 \
  "$PYTHON_BIN" -m clarity.pipeline.runner --out-dir "$OUT_DIR" "$@"
