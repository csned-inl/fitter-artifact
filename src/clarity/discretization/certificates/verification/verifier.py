"""Dispatch independent checks over a complete discretization analysis record."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .convex import verify_recorded_convex_certificate, verify_recorded_outer_reduction
from .expressions import _serialized_expression_hash
from .factored import _verify_lazy_factored_stage
from .linear import verify_recorded_linear_certificate, verify_recorded_linear_counterexample
from .reachability import (
    _verify_relational_invariant_stage,
    _verify_shared_reachability,
    _verify_smt_reachability_stage,
    _verify_smt_stage,
)
from .reduction import (
    _verify_common_case_group,
    _verify_constraint_removals,
    _verify_linear_certificate_source,
    _verify_merged_case_sources,
)


def _resolve_shared_proof_certificates(
    analysis: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    record = analysis.get("shared_proof_certificates")
    if record is None:
        return analysis, []
    if not isinstance(record, dict) or record.get(
        "rule"
    ) != "content_addressed_proof_certificate_pool_v1":
        return analysis, ["shared proof certificate pool is malformed"]
    pool = record.get("certificates")
    if not isinstance(pool, dict):
        return analysis, ["shared proof certificates are malformed"]
    errors: list[str] = []
    for key, certificate in pool.items():
        if not isinstance(certificate, dict):
            errors.append("shared proof certificate is malformed")
            continue
        observed = hashlib.sha256(json.dumps(
            certificate,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        if key != observed:
            errors.append("shared proof certificate hash is invalid")

    resolved = deepcopy(analysis)
    reference_count = 0

    def resolve(value: Any) -> Any:
        nonlocal reference_count
        if isinstance(value, dict):
            if "shared_certificate_sha256" in value:
                if set(value) != {"shared_certificate_sha256"}:
                    errors.append("shared proof certificate reference is malformed")
                    return value
                key = value.get("shared_certificate_sha256")
                certificate = pool.get(key)
                if not isinstance(certificate, dict):
                    errors.append("shared proof certificate reference is missing")
                    return value
                reference_count += 1
                return deepcopy(certificate)
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    resolved["properties"] = resolve(resolved.get("properties", []))
    if record.get("reference_count") != reference_count:
        errors.append("shared proof certificate reference count is invalid")
    return resolved, errors


def verify_recorded_optimization_certificates(analysis: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(analysis, dict):
        return ["recorded analysis is malformed"]
    analysis, resolution_errors = _resolve_shared_proof_certificates(analysis)
    errors.extend(resolution_errors)
    shared_errors, shared_regions, shared_safety_queries = (
        _verify_shared_reachability(analysis)
    )
    errors.extend(shared_errors)
    for property_record in analysis.get("properties", []):
        property_id = property_record.get("property_id", "unknown")
        reduction = property_record.get("reduction") or {}
        if reduction.get("outcome") != "DEFERRED":
            if reduction.get("kind") != "full_sysml_interval_reduction_v3":
                errors.append(f"property {property_id} reduction kind is invalid")
            counterexample = reduction.get("interval_counterexample")
            if not isinstance(counterexample, dict):
                errors.append(f"property {property_id} interval counterexample is missing")
            else:
                observed_hash = hashlib.sha256(json.dumps(
                    counterexample,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")).hexdigest()
                if observed_hash != reduction.get("interval_counterexample_sha256"):
                    errors.append(f"property {property_id} interval counterexample hash is invalid")
            sampled_counterexample = reduction.get("sampled_point_counterexample")
            if not isinstance(sampled_counterexample, dict):
                errors.append(f"property {property_id} sampled point counterexample is missing")
            else:
                sampled_hash = hashlib.sha256(json.dumps(
                    sampled_counterexample,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")).hexdigest()
                if sampled_hash != reduction.get("sampled_point_counterexample_sha256"):
                    errors.append(f"property {property_id} sampled point counterexample hash is invalid")
            pre_case_reduction = reduction.get("pre_case_constraint_reduction") or {}
            if pre_case_reduction.get(
                "rule"
            ) != "recursive_canonical_constraint_reduction_v1":
                errors.append(f"property {property_id} pre case reduction rule is invalid")
            for removal_error in (
                _verify_constraint_removals(
                    pre_case_reduction.get("sampled_point_removed_constraints")
                )
                + _verify_constraint_removals(
                    pre_case_reduction.get("physical_interval_removed_constraints")
                )
            ):
                errors.append(
                    f"property {property_id} pre case reduction: {removal_error}"
                )
            endpoint_checks = reduction.get("endpoint_checks")
            if not isinstance(endpoint_checks, list) or any(
                item.get("matches") is not True for item in endpoint_checks
            ):
                errors.append(f"property {property_id} endpoint checks are incomplete")
            inventory = reduction.get("equation_inventory")
            inventory_by_target = {
                item.get("target"): item
                for item in inventory or []
                if isinstance(item, dict)
            }
            for trajectory in reduction.get("trajectories", []):
                target = trajectory.get("physical_value")
                if inventory_by_target.get(target, {}).get("included") is not True:
                    errors.append(
                        f"property {property_id} physical equation {target} is omitted"
                    )
            for mapping in reduction.get("sensor_to_physical_mappings", []):
                required = [mapping.get("physical_value")] + [
                    item.get("target") for item in mapping.get("equation_path", [])
                ]
                for target in required:
                    if inventory_by_target.get(target, {}).get("included") is not True:
                        errors.append(
                            f"property {property_id} sensor mapping equation {target} is omitted"
                        )
            if any(
                item.get("matched_controller_call_guard") is not True
                for item in reduction.get("sensor_mapping_guard_evidence", [])
            ):
                errors.append(
                    f"property {property_id} sensor mapping guard evidence is incomplete"
                )
            coverage = reduction.get("case_coverage") or {}
            if coverage.get("complete") is not True:
                errors.append(f"property {property_id} case coverage is incomplete")
            coverage_cases = coverage.get("cases") or []
            recorded_cases = property_record.get("cases") or []
            if coverage.get("case_count") != len(coverage_cases):
                errors.append(
                    f"property {property_id} case coverage count is invalid"
                )
            obligations = coverage.get("obligations") or {}
            if set(obligations) != {"sampled_point", "physical_interval"}:
                errors.append(
                    f"property {property_id} obligation coverage is incomplete"
                )
            for obligation, obligation_coverage in obligations.items():
                if not isinstance(obligation_coverage, dict):
                    errors.append(
                        f"property {property_id} {obligation} coverage is malformed"
                    )
                    continue
                obligation_cases = obligation_coverage.get("cases") or []
                if obligation_coverage.get("rule") == "factored_obligation_root_v1":
                    expected_root = reduction.get(
                        "sampled_point_counterexample"
                        if obligation == "sampled_point"
                        else "interval_counterexample"
                    )
                    root = obligation_coverage.get("root_expression")
                    if root != expected_root:
                        errors.append(
                            f"property {property_id} {obligation} factored root does not match the reconstructed obligation"
                        )
                    if not isinstance(root, dict) or obligation_coverage.get(
                        "root_expression_sha256"
                    ) != _serialized_expression_hash(root):
                        errors.append(
                            f"property {property_id} {obligation} factored root hash is invalid"
                        )
                    if len(obligation_cases) != 1:
                        errors.append(
                            f"property {property_id} {obligation} must have exactly one factored root"
                        )
                merged_sources = [
                    source
                    for row in obligation_cases
                    if isinstance(row, dict)
                    for source in row.get("merged_sources", [])
                ]
                if obligation_coverage.get("case_count") != len(
                    obligation_cases
                ):
                    errors.append(
                        f"property {property_id} {obligation} case count is invalid"
                    )
                if obligation_coverage.get("generated_case_count") != (
                    len(obligation_cases) + len(merged_sources)
                ):
                    errors.append(
                        f"property {property_id} {obligation} generated case count is invalid"
                    )
                containment_count = sum(
                    isinstance(source, dict)
                    and source.get("rule") in {
                        "conjunctive_case_containment_v1",
                        "checked_case_containment_v2",
                    }
                    for source in merged_sources
                )
                if obligation_coverage.get(
                    "containment_merged_case_count"
                ) != containment_count:
                    errors.append(
                        f"property {property_id} {obligation} containment count is invalid"
                    )
            if [item.get("case_id") for item in coverage_cases] != [
                item.get("case_id") for item in recorded_cases
            ]:
                errors.append(f"property {property_id} case identifiers do not match coverage")
            coverage_by_id = {item.get("case_id"): item for item in coverage_cases}
            for case in recorded_cases:
                source = coverage_by_id.get(case.get("case_id"), {})
                if source.get("expression") != case.get("expression"):
                    errors.append(
                        f"property {property_id} case {case.get('case_id')} expression does not match coverage"
                    )
                if source.get("reachability_expression") is not None:
                    reachability_hash = hashlib.sha256(json.dumps(
                        source["reachability_expression"],
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")).hexdigest()
                    if reachability_hash != source.get(
                        "reachability_expression_sha256"
                    ):
                        errors.append(
                            f"property {property_id} case {case.get('case_id')} reachability expression hash is invalid"
                        )
                if source.get("time_reduction") != case.get("time_reduction"):
                    errors.append(
                        f"property {property_id} case {case.get('case_id')} time reduction does not match coverage"
                    )
                if source.get("obligation") != case.get("obligation"):
                    errors.append(
                        f"property {property_id} case {case.get('case_id')} obligation does not match coverage"
                    )
                constraint_reduction = source.get("constraint_reduction") or {}
                if constraint_reduction.get(
                    "rule"
                ) != "canonical_conjunction_reduction_v1":
                    errors.append(
                        f"property {property_id} case {case.get('case_id')} constraint reduction rule is invalid"
                    )
                for removal_error in (
                    _verify_constraint_removals(
                        constraint_reduction.get("removed_constraints")
                    )
                    + _verify_constraint_removals(
                        constraint_reduction.get("reachability_removed_constraints")
                    )
                ):
                    errors.append(
                        f"property {property_id} case {case.get('case_id')} constraint reduction: {removal_error}"
                    )
                for merge_error in _verify_merged_case_sources(
                    source,
                    source.get("merged_sources"),
                ):
                    errors.append(
                        f"property {property_id} case {case.get('case_id')} merge: {merge_error}"
                    )
                if isinstance(source.get("expression"), dict):
                    case_hash = hashlib.sha256(json.dumps(
                        source["expression"],
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")).hexdigest()
                    if case_hash != source.get("expression_sha256"):
                        errors.append(
                            f"property {property_id} case {case.get('case_id')} expression hash is invalid"
                        )

        stages = [
            (case, stage)
            for case in property_record.get("cases", [])
            for stage in case.get("progression", [])
        ]
        for case, stage in stages:
            checker = stage.get("checker")
            proof = stage.get("proof") or {}
            if checker == "smt_fallback":
                errors.extend(
                    f"property {property_id} smt fallback certificate: {error}"
                    for error in _verify_smt_stage(stage, case)
                )
                continue
            if checker == "smt_reachability":
                errors.extend(
                    f"property {property_id} smt reachability certificate: {error}"
                    for error in _verify_smt_reachability_stage(stage, case)
                )
                continue
            if checker == "relational_invariant":
                errors.extend(
                    f"property {property_id} relational invariant certificate: {error}"
                    for error in _verify_relational_invariant_stage(
                        stage,
                        case,
                        shared_regions,
                        shared_safety_queries,
                    )
                )
                continue
            if stage.get("outcome") == "VIOLATION":
                if checker != "reachability_linear":
                    errors.append(
                        f"property {property_id} unsupported violation checker {checker}"
                    )
                    continue
                if proof.get("rule") != "exact_finite_prefix_counterexample_v1":
                    errors.append(
                        f"property {property_id} reachability violation proof rule is invalid"
                    )
                    continue
                obligations = proof.get("base_obligations", [])
                replayed = [
                    obligation.get("attempt", {})
                    for obligation in obligations
                    if obligation.get("attempt", {}).get("outcome") == "VIOLATION"
                ]
                if not replayed:
                    errors.append(
                        f"property {property_id} reachability violation has no replayed obligation"
                    )
                for attempt in replayed:
                    for error in verify_recorded_linear_counterexample(
                        attempt.get("proof") or {}
                    ):
                        errors.append(
                            f"property {property_id} reachability counterexample: {error}"
                        )
                continue
            if stage.get("outcome") != "CERTIFIED":
                continue
            if proof.get("rule") == "lazy_factored_formula_coverage_v1":
                errors.extend(
                    f"property {property_id} factored {checker} certificate: {error}"
                    for error in _verify_lazy_factored_stage(stage, case)
                )
                continue
            certificate = proof.get("certificate")
            if checker == "linear":
                if not isinstance(certificate, dict):
                    stage_errors = ["linear proof certificate is missing"]
                else:
                    stage_errors = verify_recorded_linear_certificate(certificate)
                    stage_errors.extend(_verify_linear_certificate_source(
                        certificate,
                        proof,
                        case,
                    ))
            elif checker == "convex":
                if not isinstance(certificate, dict):
                    stage_errors = ["convex proof certificate is missing"]
                else:
                    stage_errors = verify_recorded_convex_certificate(certificate)
            elif checker in {"reachability_linear", "reachability_convex"}:
                method = (
                    "linear"
                    if checker == "reachability_linear"
                    else "convex"
                )
                if proof.get("rule") != "finite_prefix_and_inductive_case_exclusion_v1":
                    stage_errors = ["reachability proof rule is invalid"]
                elif proof.get("method") != method:
                    stage_errors = ["reachability proof method is invalid"]
                else:
                    stage_errors = []
                    certified_depths = [
                        item
                        for item in proof.get("depth_attempts", [])
                        if item.get("proved") is True
                    ]
                    if not certified_depths:
                        stage_errors.append("reachability proof has no certified depth")
                    for depth in certified_depths:
                        obligations = (
                            depth.get("base_obligations", [])
                            + depth.get("induction_obligations", [])
                        )
                        if not obligations:
                            stage_errors.append(
                                "reachability proof has no arithmetic obligations"
                            )
                        for obligation in obligations:
                            attempt = obligation.get("attempt") or {}
                            if obligation.get("outer_case_group") is True:
                                if (
                                    not isinstance(
                                        obligation.get("covered_case_first_id"),
                                        str,
                                    )
                                    or not isinstance(
                                        obligation.get("covered_case_last_id"),
                                        str,
                                    )
                                    or not isinstance(
                                        obligation.get("covered_case_count"),
                                        int,
                                    )
                                    or obligation.get("covered_case_count") <= 1
                                ):
                                    stage_errors.append(
                                        "reachability outer case group coverage is malformed"
                                    )
                            certificate = (attempt.get("proof") or {}).get(
                                "certificate"
                            )
                            attempt_rule = (attempt.get("proof") or {}).get(
                                "rule"
                            )
                            if attempt.get("outcome") != "CERTIFIED":
                                stage_errors.append(
                                    "reachability arithmetic obligation is not certified"
                                )
                            elif attempt_rule == "lazy_factored_formula_coverage_v1":
                                obligation_expression = obligation.get("expression")
                                if not isinstance(obligation_expression, dict):
                                    stage_errors.append(
                                        "reachability factored expression is malformed"
                                    )
                                else:
                                    stage_errors.extend(
                                        "reachability factored certificate: " + error
                                        for error in _verify_lazy_factored_stage(
                                            attempt,
                                            {"expression": obligation_expression},
                                        )
                                    )
                            elif attempt_rule == "exhaustive_case_split_empty_v1":
                                if attempt.get("applicability_checks", {}).get(
                                    "arithmetic_case_count"
                                ) != 0:
                                    stage_errors.append(
                                        "empty reachability split has a nonzero case count"
                                    )
                            elif not isinstance(certificate, dict):
                                stage_errors.append(
                                    "reachability arithmetic certificate is missing"
                                )
                            elif certificate.get("kind") == "linear_infeasibility_weights_v1":
                                stage_errors.extend(
                                    "reachability linear certificate: " + error
                                    for error in verify_recorded_linear_certificate(
                                        certificate
                                    )
                                )
                                stage_errors.extend(
                                    "reachability outer reduction: " + error
                                    for error in verify_recorded_outer_reduction(
                                        attempt.get("proof") or {},
                                        str(obligation.get("expression_sha256", "")),
                                    )
                                )
                            elif certificate.get("kind") == "convex_dual_bound_v1":
                                stage_errors.extend(
                                    "reachability convex certificate: " + error
                                    for error in verify_recorded_convex_certificate(
                                        certificate
                                    )
                                )
                                stage_errors.extend(
                                    "reachability outer reduction: " + error
                                    for error in verify_recorded_outer_reduction(
                                        attempt.get("proof") or {},
                                        str(obligation.get("expression_sha256", "")),
                                    )
                                )
                            else:
                                stage_errors.append(
                                    "reachability certificate kind is invalid"
                                )
            else:
                continue
            if checker in {"linear", "convex"}:
                stage_errors.extend(_verify_common_case_group(proof, case))
            errors.extend(
                f"property {property_id} {checker} certificate: {error}"
                for error in stage_errors
            )
    return errors
