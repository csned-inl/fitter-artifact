#!/usr/bin/env python3
"""Validate structural proof methods independently of model certification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.proof_logic_validation import (  # noqa: E402
    validate_symbolic_proof_methods,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-ms", type=int, default=5_000)
    arguments = parser.parse_args()
    result = validate_symbolic_proof_methods(timeout_ms=arguments.timeout_ms)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("classification") == "VALIDATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
