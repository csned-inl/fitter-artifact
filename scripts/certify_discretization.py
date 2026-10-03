#!/usr/bin/env python3
"""Certify literal direct-SysML safety across sampled control intervals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.discretization_semantic_validation import (  # noqa: E402
    validate_structural_discretization_semantically,
)
from clarity.certification.structural_discretization import (  # noqa: E402
    prove_discretization_structurally,
)
from clarity.certification.structural_rule_validation import (  # noqa: E402
    validate_structural_rule_schemas,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path, help="direct SysML model to certify")
    parser.add_argument(
        "--validate-z3",
        action="store_true",
        help="also refute every compiled counterexample with Z3",
    )
    parser.add_argument("--timeout-ms", type=int, default=5_000)
    args = parser.parse_args()

    result: dict[str, object] = {
        "structural": prove_discretization_structurally(args.model),
    }
    if args.validate_z3:
        result["proof_rule_validation"] = validate_structural_rule_schemas(
            timeout_ms=args.timeout_ms
        )
        result["semantic_validation"] = (
            validate_structural_discretization_semantically(
                args.model, timeout_ms=args.timeout_ms
            )
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    structural = result["structural"]
    assert isinstance(structural, dict)
    if structural.get("classification") != "CERTIFIED_STRUCTURALLY":
        return 2
    if args.validate_z3:
        rules = result["proof_rule_validation"]
        assert isinstance(rules, dict)
        if rules.get("classification") != "VALIDATED":
            return 3
        semantic = result["semantic_validation"]
        assert isinstance(semantic, dict)
        if semantic.get("classification") != "VALIDATED":
            return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
