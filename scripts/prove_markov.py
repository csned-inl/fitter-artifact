#!/usr/bin/env python3
"""Compile one SysML OT model and print its buffered-MDP proof result."""

from __future__ import annotations

import argparse
import json

from clarity.certification.ot_markov import prove_markov


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", help="path to the authoritative SysML model")
    parser.add_argument("--timeout-ms", type=int, default=5_000)
    arguments = parser.parse_args()
    result = prove_markov(arguments.model, timeout_ms=arguments.timeout_ms)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("classification") == "CERTIFIED_UNDER_PROFILE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
