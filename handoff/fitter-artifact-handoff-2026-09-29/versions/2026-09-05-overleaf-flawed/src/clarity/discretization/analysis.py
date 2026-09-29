"""Coordinate checker progression and assemble discretization analysis records."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from clarity.certification.equations import Const, Expr, Op
from clarity.certification.relevance import equation_refs
from clarity.certification.strict_extract import CertificationExtractor

from .checkers.convex import run_convex_checker
from .checkers.factored import run_lazy_factored_checker
from .checkers.linear import run_linear_checker
from .checkers.reachability import (
    SharedReachabilityCache,
    run_reachability_checker,
    run_relational_invariant_group_checker,
    run_smt_reachability_checker,
    shared_reachability_context_sha256,
)
from .model.expressions import expression_hash
from .model.optimization import quadratic_constraints
from .model.proof_rules import (
    ProofDeferred,
    expr_to_dict,
    expression_is_linear,
    prove_implication_exact,
    substitute,
)
from .model.reduction import build_reduction
from .model.reduction_types import ReducedCase
from .obligations import (
    boolean_variables as extract_boolean_variables,
    continuous_targets as extract_continuous_targets,
    integer_variables as extract_integer_variables,
    policy_call_guards as extract_policy_call_guards,
    scenario_constraints as extract_scenario_constraints,
    shield_expression as extract_shield_expression,
    specified_constant_values as extract_specified_constant_values,
    timing_record as extract_timing_record,
)
from .progression import attempt_stage, stage_record, validated_attempt
from .smt_fallback import run_smt_fallback
from .timing import canonical_dt




CHECKER_ORDER = [
    "linear",
    "convex",
    "reachability_linear",
    "reachability_convex",
    "exact_symbolic",
    "smt_fallback",
    "relational_invariant",
    "smt_reachability",
]


def _case_conjuncts(expression: Expr) -> list[Expr]:
    if isinstance(expression, Op) and expression.op == "and":
        result: list[Expr] = []
        for argument in expression.args:
            result.extend(_case_conjuncts(argument))
        return result
    return [expression]


def _case_conjunction(expressions: list[Expr]) -> Expr:
    if not expressions:
        return Const(True)
    if len(expressions) == 1:
        return expressions[0]
    return Op("and", tuple(expressions))


def _expression_is_convex(expression: Expr) -> bool:
    try:
        quadratic_constraints(expression, set())
        return True
    except ProofDeferred:
        return False


def _grouped_case_attempts(
    reduced_cases: list[ReducedCase],
    checker_name: str,
    checker: Callable[[ReducedCase], dict[str, Any]],
    conjunct_filter: Callable[[Expr], bool] | None = None,
) -> dict[str, dict[str, Any]]:
    conjunct_maps = {
        item.case_id: {
            expression_hash(expression): expression
            for expression in _case_conjuncts(item.expression)
            if conjunct_filter is None or conjunct_filter(expression)
        }
        for item in reduced_cases
    }
    results: dict[str, dict[str, Any]] = {}
    attempt_cache: dict[str, dict[str, Any]] = {}

    def attempt_for(expression: Expr, obligation: str) -> dict[str, Any]:
        expression_sha256 = expression_hash(expression)
        attempt = attempt_cache.get(expression_sha256)
        if attempt is None:
            group_case = ReducedCase(
                f"group.{checker_name}.{expression_sha256[:12]}",
                expression,
                expression_sha256,
                (),
                "common_constraint_group",
                obligation,
                expression,
            )
            attempt = validated_attempt(checker(group_case))
            attempt_cache[expression_sha256] = attempt
        return attempt

    def minimize_certified_subset(
        expression: Expr,
        obligation: str,
        attempt: dict[str, Any],
    ) -> tuple[Expr, dict[str, Any], int]:
        selected = sorted(
            _case_conjuncts(expression),
            key=expression_hash,
        )
        checks = 0
        if checker_name == "linear":
            certificate = (attempt.get("proof") or {}).get("certificate") or {}
            multipliers = certificate.get("multipliers")
            if isinstance(multipliers, list) and len(multipliers) == len(selected):
                try:
                    supported = [
                        item
                        for item, multiplier in zip(selected, multipliers)
                        if Fraction(str(multiplier)) != 0
                    ]
                except (TypeError, ValueError, ZeroDivisionError):
                    supported = []
                if 0 < len(supported) < len(selected):
                    supported_expression = _case_conjunction(supported)
                    supported_attempt = attempt_for(
                        supported_expression,
                        obligation,
                    )
                    checks += 1
                    if supported_attempt.get("outcome") == "CERTIFIED":
                        return supported_expression, supported_attempt, checks
        for candidate in tuple(selected):
            remaining = [item for item in selected if item is not candidate]
            if not remaining:
                continue
            candidate_expression = _case_conjunction(remaining)
            candidate_attempt = attempt_for(candidate_expression, obligation)
            checks += 1
            if candidate_attempt.get("outcome") == "CERTIFIED":
                selected = remaining
                attempt = candidate_attempt
        return _case_conjunction(selected), attempt, checks

    def partition(group: list[ReducedCase]) -> tuple[list[ReducedCase], list[ReducedCase]]:
        sets = [set(conjunct_maps[item.case_id]) for item in group]
        union = set().union(*sets)
        shared = set.intersection(*sets)
        choices = []
        for key in sorted(union - shared):
            present = sum(key in item for item in sets)
            if 0 < present < len(group):
                choices.append((abs(2 * present - len(group)), key))
        if choices:
            _distance, pivot = min(choices)
            left = [item for item in group if pivot in conjunct_maps[item.case_id]]
            right = [item for item in group if pivot not in conjunct_maps[item.case_id]]
            return left, right
        middle = len(group) // 2
        return group[:middle], group[middle:]

    def check(group: list[ReducedCase]) -> None:
        group = [item for item in group if item.case_id not in results]
        if not group:
            return
        if (
            len(group) == 1
            and conjunct_filter is not None
            and not conjunct_maps[group[0].case_id]
        ):
            results[group[0].case_id] = {
                "outcome": "DEFERRED",
                "reason_code": "INAPPLICABLE_REDUCED_FORM",
                "detail": (
                    f"the reduced equation form has no constraints accepted by "
                    f"the {checker_name} checker"
                ),
                "applicability_checks": {
                    "accepted": False,
                    "case_id": group[0].case_id,
                    "reduced_form_checked": True,
                },
            }
            return
        common = set(conjunct_maps[group[0].case_id])
        for item in group[1:]:
            common.intersection_update(conjunct_maps[item.case_id])
        common_expression = _case_conjunction([
            conjunct_maps[group[0].case_id][key] for key in sorted(common)
        ])
        if len(group) == 1 and conjunct_filter is None:
            common_expression = group[0].expression
        attempt = attempt_for(common_expression, group[0].obligation)
        if attempt.get("outcome") == "CERTIFIED":
            selected_expression, attempt, minimization_checks = (
                minimize_certified_subset(
                    common_expression,
                    group[0].obligation,
                    attempt,
                )
            )
            selected_hash = expression_hash(selected_expression)
            selected_conjuncts = {
                expression_hash(item)
                for item in _case_conjuncts(selected_expression)
            }
            covered_cases = [
                item
                for item in reduced_cases
                if item.case_id not in results
                and selected_conjuncts <= set(conjunct_maps[item.case_id])
            ]
            for item in covered_cases:
                covered = deepcopy(attempt)
                covered.setdefault("applicability_checks", {}).update({
                    "case_id": item.case_id,
                    "shared_case_group": len(covered_cases) > 1,
                    "shared_case_count": len(covered_cases),
                })
                if (
                    selected_hash != expression_hash(item.expression)
                    or len(covered_cases) > 1
                ):
                    covered.setdefault("proof", {})["group_reduction"] = {
                        "rule": "certified_conjunctive_subset_v2",
                        "source_expression_sha256": expression_hash(item.expression),
                        "shared_expression": expr_to_dict(selected_expression),
                        "shared_expression_sha256": selected_hash,
                        "shared_conjunct_sha256": sorted(selected_conjuncts),
                        "covered_case_count": len(covered_cases),
                        "starting_group_case_count": len(group),
                        "subset_minimization_checks": minimization_checks,
                    }
                results[item.case_id] = covered
            return
        if len(group) == 1:
            results[group[0].case_id] = deepcopy(attempt)
            return
        left, right = partition(group)
        check(left)
        check(right)

    check(list(reduced_cases))
    return results


def analyze_model(
    model_path: str | Path,
    mdp_certificate: dict[str, Any],
    *,
    dt_text: str,
    optimization_timeout_ms: int = 250,
    smt_timeout_ms: int = 30000,
) -> dict[str, Any]:
    path = str(Path(model_path).resolve())
    dt_record = canonical_dt(dt_text)
    extractor = CertificationExtractor(path)
    model = extractor.extract()
    boolean_variables = extract_boolean_variables(extractor, model, mdp_certificate)
    integer_variables = extract_integer_variables(extractor, model)
    timing = extract_timing_record(mdp_certificate, dt_record)
    annotated, _dt_updated, continuous_records = extract_continuous_targets(
        extractor,
        model,
    )
    constant_values = extract_specified_constant_values(
        extractor,
        model,
        dt_record,
    )

    blockers = [
        diagnostic.pretty()
        for diagnostic in model.diagnostics
        if diagnostic.severity in {"warning", "error"}
    ]
    if blockers:
        return {
            "schema_version": 3,
            "result": "NOT_CERTIFIED",
            "claim": "full_sysml_discretization_safety_preservation_v3",
            "timing": timing,
            "continuous_rate_assignments": continuous_records,
            "properties": [],
            "blocking_diagnostics": blockers,
        }

    try:
        shield_expression, _shield_record = extract_shield_expression(
            extractor, model, mdp_certificate
        )
        guards = extract_policy_call_guards(extractor, model)
        scenario_constraints, scenario_initial_constraints = extract_scenario_constraints(
            extractor, model
        )
        shield_expression = substitute(shield_expression, constant_values)
        guards = [substitute(item, constant_values) for item in guards]
        scenario_constraints = [
            substitute(item, constant_values) for item in scenario_constraints
        ]
        scenario_initial_constraints = [
            substitute(item, constant_values)
            for item in scenario_initial_constraints
        ]
    except ProofDeferred as exc:
        return {
            "schema_version": 3,
            "result": "NOT_CERTIFIED",
            "claim": "full_sysml_discretization_safety_preservation_v3",
            "timing": timing,
            "continuous_rate_assignments": continuous_records,
            "properties": [],
            "blocking_diagnostics": [f"{exc.reason_code}: {exc.detail}"],
        }

    properties: list[dict[str, Any]] = []
    shared_reachability_cache = SharedReachabilityCache()
    for target, equation in sorted(model.requirements.items()):
        if equation.source not in {"Prohibition", "Obligation"}:
            continue
        property_id = target.removeprefix("status.")
        dependencies = equation_refs(model, equation)
        try:
            reduced_cases, reduction, reachability_context = build_reduction(
                extractor,
                model,
                equation,
                shield_expression,
                guards,
                scenario_constraints,
                scenario_initial_constraints,
                constant_values,
                annotated,
                boolean_variables,
                integer_variables,
                dt_record,
            )
        except ProofDeferred as exc:
            properties.append({
                "property_id": property_id,
                "annotation": equation.source,
                "source": equation.pretty(),
                "dependencies": sorted(dependencies),
                "reduction": {
                    "kind": "full_sysml_interval_reduction_v3",
                    "outcome": "DEFERRED",
                    "reason_code": exc.reason_code,
                    "detail": exc.detail,
                },
                "cases": [],
                "progression": [
                    stage_record(
                        checker,
                        "DEFERRED",
                        reason_code=exc.reason_code,
                        detail=exc.detail,
                    )
                    for checker in CHECKER_ORDER
                ],
                "result": "NOT_CERTIFIED",
            })
            continue

        if any(item.factored for item in reduced_cases):
            context_sha256 = shared_reachability_context_sha256(
                reachability_context
            )
            linear_group_attempts = {
                item.case_id: run_lazy_factored_checker(
                    item,
                    boolean_variables,
                    "linear",
                    lambda leaf, remaining: run_linear_checker(
                        leaf,
                        set(),
                        timeout_ms=min(optimization_timeout_ms, remaining),
                    ),
                    lambda expression: expression_is_linear(
                        expression,
                        set(),
                    )[0],
                    timeout_ms=optimization_timeout_ms,
                    context_sha256=context_sha256,
                )
                for item in reduced_cases
            }
            convex_group_attempts = {
                item.case_id: run_lazy_factored_checker(
                    item,
                    boolean_variables,
                    "convex",
                    lambda leaf, remaining: run_convex_checker(
                        leaf,
                        set(),
                        timeout_ms=min(optimization_timeout_ms, remaining),
                    ),
                    _expression_is_convex,
                    timeout_ms=optimization_timeout_ms,
                    context_sha256=context_sha256,
                )
                for item in reduced_cases
                if linear_group_attempts[item.case_id].get("outcome")
                != "CERTIFIED"
            }
        else:
            linear_group_attempts = _grouped_case_attempts(
                reduced_cases,
                "linear",
                lambda item: run_linear_checker(
                    item,
                    set(),
                    timeout_ms=optimization_timeout_ms,
                ),
                conjunct_filter=lambda expression: expression_is_linear(
                    expression,
                    set(),
                )[0],
            )
            convex_group_attempts = _grouped_case_attempts(
                [
                    item for item in reduced_cases
                    if linear_group_attempts[item.case_id].get("outcome")
                    != "CERTIFIED"
                ],
                "convex",
                lambda item: run_convex_checker(
                    item,
                    set(),
                    timeout_ms=optimization_timeout_ms,
                ),
                conjunct_filter=_expression_is_convex,
            )
        case_records: list[dict[str, Any]] = []
        pending_relational: list[tuple[ReducedCase, dict[str, Any]]] = []
        for reduced_case in reduced_cases:
            case_progression: list[dict[str, Any]] = []
            linear_attempt = deepcopy(linear_group_attempts[reduced_case.case_id])
            if linear_attempt.get("outcome") == "VIOLATION":
                linear_attempt = {
                    **linear_attempt,
                    "outcome": "DEFERRED",
                    "reason_code": "COUNTEREXAMPLE_REPLAY_FAILED",
                    "detail": (
                        "the arithmetic candidate satisfies the local proof obligation, "
                        "but reachability from the declared initial state was not replayed"
                    ),
                }
            case_progression.append(attempt_stage("linear", linear_attempt))
            outcome = str(linear_attempt.get("outcome", "DEFERRED"))

            if outcome == "DEFERRED":
                convex_attempt = deepcopy(
                    convex_group_attempts[reduced_case.case_id]
                )
                if convex_attempt.get("outcome") == "VIOLATION":
                    convex_attempt = {
                        **convex_attempt,
                        "outcome": "DEFERRED",
                        "reason_code": "COUNTEREXAMPLE_REPLAY_FAILED",
                        "detail": (
                            "the arithmetic candidate satisfies the local proof obligation, "
                            "but reachability from the declared initial state was not replayed"
                        ),
                    }
                case_progression.append(attempt_stage("convex", convex_attempt))
                outcome = str(convex_attempt.get("outcome", "DEFERRED"))

            if outcome == "DEFERRED":
                reachability_linear = validated_attempt(
                    run_reachability_checker(
                        reduced_case,
                        reachability_context,
                        method="linear",
                        timeout_ms=optimization_timeout_ms,
                    )
                )
                case_progression.append(attempt_stage(
                    "reachability_linear",
                    reachability_linear,
                ))
                outcome = str(
                    reachability_linear.get("outcome", "DEFERRED")
                )

            if outcome == "DEFERRED":
                reachability_convex = validated_attempt(
                    run_reachability_checker(
                        reduced_case,
                        reachability_context,
                        method="convex",
                        timeout_ms=optimization_timeout_ms,
                    )
                )
                case_progression.append(attempt_stage(
                    "reachability_convex",
                    reachability_convex,
                ))
                outcome = str(
                    reachability_convex.get("outcome", "DEFERRED")
                )

            if outcome == "DEFERRED":
                if reduced_case.factored:
                    exact_proof = {
                        "proved": False,
                        "reason_code": "FACTORED_FORMULA_PRESERVED",
                        "detail": (
                            "the factored formula is passed intact to the next "
                            "checker without exhaustive symbolic case expansion"
                        ),
                    }
                else:
                    try:
                        exact_proof = prove_implication_exact(
                            [reduced_case.expression],
                            Const(False),
                            set(),
                        )
                    except ProofDeferred as exc:
                        exact_proof = {
                            "proved": False,
                            "reason_code": exc.reason_code,
                            "detail": exc.detail,
                        }
                    except Exception as exc:  # pragma: no cover
                        exact_proof = {
                            "proved": False,
                            "reason_code": "MALFORMED_OUTPUT",
                            "detail": str(exc),
                        }
                if exact_proof.get("proved"):
                    case_progression.append(stage_record(
                        "exact_symbolic",
                        "CERTIFIED",
                        applicability_checks={
                            "reduced_case_required": True,
                            "case_id": reduced_case.case_id,
                        },
                        proof={
                            "rule": "reduced_case_exact_infeasibility_v2",
                            "within_interval_proof": exact_proof,
                        },
                    ))
                    outcome = "CERTIFIED"
                else:
                    case_progression.append(stage_record(
                        "exact_symbolic",
                        "DEFERRED",
                        reason_code=str(
                            exact_proof.get("reason_code")
                            or "UNSUPPORTED_EXPRESSION"
                        ),
                        detail=str(
                            exact_proof.get("detail")
                            or "the reduced case remains feasible"
                        ),
                        applicability_checks={
                            "reduced_case_required": True,
                            "case_id": reduced_case.case_id,
                        },
                        proof=exact_proof,
                    ))
                    outcome = "DEFERRED"

            if outcome == "DEFERRED":
                smt_stage = run_smt_fallback(
                    model,
                    reduced_case,
                    timeout_ms=smt_timeout_ms,
                    recertification_timeout_ms=optimization_timeout_ms,
                )
                smt_stage.setdefault("applicability_checks", {}).update({
                    "reduced_case_required": True,
                    "case_id": reduced_case.case_id,
                })
                case_progression.append(smt_stage)
                outcome = str(smt_stage.get("outcome", "DEFERRED"))

            case_record = {
                "case_id": reduced_case.case_id,
                "expression": expr_to_dict(reduced_case.expression),
                "reachability_expression": expr_to_dict(
                    reduced_case.reachability_expression
                    or reduced_case.expression
                ),
                "parent_expression_sha256": reduced_case.parent_hash,
                "boolean_assignment": dict(reduced_case.boolean_assignment),
                "time_reduction": reduced_case.time_reduction,
                "obligation": reduced_case.obligation,
                "progression": case_progression,
                "result": (
                    outcome
                    if outcome in {"CERTIFIED", "VIOLATION"}
                    else "NOT_CERTIFIED"
                ),
            }
            case_records.append(case_record)
            if outcome == "DEFERRED":
                pending_relational.append((reduced_case, case_record))

        if pending_relational:
            relational_attempts = run_relational_invariant_group_checker(
                model,
                [item for item, _record in pending_relational],
                reachability_context,
                timeout_ms=smt_timeout_ms,
                cache=shared_reachability_cache,
            )
            for reduced_case, case_record in pending_relational:
                relational_invariant = validated_attempt(
                    relational_attempts.get(reduced_case.case_id, {
                        "outcome": "DEFERRED",
                        "reason_code": "MALFORMED_OUTPUT",
                        "detail": "grouped relational checker omitted the case",
                    })
                )
                case_record["progression"].append(attempt_stage(
                    "relational_invariant",
                    relational_invariant,
                ))
                outcome = str(
                    relational_invariant.get("outcome", "DEFERRED")
                )
                if outcome == "DEFERRED":
                    smt_reachability = validated_attempt(
                        run_smt_reachability_checker(
                            model,
                            reduced_case,
                            reachability_context,
                            timeout_ms=smt_timeout_ms,
                        )
                    )
                    case_record["progression"].append(attempt_stage(
                        "smt_reachability",
                        smt_reachability,
                    ))
                    outcome = str(
                        smt_reachability.get("outcome", "DEFERRED")
                    )
                case_record["result"] = (
                    outcome
                    if outcome in {"CERTIFIED", "VIOLATION"}
                    else "NOT_CERTIFIED"
                )

        if any(item["result"] == "VIOLATION" for item in case_records):
            property_result = "VIOLATION"
        elif all(item["result"] == "CERTIFIED" for item in case_records):
            property_result = "CERTIFIED"
        else:
            property_result = "NOT_CERTIFIED"
        property_progression: list[dict[str, Any]] = []
        for checker in CHECKER_ORDER:
            attempts = [
                stage
                for case in case_records
                for stage in case["progression"]
                if stage["checker"] == checker
            ]
            if not attempts:
                continue
            property_progression.append(stage_record(
                checker,
                (
                    "VIOLATION"
                    if any(stage["outcome"] == "VIOLATION" for stage in attempts)
                    else (
                        "CERTIFIED"
                        if all(stage["outcome"] == "CERTIFIED" for stage in attempts)
                        else "DEFERRED"
                    )
                ),
                reason_code=(
                    "COUNTEREXAMPLE_REPLAYED"
                    if any(stage["outcome"] == "VIOLATION" for stage in attempts)
                    else (
                        ""
                        if all(stage["outcome"] == "CERTIFIED" for stage in attempts)
                        else "UNRESOLVED_CASES"
                    )
                ),
                detail=f"{sum(stage['outcome'] == 'CERTIFIED' for stage in attempts)}/{len(attempts)} attempted cases certified",
                applicability_checks={"case_attempt_count": len(attempts)},
            ))

        properties.append({
            "property_id": property_id,
            "annotation": equation.source,
            "source": equation.pretty(),
            "dependencies": sorted(dependencies),
            "reduction": reduction,
            "cases": case_records,
            "progression": property_progression,
            "result": property_result,
        })

    if any(item["result"] == "VIOLATION" for item in properties):
        result = "VIOLATION"
    elif properties and all(item["result"] == "CERTIFIED" for item in properties):
        result = "CERTIFIED"
    else:
        result = "NOT_CERTIFIED"
    return {
        "schema_version": 3,
        "result": result,
        "claim": "full_sysml_discretization_safety_preservation_v3",
        "claim_scope": (
            "For the complete SysML physical process from each shielded controller "
            "update through every time in the following fixed dt interval, for every "
            "parsed #Prohibition and #Obligation."
        ),
        "timing": timing,
        "continuous_rate_semantics": (
            "#ContinuousRate marks x := x + rate * dt as a rate held over the "
            "following physical interval, with the selected controller action held "
            "through the actuator and physical equations."
        ),
        "continuous_rate_assignments": continuous_records,
        "specified_constant_values": {
            name: expr_to_dict(value) for name, value in sorted(constant_values.items())
        },
        "checker_order": CHECKER_ORDER,
        "markov_process_evidence": {
            "buffer": mdp_certificate.get("buffer", {}),
            "reconstructed_state": mdp_certificate.get("sets", {}).get("q", []),
            "executed_actions": mdp_certificate.get("sets", {}).get("actions", []),
            "time_variables": mdp_certificate.get("sets", {}).get("time_vars", []),
        },
        "optimization_timeout_ms": int(optimization_timeout_ms),
        "shared_reachability": shared_reachability_cache.export(),
        "properties": properties,
        "blocking_diagnostics": [],
    }
