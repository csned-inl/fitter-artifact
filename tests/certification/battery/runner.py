"""Coordinator for the complete Markov certification validation battery."""

from __future__ import annotations

import time
from pathlib import Path

from .equation_cases import run_equation_cases
from .model_cases import run_negative_model_cases, validate_positive_model
from .mutation_cases import run_mutation_cases
from .solver_cases import run_solver_cases
from .support import Battery


def main() -> int:
    out_dir = Path("results") / f"certification_battery_{time.strftime('%Y%m%d-%H%M%S')}"
    cert_dir = out_dir / "positive_certificates"
    cert_dir.mkdir(parents=True, exist_ok=True)
    battery = Battery(out_dir)

    run_solver_cases(battery)
    run_equation_cases(battery)

    positive_certs = {}
    for name in ("mixing", "thermostat", "cruise-discrete"):
        positive_certs[name] = validate_positive_model(battery, name, cert_dir)

    run_negative_model_cases(battery)
    run_mutation_cases(battery, positive_certs["mixing"])

    battery.report()
    print(f"\nWROTE {out_dir / 'report.json'}")
    if battery.failures:
        print("\nCERTIFICATION BATTERY FAILED")
        for failure in battery.failures:
            print(f"  - {failure}")
        return 1

    print("\nCERTIFICATION BATTERY PASSED")
    return 0
