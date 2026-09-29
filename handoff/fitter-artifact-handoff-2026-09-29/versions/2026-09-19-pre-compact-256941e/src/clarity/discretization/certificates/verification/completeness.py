"""Require checked evidence for every source-derived safety obligation."""

from __future__ import annotations

from typing import Any

from .expressions import _serialized_expression_hash

OBLIGATIONS = ("sampled_point", "physical_interval")


def validate_record_shape(analysis: Any) -> list[str]:
    """Validate containers before evidence/source checkers traverse them."""
    if not isinstance(analysis, dict) or not isinstance(analysis.get("properties"), list):
        return ["analysis properties are malformed"]
    errors: list[str] = []
    seen = set()
    for prop in analysis["properties"]:
        if not isinstance(prop, dict):
            errors.append("property record is malformed")
            continue
        name = prop.get("property_id")
        if not isinstance(name, str) or not name or name in seen:
            errors.append("missing or duplicate property identifier")
        else:
            seen.add(name)
        if not isinstance(prop.get("reduction"), dict):
            errors.append(f"property {name} reduction is malformed")
        cases = prop.get("cases")
        if not isinstance(cases, list):
            errors.append(f"property {name} cases are malformed")
            continue
        ids = set()
        for case in cases:
            if not isinstance(case, dict):
                errors.append(f"property {name} case is malformed")
                continue
            key = case.get("case_id")
            if not isinstance(key, str) or not key or key in ids:
                errors.append(f"property {name} missing or duplicate case identifier")
            else:
                ids.add(key)
            if not isinstance(case.get("progression"), list):
                errors.append(f"property {name} case {key} proof attempts are malformed")
    return errors


def aggregate(results: list[str]) -> str:
    if "VIOLATION" in results:
        return "VIOLATION"
    if results and all(result == "CERTIFIED" for result in results):
        return "CERTIFIED"
    return "NOT_CERTIFIED"


def verify_completeness(analysis, case_results, inventory=None):
    """Connect the two required roots to verified evidence and derive results.

    ``inventory`` is supplied by source reconstruction for full verification.
    Structural-only checking can compare records but cannot authorize safety.
    """
    errors: list[str] = []
    records = {prop["property_id"]: prop for prop in analysis["properties"]}
    expected = inventory if inventory is not None else {
        name: {"annotation": prop.get("annotation"), "obligations": {
            "sampled_point": prop["reduction"].get("sampled_point_counterexample"),
            "physical_interval": prop["reduction"].get("interval_counterexample"),
        }} for name, prop in records.items()
    }
    if set(records) != set(expected):
        errors.append("proof inventory does not match the SysML safety requirements")
    properties = []
    for name, requirement in expected.items():
        prop = records.get(name, {})
        reduction = prop.get("reduction", {})
        cases = prop.get("cases", [])
        property_errors = []
        deferred = reduction.get("outcome") == "DEFERRED"
        if deferred and cases:
            property_errors.append("deferred reduction contains proof cases")
        coverage = reduction.get("case_coverage", {})
        coverage_obligations = coverage.get("obligations", {})
        if not deferred and set(coverage_obligations) != set(OBLIGATIONS):
            property_errors.append("must cover both sampled_point and physical_interval")
        if not deferred and (len(cases) != 2 or {c.get("obligation") for c in cases} != set(OBLIGATIONS)):
            property_errors.append("requires exactly one factored root for each safety obligation")
        obligations = []
        expected_coverage_rows = []
        for obligation in OBLIGATIONS:
            obligation_errors = []
            candidates = [case for case in cases if case.get("obligation") == obligation]
            verified = {"result": "NOT_CERTIFIED", "checker": None, "proof_rule": None}
            expression = requirement.get("obligations", {}).get(obligation)
            case_id = None
            if not deferred:
                root = coverage_obligations.get(obligation, {})
                if root.get("rule") != "factored_obligation_root_v1":
                    obligation_errors.append("unsupported obligation coverage rule")
                rows = root.get("cases", [])
                if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                    obligation_errors.append("missing unique root coverage record")
                    rows = []
                expected_coverage_rows.extend(rows)
                if not isinstance(expression, dict):
                    obligation_errors.append("missing source-reconstructed obligation")
                elif root.get("root_expression") != expression or root.get("root_expression_sha256") != _serialized_expression_hash(expression):
                    obligation_errors.append("coverage root does not match the source obligation")
                if len(candidates) != 1:
                    obligation_errors.append("missing unique proof record")
                else:
                    case = candidates[0]
                    case_id = case["case_id"]
                    if case.get("expression") != expression:
                        obligation_errors.append("proof record does not cover the source obligation")
                    if rows and (rows[0].get("case_id") != case_id or rows[0].get("expression") != expression or rows[0].get("obligation") != obligation):
                        obligation_errors.append("coverage record does not identify the required proof")
                    verified = case_results.get((name, case_id), verified)
                    if case.get("result") != verified["result"]:
                        obligation_errors.append("recorded result disagrees with verified evidence")
                    if prop.get("result") == "CERTIFIED" and verified["result"] != "CERTIFIED":
                        obligation_errors.append("no verified successful proof")
            if obligation_errors:
                verified = {**verified, "result": "NOT_CERTIFIED"}
            errors.extend(f"property {name} {obligation}: {error}" for error in obligation_errors)
            obligations.append({
                "obligation": obligation, "case_id": case_id,
                "expression_sha256": _serialized_expression_hash(expression) if isinstance(expression, dict) else None,
                "result": verified["result"], "checker": verified["checker"],
                "proof_rule": verified["proof_rule"], "errors": obligation_errors,
            })
        if not deferred and coverage.get("cases") != expected_coverage_rows:
            property_errors.append("combined coverage does not enumerate the two required roots")
        result = aggregate([item["result"] for item in obligations])
        if property_errors:
            result = "NOT_CERTIFIED"
        if prop.get("result") != result:
            property_errors.append(f"recorded result {prop.get('result')} disagrees with verified result {result}")
        errors.extend(f"property {name}: {error}" for error in property_errors)
        properties.append({"property_id": name, "annotation": requirement.get("annotation"),
                           "result": result, "obligations": obligations, "errors": property_errors})
    result = aggregate([prop["result"] for prop in properties])
    if analysis.get("blocking_diagnostics"):
        result = "NOT_CERTIFIED"
    if analysis.get("result") != result:
        errors.append(f"analysis result {analysis.get('result')} disagrees with verified result {result}")
    return {"result": result, "properties": properties, "errors": errors}
