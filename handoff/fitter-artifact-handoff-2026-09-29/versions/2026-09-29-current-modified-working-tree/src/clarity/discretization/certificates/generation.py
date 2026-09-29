"""Generate discretization certificates from checked preprocessing inputs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from clarity.certification.certificate import (
    check_certificate as check_mdp_certificate,
    load_certificate as load_mdp_certificate,
    model_hash,
)
from clarity.certification.reduced_mdp_spec import (
    check_reduced_mdp_spec,
    load_reduced_mdp_spec,
)

from ..analysis import analyze_model
from ..timing import canonical_dt
from .compaction import compact_analysis
from .io import (
    CERTIFICATE_KIND,
    SCHEMA_VERSION,
    canonical_json_bytes,
    certificate_hash,
    file_sha256,
    sha256_bytes,
)


def _validate_inputs(
    model_path: Path,
    mdp_path: Path,
    reduced_spec_path: Path,
    dt_text: str,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    errors: list[str] = []
    mdp_certificate = load_mdp_certificate(mdp_path)
    errors.extend(
        f"Markov process certificate: {error}"
        for error in check_mdp_certificate(mdp_certificate, check_hash=True)
    )
    if mdp_certificate.get("theorem_gate", {}).get(
        "full_mdp_theorem_claim_allowed"
    ) is not True:
        errors.append("Markov process certificate does not allow its full claim")
    if mdp_certificate.get("claim", {}).get("solver_backed_mdp_theorem") != (
        "discharged"
    ):
        errors.append("Markov process certificate claim is not discharged")

    reduced_spec = load_reduced_mdp_spec(reduced_spec_path)
    errors.extend(
        f"reduced specification: {error}"
        for error in check_reduced_mdp_spec(reduced_spec)
    )

    model_abs = str(model_path.resolve())
    observed_model_hash = model_hash(model_abs)
    for label, source in (
        ("Markov process certificate", mdp_certificate.get("model", {})),
        ("reduced specification", reduced_spec.get("model", {})),
    ):
        if os.path.abspath(str(source.get("path", ""))) != model_abs:
            errors.append(f"{label} model path does not match")
        if source.get("sha256") != observed_model_hash:
            errors.append(f"{label} model hash does not match")

    requested_dt = canonical_dt(dt_text)
    try:
        mdp_dt = canonical_dt(mdp_certificate.get("settings", {}).get("dt"))
        if mdp_dt["canonical"] != requested_dt["canonical"]:
            errors.append("Markov process certificate dt does not match")
    except (TypeError, ValueError, ZeroDivisionError):
        errors.append("Markov process certificate dt is invalid")
    try:
        spec_dt = canonical_dt(reduced_spec.get("model", {}).get("dt"))
        if spec_dt["canonical"] != requested_dt["canonical"]:
            errors.append("reduced specification dt does not match")
    except (TypeError, ValueError, ZeroDivisionError):
        errors.append("reduced specification dt is invalid")
    return mdp_certificate, reduced_spec, errors


def build_certificate(
    model_path: str | Path,
    mdp_certificate_path: str | Path,
    reduced_spec_path: str | Path,
    *,
    dt_text: str,
    optimization_timeout_ms: int = 250,
    smt_timeout_ms: int = 30000,
) -> dict[str, Any]:
    model = Path(model_path).resolve()
    mdp_path = Path(mdp_certificate_path).resolve()
    spec_path = Path(reduced_spec_path).resolve()
    mdp_certificate, reduced_spec, input_errors = _validate_inputs(
        model,
        mdp_path,
        spec_path,
        dt_text,
    )
    if input_errors:
        analysis = {
            "schema_version": 5,
            "result": "NOT_CERTIFIED",
            "claim": "full_sysml_discretization_safety_preservation_v5",
            "properties": [],
            "blocking_diagnostics": input_errors,
        }
    else:
        analysis = analyze_model(
            model,
            mdp_certificate,
            dt_text=dt_text,
            optimization_timeout_ms=optimization_timeout_ms,
            smt_timeout_ms=smt_timeout_ms,
        )
        analysis = compact_analysis(analysis)

    from .verification.preservation import required_preservation_inventory
    certificate: dict[str, Any] = {
        'preservation': {'inventory': required_preservation_inventory(model), 'evidence': {}},
        "schema_version": SCHEMA_VERSION,
        "execution": mdp_certificate.get("execution"),
        "kind": CERTIFICATE_KIND,
        "result": "VIOLATION" if analysis.get("result") == "VIOLATION" else "NOT_CERTIFIED",
        "claim": {
            "name": "full_sysml_discretization_safety_preservation_v5",
            "status": "not_discharged",
        },
        "model": {
            "path": str(model),
            "sha256": model_hash(model),
        },
        "settings": {
            "dt": canonical_dt(dt_text),
            "optimization_timeout_ms": int(optimization_timeout_ms),
            "smt_timeout_ms": int(smt_timeout_ms),
        },
        "markov_process_certificate": {
            "path": str(mdp_path),
            "file_sha256": file_sha256(mdp_path),
            "canonical_sha256": sha256_bytes(canonical_json_bytes(mdp_certificate)),
        },
        "reduced_specification": {
            "path": str(spec_path),
            "file_sha256": file_sha256(spec_path),
            "canonical_sha256": sha256_bytes(canonical_json_bytes(reduced_spec)),
            "self_sha256": reduced_spec.get("self_sha256"),
        },
        "analysis": analysis,
    }
    certificate["self_sha256"] = certificate_hash(certificate)
    return certificate
