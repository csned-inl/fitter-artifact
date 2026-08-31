#!/usr/bin/env python3
"""Run the focused discretization validation groups."""

from __future__ import annotations

import argparse

from certificate_validation import validate_certificates
from proof_rule_validation import validate_proof_rules
from source_mapping_validation import validate_annotation_cascade


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("certificate", nargs="+")
    args = parser.parse_args()
    validate_proof_rules()
    validate_certificates(args.certificate)
    validate_annotation_cascade(args.certificate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
