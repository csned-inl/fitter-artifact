#!/usr/bin/env python3
"""Focused regression checks for the certification extractor.

This is not the final large validation battery. It guards the high-impact
semantics we are adding now: explicit flow equations, state-machine actuator
equations, and dependency expansion through same-cycle definitions.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from clarity.models import models_root
from clarity.sysml.inputs import discover_sysml
from clarity.certification.certificate import (
    PROFILE_OBLIGATIONS_DISCHARGED,
    build_certificate_for_path,
    check_certificate,
    load_certificate,
    write_certificate,
)
from clarity.certification.equations import Op
from clarity.certification.relevance import compute_transition_closed_relevance, equation_refs
from clarity.certification.strict_extract import extract_equation_model
from clarity.certification.reconstruct import get_strict_model, reconstruct
from clarity.sysml.runtime_settings import DEFAULT_DT


_DISCOVERED = {
    item.key: item.path
    for item in discover_sysml(
        [], models_root=models_root()
    )
}
MODELS = {
    "mixing": _DISCOVERED["tank-filling-system"],
    "thermostat": _DISCOVERED["thermostat"],
    "cruise-discrete": _DISCOVERED["cruise-control"],
}
TEST_DT = DEFAULT_DT


BLOCKED_DIAGNOSTICS = {
    "legacy_dependency_fallback",
    "multiple_flow_sources",
    "state_machine_output_partial",
    "state_machine_semantics_partial",
    "unsupported_flow_merge",
}

EXPECTED_STRICT_CLOSURE = {
    "mixing": (2, 1),
    "thermostat": (1, 2),
    "cruise-discrete": (1, 2),
}

EXPECTED_SOLVER_ADVISORY = {
    "mixing": "discharged",
    "thermostat": "discharged",
    "cruise-discrete": "discharged",
}


def _first_closure(model, max_obs: int = 2, max_act: int = 4):
    target = set(model.get("R", set())) or set(model["STATE"])
    for b_obs in range(max_obs + 1):
        for b_act in range(max_act + 1):
            missing, _ = reconstruct(model, b_obs, b_act, horizon=8, target=target)
            if not missing:
                return b_obs, b_act
    return None


def _failures_for_model(name: str) -> list[str]:
    model = extract_equation_model(MODELS[name])
    relevance = compute_transition_closed_relevance(model)
    failures: list[str] = []

    terminals = list(model.terminals.values())
    terminal = terminals[0] if len(terminals) == 1 else None
    if terminal is None:
        failures.append(f"{name}: expected exactly one #Completion equation")
    else:
        terminal_refs = equation_refs(model, terminal)
        unresolved = sorted(
            ref for ref in terminal_refs
            if ref not in model.state
            and ref not in model.actions
            and ref not in model.definitions
        )
        if unresolved:
            failures.append(
                f"{name}: #Completion equation has unresolved refs {unresolved}"
            )

    for diag in model.diagnostics:
        if diag.severity == "error" or diag.code in BLOCKED_DIAGNOSTICS:
            failures.append(f"{name}: blocked diagnostic emitted: {diag.pretty()}")
    for eq in model.all_equations():
        unresolved_raw = sorted(eq.raw_refs() - model.constants)
        if unresolved_raw:
            failures.append(
                f"{name}: {eq.target} has unresolved raw refs {unresolved_raw}"
            )

    if name == "mixing":
        inlet = model.definitions.get("fillingTank_inlet_flowRateMl")
        if inlet is None:
            failures.append("mixing: missing fillingTank_inlet_flowRateMl definition")
        elif not isinstance(inlet.expr, Op) or inlet.expr.op != "+":
            failures.append(
                "mixing: fillingTank_inlet_flowRateMl is not an additive equation"
            )

        fill_transition = model.transitions.get("fillingTank_currentLevelMl")
        if fill_transition is None:
            failures.append("mixing: missing fillingTank_currentLevelMl transition")
        else:
            refs = equation_refs(model, fill_transition)
            expected = {
                "pump1_isRunning",
                "pump2_isRunning",
                "valve1_isOpen",
                "valve2_isOpen",
            }
            missing = sorted(expected - refs)
            if missing:
                failures.append(
                    "mixing: fillingTank transition does not depend on both "
                    f"feeder actuator paths; missing {missing}"
                )

        if "fillingTank_currentLevelMl" in relevance.q:
            failures.append(
                "mixing: fillingTank_currentLevelMl entered q even though no "
                "current requirement/observation reads it"
            )
        reason = relevance.ignored_state_reasons.get("fillingTank_currentLevelMl", "")
        if "backward cone" not in reason:
            failures.append(
                "mixing: fillingTank_currentLevelMl lacks a noninterference reason"
            )

    if name == "thermostat":
        for target in ("heater_heatOut_heat_rateWatts", "ac_heatOut_heat_rateWatts"):
            eq = model.transitions.get(target)
            if eq is None:
                failures.append(f"thermostat: missing actuator output transition {target}")

    if name == "cruise-discrete":
        for target, action in (
            ("engine_forceOut_drive_forceNewtons", "controller_policyCall_applyThrottle"),
            ("brake_forceOut_drive_forceNewtons", "controller_policyCall_applyBrake"),
        ):
            eq = model.transitions.get(target)
            if eq is None:
                failures.append(f"cruise-discrete: missing actuator output transition {target}")
                continue
            if action not in eq.refs():
                failures.append(
                    f"cruise-discrete: {target} does not reference policy action {action}"
                )

    for variable in relevance.q:
        entries = model.value_semantics.get('decision_updates', {}).get(variable, [])
        if len(entries) != 1 or not entries[0].get('runtime_key'):
            failures.append(f'{name}: relevant state has no unique source storage: {variable}')

    return failures


def _certificate_failures_for_model(name: str, out_dir: Path) -> list[str]:
    failures: list[str] = []
    cert = build_certificate_for_path(MODELS[name], dt=TEST_DT)
    expected = EXPECTED_STRICT_CLOSURE[name]
    observed = None if cert["buffer"] is None else (
        cert["buffer"]["b_obs"], cert["buffer"]["b_act"]
    )
    if observed != expected:
        failures.append(
            f"{name}: certificate buffer changed; expected {expected}, observed {observed}"
        )

    path = out_dir / f"{name}.certificate.json"
    write_certificate(cert, path)
    loaded = load_certificate(path)
    errors = check_certificate(loaded)
    if errors:
        failures.append(f"{name}: certificate checker failed: {errors}")

    if cert["claim"]["profile_mdp_theorem"] != "discharged":
        failures.append(
            f"{name}: profile MDP theorem is not discharged"
        )
    if cert["claim"]["mdp_theorem"] != "discharged":
        failures.append(
            f"{name}: solver-backed MDP theorem is not discharged"
        )
    if cert["claim"]["solver_backed_mdp_theorem"] != "discharged":
        failures.append(
            f"{name}: solver-backed theorem claim is not discharged"
        )

    obligations = cert["mdp_obligations"]
    if "terminal_state" not in obligations:
        failures.append(f"{name}: certificate missing terminal_state obligation")
    elif not obligations["terminal_state"]["equations"]:
        failures.append(f"{name}: certificate terminal_state obligation has no equations")
    if obligations.get("truncation", {}).get("status") != "discharged_as_finite_horizon_augmented_state":
        failures.append(f"{name}: certificate missing discharged finite-horizon truncation model")
    if obligations.get("reward", {}).get("status") != "discharged_over_augmented_state":
        failures.append(f"{name}: certificate reward obligation is not discharged")
    if obligations.get("done", {}).get("status") != "discharged_over_augmented_state":
        failures.append(f"{name}: certificate done obligation is not discharged")
    shield = obligations.get("shield", {})
    if shield.get("status") != "discharged":
        failures.append(f"{name}: certificate shield obligation is not discharged")
    if shield.get("semantic_status") != "discharged_discrete_exact_ast":
        failures.append(
            f"{name}: unexpected shield semantic status {shield.get('semantic_status')}"
        )
    if obligations.get("overall_mdp_theorem_status") != PROFILE_OBLIGATIONS_DISCHARGED:
        failures.append(f"{name}: certificate profile obligations are not discharged")
    gate = cert.get("theorem_gate", {})
    if gate.get("profile_mdp_theorem") != "discharged":
        failures.append(f"{name}: theorem gate profile theorem is not discharged")
    if gate.get("solver_backed_mdp_theorem") != "discharged":
        failures.append(f"{name}: theorem gate solver-backed theorem is not discharged")
    if gate.get("full_mdp_theorem_claim_allowed") is not True:
        failures.append(f"{name}: theorem gate does not allow solver-backed MDP claim")
    if gate.get("full_mdp_theorem_blockers"):
        failures.append(f"{name}: theorem gate records unexpected blockers")
    if gate.get("solver_backed_uniqueness", {}).get("status") != "discharged":
        failures.append(f"{name}: theorem gate solver uniqueness is not discharged")
    source_proof = cert.get('proof') or {}
    history = cert.get('solver_advisory', {}).get('source_history_reconstruction', {})
    if source_proof.get('proof_engine') != 'source_history_v1' or source_proof.get('passes') is not True:
        failures.append(f'{name}: source-history proof does not pass')
    if history.get('status') != 'discharged' or history.get('claim') != 'source_history_reconstruction':
        failures.append(f'{name}: source-history reconstruction is not discharged')
    if source_proof.get('target') != cert.get('sets', {}).get('q'):
        failures.append(f'{name}: source reconstruction target differs from q')
    solver = cert.get("solver_advisory", {}).get("one_step_transition_closure", {})
    if solver.get("status") != EXPECTED_SOLVER_ADVISORY[name]:
        failures.append(
            f"{name}: unexpected solver advisory status {solver.get('status')}"
        )
    if solver.get("status") == "discharged":
        if solver.get("claim") != "one_step_transition_closure":
            failures.append(f"{name}: discharged solver advisory has wrong claim")
    elif solver.get("claim") != "not_claimed_by_this_artifact":
        failures.append(f"{name}: non-discharged solver advisory overclaims")

    for section, equations in cert.get("equations", {}).items():
        for eq in equations:
            if eq.get("unresolved_raw_refs"):
                failures.append(
                    f"{name}: certificate {section}.{eq.get('target')} has unresolved raw refs"
                )

    ignored_state = set(cert.get("sets", {}).get("ignored_state", []))
    ignored_reasons = cert.get("sets", {}).get("ignored_state_reasons", {})
    for var in ignored_state:
        if "backward cone" not in ignored_reasons.get(var, ""):
            failures.append(f"{name}: ignored state lacks noninterference reason: {var}")

    return failures


def main() -> int:
    names = ("mixing", "thermostat", "cruise-discrete")
    failures: list[str] = []
    for name in names:
        failures.extend(_failures_for_model(name))
    with tempfile.TemporaryDirectory(prefix="strict-q-cert-") as tmp:
        out_dir = Path(tmp)
        for name in names:
            failures.extend(_certificate_failures_for_model(name, out_dir))

    if failures:
        print("REFERENCE MODEL VALIDATION FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("REFERENCE MODEL VALIDATION PASSED")
    for name in names:
        model = extract_equation_model(MODELS[name])
        relevance = compute_transition_closed_relevance(model)
        print(
            f"  - {name}: state={len(model.state)} def_eq={len(model.definitions)} "
            f"transition_eq={len(model.transitions)} q={len(relevance.q)}"
        )
    print("  - certificate artifacts generated and independently checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
