"""Shared fixtures and reporting for Markov certification validation."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from clarity.models import models_root
from clarity.sysml.inputs import discover_sysml
from clarity.certification.reconstruct import reconstruct
from clarity.sysml.runtime_settings import DEFAULT_DT


_DISCOVERED = {
    item.key: item.path
    for item in discover_sysml([], models_root=models_root())
}
MODELS = {
    "mixing": _DISCOVERED["tank-filling-system"],
    "thermostat": _DISCOVERED["thermostat"],
    "cruise-discrete": _DISCOVERED["cruise-control"],
}
TEST_DT = DEFAULT_DT


EXPECTED = {
    "mixing": {
        "buffer": (2, 1),
        "state": 15,
        "definitions": 27,
        "transitions": 15,
        "q": 14,
        "terminal_state_refs": {
            "controller_volume1Res_response",
            "controller_volume2Res_response",
        },
        "solver_status": "discharged",
    },
    "thermostat": {
        "buffer": (1, 2),
        "state": 11,
        "definitions": 6,
        "transitions": 11,
        "q": 9,
        "terminal_state_refs": {
            "controller_acOn",
            "controller_heaterOn",
            "controller_reading_temperatureCelcius",
        },
        "solver_status": "discharged",
    },
    "cruise-discrete": {
        "buffer": (1, 2),
        "state": 15,
        "definitions": 9,
        "transitions": 15,
        "q": 13,
        "terminal_state_refs": {
            "controller_brakeOn",
            "controller_throttleOn",
            "controller_reading_speedMps",
        },
        "solver_status": "discharged",
    },
}


def first_closure(model: dict[str, Any], max_obs: int = 2, max_act: int = 4):
    if set(model["STATE"]) - set(model["nsupp"]):
        return None
    target = set(model.get("R", set())) or set(model["STATE"])
    for b_obs in range(max_obs + 1):
        for b_act in range(max_act + 1):
            missing, _ = reconstruct(model, b_obs, b_act, horizon=8, target=target)
            if not missing:
                return b_obs, b_act
    return None


class Battery:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.cases: list[dict[str, Any]] = []
        self.failures: list[str] = []

    def record(self, name: str, passed: bool, detail: str = "", data: dict[str, Any] | None = None) -> None:
        self.cases.append({
            "name": name,
            "passed": passed,
            "detail": detail,
            "data": data or {},
        })
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}: {detail}")
        if not passed:
            self.failures.append(f"{name}: {detail}")

    def require(self, name: str, condition: bool, detail: str) -> None:
        self.record(name, condition, detail)

    def report(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "cases": self.cases,
            "failures": self.failures,
            "passed": not self.failures,
        }
        (self.out_dir / "report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
