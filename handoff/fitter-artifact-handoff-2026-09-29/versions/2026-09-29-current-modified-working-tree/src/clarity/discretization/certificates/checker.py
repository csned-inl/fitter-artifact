"""Verify discretization certificates independently of certificate generation."""

from __future__ import annotations

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

from ..timing import canonical_dt
from .compaction import expand_analysis
from .io import (
    CERTIFICATE_KIND,
    SCHEMA_VERSION,
    canonical_json_bytes,
    certificate_hash,
    file_sha256,
    sha256_bytes,
)
from .verification import verify_recorded_optimization_certificates
from .verification.source import verify_analysis_source
from .verification.completeness import validate_record_shape, verify_completeness


def _check_certificate(
    certificate: dict[str, Any],
    *,
    check_files: bool,
    analysis: dict[str, Any],
    case_results: dict,
    inventory: dict,
) -> list[str]:
    errors: list[str] = []
    if certificate.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version={certificate.get('schema_version')}")
    if certificate.get("kind") != CERTIFICATE_KIND:
        errors.append(f"unsupported kind={certificate.get('kind')}")
    if certificate.get("self_sha256") != certificate_hash(certificate):
        errors.append("self_sha256 does not match certificate contents")
    execution = certificate.get("execution")
    if not isinstance(execution, dict) or execution.get("version") != 1:
        errors.append("missing ordered execution correspondence")
    result = certificate.get("result")
    if result not in {"CERTIFIED", "NOT_CERTIFIED", "VIOLATION"}:
        errors.append(f"certificate result is invalid: {result}")
    expected_status = "discharged" if result == "CERTIFIED" else "not_discharged"
    if certificate.get("claim", {}).get("status") != expected_status:
        errors.append("certificate claim status does not match its result")
    errors.extend(verify_recorded_optimization_certificates(analysis, case_results=case_results))

    model_info = certificate.get("model", {})
    model_path = Path(str(model_info.get("path", "")))
    mdp_info = certificate.get("markov_process_certificate", {})
    mdp_path = Path(str(mdp_info.get("path", "")))
    spec_info = certificate.get("reduced_specification", {})
    spec_path = Path(str(spec_info.get("path", "")))
    if not check_files:
        return errors
    for label, path in (
        ("model", model_path),
        ("Markov process certificate", mdp_path),
        ("reduced specification", spec_path),
    ):
        if not path.is_file():
            errors.append(f"{label} path does not exist: {path}")
    if not all(path.is_file() for path in (model_path, mdp_path, spec_path)):
        return errors

    from clarity.certification.ordered_execution import validate_execution_description
    errors.extend(validate_execution_description(execution, model_path))
    if model_hash(model_path) != model_info.get("sha256"):
        errors.append("model sha256 does not match certificate")
    if file_sha256(mdp_path) != mdp_info.get("file_sha256"):
        errors.append("Markov process certificate file sha256 does not match")
    if file_sha256(spec_path) != spec_info.get("file_sha256"):
        errors.append("reduced specification file sha256 does not match")

    mdp_certificate = load_mdp_certificate(mdp_path)
    if sha256_bytes(canonical_json_bytes(mdp_certificate)) != mdp_info.get(
        "canonical_sha256"
    ):
        errors.append("Markov process certificate canonical sha256 does not match")
    errors.extend(
        f"Markov process certificate: {error}"
        for error in check_mdp_certificate(mdp_certificate, check_hash=True)
    )

    reduced_spec = load_reduced_mdp_spec(spec_path)
    if sha256_bytes(canonical_json_bytes(reduced_spec)) != spec_info.get(
        "canonical_sha256"
    ):
        errors.append("reduced specification canonical sha256 does not match")
    if reduced_spec.get("self_sha256") != spec_info.get("self_sha256"):
        errors.append("reduced specification self sha256 does not match")
    errors.extend(
        f"reduced specification: {error}"
        for error in check_reduced_mdp_spec(reduced_spec)
    )

    dt_record = certificate.get("settings", {}).get("dt", {})
    try:
        dt_text = str(dt_record["input"])
        if canonical_dt(dt_text) != dt_record:
            errors.append("certificate dt record is not canonical")
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        errors.append("certificate dt record is invalid")
        return errors
    for name in ("optimization_timeout_ms", "smt_timeout_ms"):
        try:
            if int(certificate.get("settings", {}).get(name)) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"certificate {name.removesuffix('_ms').replace('_', ' ')} is invalid")
            return errors
    errors.extend(
        verify_analysis_source(
            str(model_path),
            mdp_certificate,
            dt_record,
            analysis,
            inventory=inventory,
        )
    )
    return errors


def verify_certificate(certificate: dict[str, Any], *, check_files: bool = True) -> dict[str, Any]:
    """Return evidence validity and source-verified safety as separate outcomes."""
    errors: list[str] = []
    case_results: dict = {}
    inventory: dict = {}
    completion = {"result": "NOT_CERTIFIED", "properties": []}
    preservation = {'verified': False, 'unproved': [], 'errors': []}
    try:
        analysis, expansion_errors = expand_analysis(certificate.get("analysis", {}))
        errors.extend(expansion_errors)
        errors.extend(validate_record_shape(analysis))
        if not errors:
            errors.extend(_check_certificate(
                certificate, check_files=check_files, analysis=analysis,
                case_results=case_results, inventory=inventory,
            ))
            completion = verify_completeness(
                analysis, case_results, inventory if check_files else None,
            )
            errors.extend(completion["errors"])
            if check_files:
                from .verification.preservation import verify_preservation_inventory
                preservation = verify_preservation_inventory(
                    certificate.get('preservation'), certificate.get('model', {}).get('path', ''))
                errors.extend(preservation['errors'])
            full_result = completion['result']
            if full_result == 'CERTIFIED' and not preservation['verified']:
                full_result = 'NOT_CERTIFIED'
            if certificate.get("result") != full_result:
                errors.append("certificate result disagrees with verified source preservation result")
    except (AttributeError, KeyError, TypeError, ValueError, IndexError,
            ZeroDivisionError, RecursionError, OSError) as exc:
        errors.append(f"malformed or unreadable certificate evidence: {exc}")
    valid = not errors
    predicate_result = completion['result'] if valid else 'NOT_CERTIFIED'
    result = predicate_result
    if result == 'CERTIFIED' and not preservation['verified']:
        result = 'NOT_CERTIFIED' 
    properties = completion["properties"]
    return {
        "schema_version": 2,
        "kind": "discretization_safety_verification_v2",
        "certificate_sha256": certificate.get("self_sha256") if isinstance(certificate, dict) else None,
        "valid": valid,
        'predicate_subproof_result': predicate_result,
        'preservation': preservation,
        "source_verified": bool(check_files and valid and preservation['verified']),
        "safety_certified": bool(check_files and valid and result == "CERTIFIED"),
        "result": result,
        "properties_checked": len(properties),
        "properties_certified": sum(prop["result"] == "CERTIFIED" for prop in properties) if valid and preservation["verified"] else 0,
        "obligations_checked": sum(len(prop["obligations"]) for prop in properties),
        "obligations_certified": sum(item["result"] == "CERTIFIED" for prop in properties
                                     for item in prop["obligations"]) if valid and preservation["verified"] else 0,
        "properties": properties,
        "errors": errors,
    }


def check_certificate(certificate: dict[str, Any], *, check_files: bool = True) -> list[str]:
    """Compatibility API: structural validity does not imply safety approval."""
    return verify_certificate(certificate, check_files=check_files)["errors"]
