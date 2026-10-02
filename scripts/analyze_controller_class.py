#!/usr/bin/env python3
"""Print a direct-SysML controller-class analysis as JSON."""

from __future__ import annotations

import argparse
import json

from clarity.certification.controller_class_analysis import analyze_controller_class


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", help="path to the SysML OT model")
    arguments = parser.parse_args()
    report = analyze_controller_class(arguments.model)
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
