"""Positive and negative SysML model cases."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from clarity.certification.certificate import (
    PROFILE_OBLIGATIONS_DISCHARGED,
    build_certificate_for_path,
    check_certificate,
    load_certificate,
    write_certificate,
)
from clarity.certification.relevance import (
    compute_transition_closed_relevance,
    equation_refs,
)
from clarity.certification.strict_extract import extract_equation_model
from clarity.certification.reconstruct import get_strict_model, reconstruct

from .support import Battery, EXPECTED, MODELS, TEST_DT, first_closure


def validate_positive_model(
    battery: Battery, name: str, cert_dir: Path
) -> dict[str, Any]:
    expected = EXPECTED[name]
    model = extract_equation_model(MODELS[name])
    relevance = compute_transition_closed_relevance(model)
    terminals = list(model.terminals.values())
    terminal = terminals[0] if len(terminals) == 1 else None
    terminal_refs = set()
    if terminal is not None:
        terminal_refs = {
            ref for ref in equation_refs(model, terminal)
            if ref in model.state
        }

    battery.require(
        f"{name}/shape",
        len(model.state) == expected["state"]
        and len(model.definitions) == expected["definitions"]
        and len(model.transitions) == expected["transitions"]
        and len(relevance.q) == expected["q"],
        (
            f"state={len(model.state)} def={len(model.definitions)} "
            f"trans={len(model.transitions)} q={len(relevance.q)}"
        ),
    )
    battery.require(
        f"{name}/terminal_refs",
        terminal_refs == expected["terminal_state_refs"],
        f"terminal_state_refs={sorted(terminal_refs)}",
    )
    blocked_diags = [
        diag.pretty()
        for diag in model.diagnostics
        if diag.severity in {"warning", "error"}
    ]
    unresolved_raw = {
        eq.target: sorted(eq.raw_refs() - model.constants)
        for eq in model.all_equations()
        if eq.raw_refs() - model.constants
    }
    missing_ignored_reasons = sorted(
        var for var in relevance.ignored_state
        if "backward cone" not in relevance.ignored_state_reasons.get(var, "")
    )
    battery.require(
        f"{name}/equation_audit",
        not blocked_diags and not unresolved_raw and not missing_ignored_reasons,
        (
            f"blocked_diags={blocked_diags[:3]} "
            f"unresolved_raw={unresolved_raw} "
            f"missing_ignored_reasons={missing_ignored_reasons}"
        ),
    )

    strict_model = get_strict_model(MODELS[name], dt=TEST_DT)
    observed_buffer = first_closure(strict_model)
    battery.require(
        f"{name}/first_closure",
        observed_buffer == expected["buffer"],
        f"expected={expected['buffer']} observed={observed_buffer}",
    )

    exp_obs, exp_act = expected["buffer"]
    target = set(strict_model.get("R", set())) or set(strict_model["STATE"])
    prior_failures = []
    for b_obs in range(exp_obs + 1):
        for b_act in range(5):
            if (b_obs, b_act) >= (exp_obs, exp_act):
                continue
            try:
                missing, _ = reconstruct(strict_model, b_obs, b_act, horizon=8, target=target)
            except ValueError:
                continue  # Missing source transitions are not a successful buffer.
            if not missing:
                prior_failures.append((b_obs, b_act))
    battery.require(
        f"{name}/smaller_buffers_fail",
        not prior_failures,
        f"unexpected earlier passing buffers={prior_failures}",
    )

    cert = build_certificate_for_path(MODELS[name], dt=TEST_DT)
    cert_path = cert_dir / f"{name}.certificate.json"
    write_certificate(cert, cert_path)
    disk_cert = load_certificate(cert_path)
    errors = check_certificate(disk_cert)
    buffer = None if cert["buffer"] is None else (
        cert["buffer"]["b_obs"], cert["buffer"]["b_act"]
    )
    obligations = cert["mdp_obligations"]
    gate = cert["theorem_gate"]
    equation_proof = cert["equation_proof"] or {}
    ignored_state = set(cert["sets"]["ignored_state"])
    ignored_reasons = cert["sets"]["ignored_state_reasons"]
    one_step_solver = cert["solver_advisory"]["one_step_transition_closure"]
    certificate_unresolved_raw = [
        (section, eq["target"], eq.get("unresolved_raw_refs", []))
        for section, equations in cert["equations"].items()
        for eq in equations
        if eq.get("unresolved_raw_refs")
    ]
    battery.require(
        f"{name}/certificate",
        not errors
        and cert["result"] == "PASS"
        and cert["claim"]["profile_mdp_theorem"] == "discharged"
        and cert["claim"]["mdp_theorem"] == "discharged"
        and cert["claim"]["solver_backed_mdp_theorem"] == "discharged"
        and buffer == expected["buffer"]
        and obligations["terminal_state"]["status"] == "covered_by_q_and_action"
        and obligations["truncation"]["status"] == "discharged_as_finite_horizon_augmented_state"
        and obligations["reward"]["status"] == "discharged_over_augmented_state"
        and obligations["done"]["status"] == "discharged_over_augmented_state"
        and obligations["shield"]["status"] == "discharged"
        and obligations["shield"]["semantic_status"] == "discharged_discrete_exact_ast"
        and obligations["overall_mdp_theorem_status"] == PROFILE_OBLIGATIONS_DISCHARGED
        and gate["profile_mdp_theorem"] == "discharged"
        and gate["solver_backed_mdp_theorem"] == "discharged"
        and gate["profile_obligations_discharged"] is True
        and gate["full_mdp_theorem_claim_allowed"] is True
        and not gate["full_mdp_theorem_blockers"]
        and gate["solver_backed_uniqueness"]["status"] == "discharged"
        and gate["solver_backed_uniqueness"]["result_status"] == "discharged"
        and cert["search"]["legacy_and_equation_search_agree"] is True
        and equation_proof["proof_engine"] == "equation_ir_syntactic_v1"
        and equation_proof["passes"] is True
        and not equation_proof["missing_target_facts"]
        and one_step_solver["status"] == expected["solver_status"]
        and (
            one_step_solver["claim"] == "one_step_transition_closure"
            if one_step_solver["status"] == "discharged"
            else one_step_solver["claim"] == "not_claimed_by_this_artifact"
        )
        and not any(
            fact["rule"] == "equation_guarded_copy_inversion"
            and fact["detail"].get("guard_status") != "proven_true"
            for fact in equation_proof["facts"]
        )
        and not certificate_unresolved_raw
        and all(
            "backward cone" in ignored_reasons.get(var, "")
            for var in ignored_state
        ),
        (
            f"buffer={buffer} checker_errors={errors} "
            f"solver={one_step_solver['status']} "
            f"unresolved_raw={certificate_unresolved_raw[:3]}"
        ),
    )
    return cert

def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise AssertionError(f"{label}: expected one replacement target, found {count}")
    return text.replace(old, new, 1)


def _write_temp_model(src: str, new_text: str, tmp: Path, name: str) -> str:
    model_dir = tmp / name
    model_dir.mkdir(parents=True, exist_ok=True)
    path = model_dir / "model.sysml"
    path.write_text(new_text, encoding="utf-8")
    return str(path)


def run_negative_model_cases(battery: Battery) -> None:
    with tempfile.TemporaryDirectory(prefix="cert-battery-models-") as td:
        tmp = Path(td)

        thermostat_text = Path(MODELS["thermostat"]).read_text(encoding="utf-8")
        thermostat_no_done = _replace_once(
            thermostat_text,
            (
                "                in done := setPointCelcius <= reading.temperatureCelcius + toleranceCelcius and\n"
                "                setPointCelcius >= reading.temperatureCelcius - toleranceCelcius and not heaterOn and not acOn;\n"
            ),
            "",
            "thermostat remove done binding",
        )
        thermostat_no_done_path = _write_temp_model(
            MODELS["thermostat"], thermostat_no_done, tmp, "thermostat-no-done"
        )
        cert = build_certificate_for_path(thermostat_no_done_path, dt=TEST_DT)
        errors = check_certificate(cert, check_hash=False)
        blocking_codes = {d["code"] for d in cert["diagnostics"]["blocking"]}
        battery.require(
            "negative_model/missing_terminal_binding",
            cert["result"] != "PASS"
            and errors
            and "missing_terminal_binding" in blocking_codes,
            f"result={cert['result']} blocking={sorted(blocking_codes)} errors={errors[:3]}",
        )

        mixing_text = Path(MODELS["mixing"]).read_text(encoding="utf-8")
        hidden_done = _replace_once(
            mixing_text,
            (
                "                in done := (tank1OriginalLevelMl - volume1Res.response >= tank1TransferMl) and\n"
                "                            (tank2OriginalLevelMl - volume2Res.response >= tank2TransferMl);\n"
            ),
            "                in done := fillingTank.currentLevelMl >= 0;\n",
            "mixing hidden terminal dependency",
        )
        hidden_done_path = _write_temp_model(
            MODELS["mixing"], hidden_done, tmp, "mixing-hidden-terminal"
        )
        cert = build_certificate_for_path(hidden_done_path, dt=TEST_DT)
        errors = check_certificate(cert, check_hash=False)
        battery.require(
            "negative_model/hidden_terminal_dependency",
            cert["result"] != "PASS" and errors,
            f"result={cert['result']} buffer={cert['buffer']} errors={errors[:3]}",
        )

        thermostat_no_neural_req = _replace_once(
            thermostat_text,
            "#NeuralRequirement requirement def 'Neural Controller Soundness'",
            "requirement def 'Neural Controller Soundness'",
            "thermostat remove NeuralRequirement metadata",
        )
        thermostat_no_neural_req_path = _write_temp_model(
            MODELS["thermostat"],
            thermostat_no_neural_req,
            tmp,
            "thermostat-no-neural-requirement",
        )
        cert = build_certificate_for_path(thermostat_no_neural_req_path, dt=TEST_DT)
        errors = check_certificate(cert, check_hash=False)
        shield = cert["mdp_obligations"]["shield"]
        battery.require(
            "negative_model/missing_neural_requirement",
            errors
            and shield["status"] == "not_discharged"
            and shield["semantic_status"] == "missing_neural_requirement_ast",
            f"shield={shield.get('semantic_status')} errors={errors[:3]}",
        )

        thermostat_unknown_shield_ref = _replace_once(
            thermostat_text,
            "(not (p.heaterState and p.acState))",
            "((not (p.heaterState and p.acState)) and p.unmappedHiddenInput)",
            "thermostat unknown shield ref",
        )
        thermostat_unknown_shield_ref_path = _write_temp_model(
            MODELS["thermostat"],
            thermostat_unknown_shield_ref,
            tmp,
            "thermostat-unknown-shield-ref",
        )
        cert = build_certificate_for_path(
            thermostat_unknown_shield_ref_path, dt=TEST_DT
        )
        errors = check_certificate(cert, check_hash=False)
        shield = cert["mdp_obligations"]["shield"]
        battery.require(
            "negative_model/unknown_shield_ref",
            errors
            and shield["status"] == "not_discharged"
            and shield["semantic_status"] == "unknown_requirement_refs",
            f"unknown={shield.get('unknown_requirement_refs')} errors={errors[:3]}",
        )

        thermostat_unparsed_requirement = _replace_once(
            thermostat_text,
            (
                "                currentTime > 0 implies \n"
                "                    (s.lastObservedTemperature < s.controller.setPointCelcius - s.controller.toleranceCelcius\n"
                "                        implies s.controller.heaterOn)\n"
            ),
            "                @unparsedRequirementExpression\n",
            "thermostat unparsed requirement expression",
        )
        thermostat_unparsed_requirement_path = _write_temp_model(
            MODELS["thermostat"],
            thermostat_unparsed_requirement,
            tmp,
            "thermostat-unparsed-requirement",
        )
        try:
            build_certificate_for_path(thermostat_unparsed_requirement_path, dt=TEST_DT)
        except ValueError as exc:
            battery.require(
                "negative_model/unparsed_requirement",
                "cannot parse SysML requirement 'Heat When Cold'" in str(exc)
                and "@unparsedRequirement" in str(exc),
                f"source requirement rejected before certification: {exc}",
            )
        else:
            battery.require(
                "negative_model/unparsed_requirement", False,
                "malformed source requirement was not rejected by the parser",
            )

    strict_model = get_strict_model(
        MODELS["mixing"], dt=TEST_DT, enable_sampled_memory=False
    )
    battery.require(
        "negative_closure/mixing_without_sampled_memory",
        first_closure(strict_model) is None,
        "mixing must not certify if sampled-memory rules are disabled",
    )
