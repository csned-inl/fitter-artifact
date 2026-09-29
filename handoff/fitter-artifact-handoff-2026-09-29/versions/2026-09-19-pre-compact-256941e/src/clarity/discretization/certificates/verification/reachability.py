"""Verify SMT, relational, and shared reachability proof records."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..replay import replay_serialized_boolean_expression
from .convex import verify_recorded_convex_certificate, verify_recorded_outer_reduction
from .expressions import (
    _serialized_conjunct_hashes,
    _serialized_conjunction,
    _serialized_conjuncts,
    _serialized_disjunct_hashes,
    _serialized_expression_hash,
)
from .linear import verify_recorded_linear_certificate

try:  # pragma: no cover - integration environment determines availability
    import z3  # type: ignore
except Exception:  # pragma: no cover
    z3 = None


def _verify_smt_stage(
    stage: dict[str, Any],
    case: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    proof = stage.get("proof") or {}
    rule = proof.get("rule")
    source_expression = case.get("expression")
    if not isinstance(source_expression, dict):
        return ["source case expression is malformed"]
    source_hash = _serialized_expression_hash(source_expression)
    if proof.get("source_expression_sha256") != source_hash:
        errors.append("source expression hash is invalid")

    if rule == "exact_local_feasibility_replay_v1":
        if stage.get("outcome") != "DEFERRED":
            errors.append("local feasibility replay must remain deferred")
        if proof.get("solver_status") != "sat":
            errors.append("local feasibility replay solver status is invalid")
        try:
            replayed = replay_serialized_boolean_expression(
                source_expression,
                proof.get("exact_values"),
            )
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            errors.append(f"local feasibility replay is malformed: {exc}")
        else:
            if replayed is not True or proof.get("exact_replay") is not True:
                errors.append("local feasibility values do not satisfy the source case")
        return errors

    if rule != "solver_selected_subset_recertification_v1":
        if stage.get("outcome") == "CERTIFIED":
            errors.append("certified solver fallback proof rule is invalid")
        return errors
    if proof.get("solver_status") != "unsat":
        errors.append("selected subset solver status is invalid")

    source_constraints = _serialized_conjuncts(source_expression)
    if proof.get("source_constraint_count") != len(source_constraints):
        errors.append("source constraint count is invalid")
    indices = proof.get("selected_indices")
    if (
        not isinstance(indices, list)
        or any(not isinstance(index, int) for index in indices)
        or indices != sorted(set(indices))
        or any(index < 0 or index >= len(source_constraints) for index in indices)
    ):
        errors.append("selected constraint indices are malformed")
        return errors
    selected = [source_constraints[index] for index in indices]
    if proof.get("selected_constraints") != selected:
        errors.append("selected constraints are not the recorded source subset")
    selected_expression = _serialized_conjunction(selected)
    if proof.get("selected_expression") != selected_expression:
        errors.append("selected constraint expression is invalid")
    selected_hash = _serialized_expression_hash(selected_expression)
    if proof.get("selected_expression_sha256") != selected_hash:
        errors.append("selected constraint expression hash is invalid")

    if stage.get("outcome") != "CERTIFIED":
        return errors
    attempt = proof.get("certificate_attempt")
    if not isinstance(attempt, dict) or attempt.get("outcome") != "CERTIFIED":
        errors.append("selected constraint certificate attempt is missing")
        return errors
    if proof.get("certifying_checker") != attempt.get("checker"):
        errors.append("selected constraint certifying checker is inconsistent")
    certificate = (attempt.get("proof") or {}).get("certificate")
    if not isinstance(certificate, dict):
        errors.append("selected constraint certificate is missing")
        return errors
    kind = certificate.get("kind")
    if kind == "linear_infeasibility_weights_v1":
        errors.extend(
            "selected constraint linear certificate: " + error
            for error in verify_recorded_linear_certificate(certificate)
        )
    elif kind == "convex_dual_bound_v1":
        errors.extend(
            "selected constraint convex certificate: " + error
            for error in verify_recorded_convex_certificate(certificate)
        )
    else:
        errors.append("selected constraint certificate kind is invalid")
    errors.extend(
        "selected constraint outer reduction: " + error
        for error in verify_recorded_outer_reduction(
            attempt.get("proof") or {},
            selected_hash,
        )
    )
    return errors


def _verify_smt_reachability_query(query: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(query, dict):
        return ["SMT reachability query is malformed"]
    expression = query.get("query_expression")
    if not isinstance(expression, dict):
        errors.append("SMT reachability query expression is malformed")
    elif query.get("query_expression_sha256") != _serialized_expression_hash(
        expression
    ):
        errors.append("SMT reachability query expression hash is invalid")
    smt2 = query.get("query_smt2")
    if not isinstance(smt2, str) or not smt2:
        errors.append("SMT reachability query text is missing")
    elif query.get("query_smt2_sha256") != hashlib.sha256(
        smt2.encode("utf-8")
    ).hexdigest():
        errors.append("SMT reachability query text hash is invalid")
    status = query.get("solver_status")
    if status == "unsat":
        proof_text = query.get("z3_proof")
        if not isinstance(proof_text, str) or not proof_text:
            errors.append("SMT reachability no solution proof is missing")
        elif query.get("z3_proof_sha256") != hashlib.sha256(
            proof_text.encode("utf-8")
        ).hexdigest():
            errors.append("SMT reachability no solution proof hash is invalid")
    elif status == "sat":
        try:
            replayed = replay_serialized_boolean_expression(
                expression,
                query.get("exact_values"),
            )
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            errors.append(f"SMT reachability trace is malformed: {exc}")
        else:
            if replayed is not True or query.get("exact_replay") is not True:
                errors.append("SMT reachability trace does not satisfy its query")
    elif status not in {"unknown", "unsupported"}:
        errors.append("SMT reachability solver status is invalid")
    return errors


def _recheck_smt_no_solution(query: dict[str, Any]) -> list[str]:
    if z3 is None:
        return ["z3-solver is unavailable for SMT reachability proof replay"]
    smt2 = query.get("query_smt2")
    if not isinstance(smt2, str) or not smt2:
        return ["SMT reachability query text is missing"]
    try:
        # A hash only authenticates the supplied text, not the obligation.
        # Reconstruct the predicate and require the supplied query to mean it.
        from clarity.certification.equations import EquationModel, Equation
        from clarity.certification.solver import Encoder, infer_sorts
        from .expressions import _factored_expr_from_dict
        expression = _factored_expr_from_dict(query['query_expression'])
        model = EquationModel('<replayed obligation>', expression.refs(), set())
        model.requirements['query'] = Equation('query', expression, 'requirement')
        sorts, conflicts = infer_sorts(model)
        if conflicts:
            return [f'SMT source query type conflicts: {conflicts}']
        expected = Encoder(model, sorts).encode_expr(expression, 1, 'current')
        assertions = z3.parse_smt2_string(smt2)
        solver = z3.Solver()
        solver.set(timeout=30000)
        solver.add(z3.Xor(z3.And(*assertions), expected))
        if solver.check() != z3.unsat:
            return ['SMT text does not encode the recorded source obligation']
        solver = z3.Solver()
        solver.set(timeout=30000)
        solver.add(expected)
        result = solver.check()
    except Exception as exc:  # pragma: no cover - fail-closed boundary
        return [f"SMT reachability proof replay failed: {exc}"]
    if result != z3.unsat:
        return [f"SMT reachability no solution result did not replay: {result}"]
    return []


def _verify_smt_reachability_stage(
    stage: dict[str, Any],
    case: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    proof = stage.get("proof") or {}
    if stage.get("outcome") == "DEFERRED" and not proof:
        return []
    rule = proof.get("rule")
    source_expression = case.get("expression")
    reachability_expression = case.get("reachability_expression")
    if not isinstance(source_expression, dict):
        return ["SMT reachability source expression is malformed"]
    if not isinstance(reachability_expression, dict):
        return ["SMT reachability expression is malformed"]
    if proof.get("case_expression_sha256") != _serialized_expression_hash(
        source_expression
    ):
        errors.append("SMT reachability source expression hash is invalid")
    if proof.get("reachability_expression_sha256") != _serialized_expression_hash(
        reachability_expression
    ):
        errors.append("SMT reachability expression hash is invalid")

    if rule == "smt_finite_prefix_counterexample_v1":
        if stage.get("outcome") != "VIOLATION":
            errors.append("SMT reachability trace must report a violation")
        trace_query = proof.get("trace_query")
        errors.extend(_verify_smt_reachability_query(trace_query))
        if isinstance(trace_query, dict) and trace_query.get("solver_status") != "sat":
            errors.append("SMT reachability trace is not a solution")
        return errors

    if rule != "smt_finite_prefix_and_inductive_exclusion_v1":
        if stage.get("outcome") in {"CERTIFIED", "VIOLATION"}:
            errors.append("SMT reachability proof rule is invalid")
        return errors
    attempts = proof.get("depth_attempts")
    if not isinstance(attempts, list):
        return errors + ["SMT reachability depth attempts are malformed"]
    for attempt in attempts:
        if not isinstance(attempt, dict):
            errors.append("SMT reachability depth attempt is malformed")
            continue
        for query in attempt.get("base_queries", []):
            errors.extend(_verify_smt_reachability_query(query))
        induction = attempt.get("induction_query")
        if induction is not None:
            errors.extend(_verify_smt_reachability_query(induction))
    if stage.get("outcome") == "CERTIFIED":
        certified = [
            attempt for attempt in attempts if attempt.get("proved") is True
        ]
        if not certified:
            errors.append("SMT reachability proof has no certified depth")
        for attempt in certified:
            bases = attempt.get("base_queries")
            induction = attempt.get("induction_query")
            if not isinstance(bases, list) or not bases:
                errors.append("SMT reachability certified depth has no base query")
            elif any(query.get("solver_status") != "unsat" for query in bases):
                errors.append("SMT reachability base query is not proved impossible")
            else:
                for query in bases:
                    errors.extend(_recheck_smt_no_solution(query))
            if not isinstance(induction, dict) or induction.get(
                "solver_status"
            ) != "unsat":
                errors.append("SMT reachability induction query is not proved impossible")
            else:
                errors.extend(_recheck_smt_no_solution(induction))
    return errors


def _verify_relational_invariant_stage(
    stage: dict[str, Any],
    case: dict[str, Any],
    shared_regions: dict[str, Any] | None = None,
    shared_safety_queries: dict[str, Any] | None = None,
) -> list[str]:
    proof = stage.get("proof") or {}
    if stage.get("outcome") != "CERTIFIED":
        return []
    errors: list[str] = []
    if proof.get("rule") == "shared_relational_inductive_invariant_v2":
        source_expression = case.get("expression")
        reachability_expression = case.get("reachability_expression")
        if not isinstance(source_expression, dict) or proof.get(
            "case_expression_sha256"
        ) != _serialized_expression_hash(source_expression):
            errors.append("shared relational source expression hash is invalid")
        if not isinstance(reachability_expression, dict) or proof.get(
            "reachability_expression_sha256"
        ) != _serialized_expression_hash(reachability_expression):
            errors.append("shared relational reachability expression hash is invalid")
        region_key = proof.get("shared_reachable_region_sha256")
        safety_key = proof.get("merged_safety_query_sha256")
        region = (shared_regions or {}).get(region_key)
        safety = (shared_safety_queries or {}).get(safety_key)
        if not isinstance(region, dict):
            errors.append("shared reachable region reference is missing")
        if not isinstance(safety, dict):
            errors.append("merged relational safety query reference is missing")
        else:
            if safety.get("context_sha256") != region_key:
                errors.append("merged relational safety query context is inconsistent")
            if case.get("case_id") not in safety.get("case_ids", []):
                errors.append("merged relational safety query omits its case")
            coverage = [
                item
                for item in safety.get("covered_case_expressions", [])
                if isinstance(item, dict)
                and item.get("case_id") == case.get("case_id")
                and item.get("expression_sha256")
                == proof.get("case_safety_expression_sha256")
                and item.get("merge_rule") == proof.get("merge_rule")
            ]
            if not coverage:
                errors.append("merged relational safety query has no case coverage")
            query = safety.get("query")
            if not isinstance(query, dict) or query.get("solver_status") != "unsat":
                errors.append("merged relational safety obligation is not proved impossible")
        return errors
    if proof.get("rule") != "relational_inductive_invariant_v1":
        return ["relational invariant proof rule is invalid"]
    source_expression = case.get("expression")
    reachability_expression = case.get("reachability_expression")
    if not isinstance(source_expression, dict):
        errors.append("relational invariant source expression is malformed")
    elif proof.get("case_expression_sha256") != _serialized_expression_hash(
        source_expression
    ):
        errors.append("relational invariant source expression hash is invalid")
    if not isinstance(reachability_expression, dict):
        errors.append("relational invariant reachability expression is malformed")
    elif proof.get(
        "reachability_expression_sha256"
    ) != _serialized_expression_hash(reachability_expression):
        errors.append("relational invariant reachability expression hash is invalid")

    transition = proof.get("transition_expression")
    if not isinstance(transition, dict):
        errors.append("relational transition expression is malformed")
    elif proof.get("transition_expression_sha256") != _serialized_expression_hash(
        transition
    ):
        errors.append("relational transition expression hash is invalid")

    invariants = proof.get("invariant")
    invariant_hashes = proof.get("invariant_sha256")
    if not isinstance(invariants, list) or not invariants:
        errors.append("relational invariant is missing")
        invariants = []
    if not isinstance(invariant_hashes, list) or len(invariant_hashes) != len(
        invariants
    ):
        errors.append("relational invariant hashes are malformed")
        invariant_hashes = []
    for index, expression in enumerate(invariants):
        if not isinstance(expression, dict):
            errors.append("relational invariant clause is malformed")
        elif index < len(invariant_hashes) and invariant_hashes[
            index
        ] != _serialized_expression_hash(expression):
            errors.append("relational invariant clause hash is invalid")

    initiation = proof.get("initiation_queries")
    preservation = proof.get("preservation_queries")
    if not isinstance(initiation, list):
        errors.append("relational invariant initiation queries are malformed")
        initiation = []
    if not isinstance(preservation, list):
        errors.append("relational invariant preservation queries are malformed")
        preservation = []
    initiated_hashes = {
        item.get("candidate_sha256")
        for item in initiation
        if isinstance(item, dict)
    }
    preserved_hashes = {
        item.get("candidate_sha256")
        for item in preservation
        if isinstance(item, dict)
    }
    for invariant_hash in invariant_hashes:
        if invariant_hash not in initiated_hashes:
            errors.append("relational invariant clause has no initiation proof")
        if invariant_hash not in preserved_hashes:
            errors.append("relational invariant clause has no preservation proof")
    for record in [*initiation, *preservation]:
        if not isinstance(record, dict):
            errors.append("relational invariant query record is malformed")
            continue
        candidate = record.get("candidate")
        if not isinstance(candidate, dict):
            errors.append("relational invariant query candidate is malformed")
        elif record.get("candidate_sha256") != _serialized_expression_hash(
            candidate
        ):
            errors.append("relational invariant query candidate hash is invalid")
        query = record.get("query")
        errors.extend(_verify_smt_reachability_query(query))
        if not isinstance(query, dict) or query.get("solver_status") != "unsat":
            errors.append("relational invariant obligation is not proved impossible")
        else:
            errors.extend(_recheck_smt_no_solution(query))

    safety_query = proof.get("safety_query")
    errors.extend(_verify_smt_reachability_query(safety_query))
    if not isinstance(safety_query, dict) or safety_query.get(
        "solver_status"
    ) != "unsat":
        errors.append("relational invariant safety obligation is not proved impossible")
    else:
        errors.extend(_recheck_smt_no_solution(safety_query))
    return errors


def _verify_shared_candidate_batches(
    records: Any,
    invariant_hashes: list[Any],
    label: str,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(records, list) or not records:
        return [f"shared reachable region {label} batches are malformed"]
    covered: set[Any] = set()
    for record in records:
        if not isinstance(record, dict):
            errors.append(f"shared reachable region {label} batch is malformed")
            continue
        candidates = record.get("candidates")
        candidate_hashes = record.get("candidate_sha256")
        if not isinstance(candidates, list) or not isinstance(candidate_hashes, list):
            errors.append(f"shared reachable region {label} candidates are malformed")
            continue
        if len(candidates) != len(candidate_hashes):
            errors.append(f"shared reachable region {label} candidate hashes are malformed")
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict) or index >= len(
                candidate_hashes
            ) or candidate_hashes[index] != _serialized_expression_hash(candidate):
                errors.append(f"shared reachable region {label} candidate hash is invalid")
            elif index < len(candidate_hashes):
                covered.add(candidate_hashes[index])
        query = record.get("query")
        errors.extend(_verify_smt_reachability_query(query))
        if not isinstance(query, dict) or query.get("solver_status") != "unsat":
            errors.append(f"shared reachable region {label} batch is not proved impossible")
        else:
            errors.extend(_recheck_smt_no_solution(query))
    for invariant_hash in invariant_hashes:
        if invariant_hash not in covered:
            errors.append(f"shared reachable region clause has no {label} proof")
    return errors


def _verify_shared_reachability(
    analysis: dict[str, Any],
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    errors: list[str] = []
    shared = analysis.get("shared_reachability") or {}
    regions = shared.get("regions") or {}
    safety_queries = shared.get("safety_queries") or {}
    if not isinstance(regions, dict):
        return ["shared reachable regions are malformed"], {}, {}
    if not isinstance(safety_queries, dict):
        return ["shared relational safety queries are malformed"], regions, {}

    for key, region in regions.items():
        if not isinstance(region, dict):
            errors.append("shared reachable region is malformed")
            continue
        context = region.get("context")
        if not isinstance(context, dict):
            errors.append("shared reachable region context is malformed")
        else:
            context_hash = hashlib.sha256(json.dumps(
                context,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            if key != context_hash or region.get("context_sha256") != context_hash:
                errors.append("shared reachable region context hash is invalid")
        rule = region.get("rule")
        if rule not in {
            "shared_relational_reachable_region_v1",
            "shared_relational_reachable_region_v2",
        }:
            errors.append("shared reachable region proof rule is invalid")
        for name in ("initial_expression", "domain_expression", "transition_expression"):
            expression = region.get(name)
            if not isinstance(expression, dict) or region.get(
                name + "_sha256"
            ) != _serialized_expression_hash(expression):
                errors.append(f"shared reachable region {name} hash is invalid")

        invariants = region.get("invariant")
        invariant_hashes = region.get("invariant_sha256")
        if not isinstance(invariants, list) or not invariants:
            errors.append("shared reachable region invariant is missing")
            invariants = []
        if not isinstance(invariant_hashes, list) or len(invariant_hashes) != len(
            invariants
        ):
            errors.append("shared reachable region invariant hashes are malformed")
            invariant_hashes = []
        for index, expression in enumerate(invariants):
            if not isinstance(expression, dict) or index >= len(
                invariant_hashes
            ) or invariant_hashes[index] != _serialized_expression_hash(expression):
                errors.append("shared reachable region invariant clause hash is invalid")

        if rule == "shared_relational_reachable_region_v2":
            errors.extend(_verify_shared_candidate_batches(
                region.get("initiation_batches"),
                invariant_hashes,
                "initiation",
            ))
            errors.extend(_verify_shared_candidate_batches(
                region.get("preservation_batches"),
                invariant_hashes,
                "preservation",
            ))
        else:
            initiation = region.get("initiation_queries")
            preservation = region.get("preservation_queries")
            if not isinstance(initiation, list):
                errors.append("shared reachable region initiation queries are malformed")
                initiation = []
            if not isinstance(preservation, list):
                errors.append("shared reachable region preservation queries are malformed")
                preservation = []
            initiated_hashes = {
                item.get("candidate_sha256")
                for item in initiation
                if isinstance(item, dict)
            }
            preserved_hashes = {
                item.get("candidate_sha256")
                for item in preservation
                if isinstance(item, dict)
            }
            for invariant_hash in invariant_hashes:
                if invariant_hash not in initiated_hashes:
                    errors.append("shared reachable region clause has no initiation proof")
                if invariant_hash not in preserved_hashes:
                    errors.append("shared reachable region clause has no preservation proof")
            for record in [*initiation, *preservation]:
                if not isinstance(record, dict):
                    errors.append("shared reachable region query record is malformed")
                    continue
                candidate = record.get("candidate")
                if not isinstance(candidate, dict) or record.get(
                    "candidate_sha256"
                ) != _serialized_expression_hash(candidate):
                    errors.append("shared reachable region candidate hash is invalid")
                query = record.get("query")
                errors.extend(_verify_smt_reachability_query(query))
                if not isinstance(query, dict) or query.get("solver_status") != "unsat":
                    errors.append("shared reachable region obligation is not proved impossible")
                else:
                    errors.extend(_recheck_smt_no_solution(query))

        for removal in region.get("implication_removals", []):
            if not isinstance(removal, dict):
                errors.append("shared reachable region implication removal is malformed")
                continue
            candidate = removal.get("removed_candidate")
            if not isinstance(candidate, dict) or removal.get(
                "removed_candidate_sha256"
            ) != _serialized_expression_hash(candidate):
                errors.append("removed reachable region constraint hash is invalid")
            query = removal.get("query")
            errors.extend(_verify_smt_reachability_query(query))
            if not isinstance(query, dict) or query.get("solver_status") != "unsat":
                errors.append("reachable region constraint removal is not proved")
            else:
                errors.extend(_recheck_smt_no_solution(query))

    for key, safety in safety_queries.items():
        if not isinstance(safety, dict):
            errors.append("merged relational safety query is malformed")
            continue
        expression = safety.get("safety_expression")
        if not isinstance(expression, dict):
            errors.append("merged relational safety expression is malformed")
            continue
        expression_hash = _serialized_expression_hash(expression)
        if safety.get("safety_expression_sha256") != expression_hash:
            errors.append("merged relational safety expression hash is invalid")
        expected_key = hashlib.sha256(
            f"{safety.get('context_sha256')}:{expression_hash}".encode("utf-8")
        ).hexdigest()
        if key != expected_key:
            errors.append("merged relational safety query hash is invalid")
        if safety.get("context_sha256") not in regions:
            errors.append("merged relational safety query has no reachable region")
        covered = safety.get("covered_case_expressions")
        if not isinstance(covered, list) or not covered:
            errors.append("merged relational safety query has no covered cases")
            covered = []
        source_conjuncts = _serialized_conjunct_hashes(expression)
        source_disjuncts = _serialized_disjunct_hashes(expression)
        for coverage in covered:
            if not isinstance(coverage, dict):
                errors.append("merged relational safety case coverage is malformed")
                continue
            covered_expression = coverage.get("expression")
            if not isinstance(covered_expression, dict) or coverage.get(
                "expression_sha256"
            ) != _serialized_expression_hash(covered_expression):
                errors.append("merged relational safety case expression hash is invalid")
                continue
            rule = coverage.get("merge_rule")
            covered_conjuncts = _serialized_conjunct_hashes(covered_expression)
            if rule == "identical_expression":
                if covered_conjuncts != source_conjuncts:
                    errors.append("identical relational safety merge is not identical")
            elif rule == "conjunct_containment":
                if not source_conjuncts <= covered_conjuncts:
                    errors.append("relational safety containment merge is invalid")
            elif rule == "new_query":
                if coverage.get("expression_sha256") != expression_hash:
                    errors.append("new relational safety query coverage is inconsistent")
            elif rule == "group_disjunction":
                if coverage.get("expression_sha256") not in source_disjuncts:
                    errors.append("grouped relational safety coverage is invalid")
            else:
                errors.append("relational safety merge rule is invalid")
        query = safety.get("query")
        errors.extend(_verify_smt_reachability_query(query))
        if isinstance(query, dict) and query.get("query_expression") != expression:
            errors.append("merged relational safety query expression does not match")
        if isinstance(query, dict) and query.get("solver_status") == "unsat":
            errors.extend(_recheck_smt_no_solution(query))
    return errors, regions, safety_queries
