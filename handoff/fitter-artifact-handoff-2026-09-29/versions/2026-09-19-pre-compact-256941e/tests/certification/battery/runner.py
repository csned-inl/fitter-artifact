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
        try:
            positive_certs[name] = validate_positive_model(battery, name, cert_dir)
        except (ValueError, TypeError, KeyError) as exc:
            battery.record(f'{name}/incomplete_proof', False, f'{type(exc).__name__}: {exc}')

    run_negative_model_cases(battery)
    # Exercise mutations against checked positive evidence; a failed bundled
    # proof remains a failed model result and is never a valid mutation control.
    from clarity.certification.certificate import build_certificate_for_path, check_certificate
    fixture = Path(__file__).parents[2] / 'discretization/fixtures/held_constant.sysml'
    positive = build_certificate_for_path(str(fixture), dt=.1)
    if check_certificate(positive):
        battery.record('mutations/positive_control', False, 'positive certificate did not verify')
    else:
        run_mutation_cases(battery, positive)

    battery.report()
    print(f"\nWROTE {out_dir / 'report.json'}")
    if battery.failures:
        print("\nCERTIFICATION BATTERY FAILED")
        for failure in battery.failures:
            print(f"  - {failure}")
        return 1

    print("\nCERTIFICATION BATTERY PASSED")
    return 0
