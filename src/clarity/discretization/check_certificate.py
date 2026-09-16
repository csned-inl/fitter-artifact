#!/usr/bin/env python3
"""Independent checker for discretization-safety certificates."""

from __future__ import annotations

import argparse
import sys

from .certificates import verify_certificate, load_certificate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("certificate", nargs="+")
    args = parser.parse_args()
    status = 0
    for path in args.certificate:
        verification = verify_certificate(load_certificate(path))
        errors = verification["errors"]
        if errors:
            status = 1
            print(f"{path}: CHECK FAILED")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"{path}: CHECK PASSED; safety={verification['result']}; "
                  f"obligations={verification['obligations_certified']}/{verification['obligations_checked']}")
    return status


if __name__ == "__main__":
    sys.exit(main())
