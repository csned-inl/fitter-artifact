"""Shared relational reachable-region construction and grouped safety checks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Callable

from clarity.certification.equations import Const, EquationModel, Expr, Op

from ...certificates.replay import replay_serialized_boolean_expression
from .encoding import (
    _IncrementalSmtQueryRunner,
    _hard_timeout,
    _mode_condition,
    _remaining_timeout_ms,
    _run_smt_query,
    z3,
)
from .invariants import (
    _candidate_invariants,
    _conjunct_hashes,
    _relational_lift,
    _relational_query,
    _relational_transition,
    _shared_context_record,
    shared_reachability_context_sha256,
)
from ...model.proof_rules import ProofDeferred, expr_to_dict
from ...model.expressions import expression_hash, simplify
from ...model.reduction_types import ReachabilityContext, ReducedCase


@dataclass
class _SharedRelationalRegion:
    context_sha256: str
    invariant: tuple[Expr, ...]
    record: dict[str, Any]
    query_runner: _IncrementalSmtQueryRunner


@dataclass
class SharedReachabilityCache:
    """Reuse one checked reachable region and identical safety queries."""

    regions: dict[str, _SharedRelationalRegion] = field(default_factory=dict)
    region_failures: dict[str, dict[str, Any]] = field(default_factory=dict)
    safety_queries: dict[str, dict[str, Any]] = field(default_factory=dict)
    safety_expressions: dict[str, Expr] = field(default_factory=dict, repr=False)

    def export(self) -> dict[str, Any]:
        return {
            "regions": {
                key: value.record for key, value in sorted(self.regions.items())
            },
            "region_failures": {
                key: value for key, value in sorted(self.region_failures.items())
            },
            "safety_queries": {
                key: value for key, value in sorted(self.safety_queries.items())
            },
        }


def _batched_candidate_filter(
    candidates: list[Expr],
    antecedent: Expr,
    context: ReachabilityContext,
    frame: int,
    run_query: Callable[[Expr], dict[str, Any]],
) -> tuple[list[Expr], list[Expr]]:
    proved: list[Expr] = []
    rejected: list[Expr] = []

    def check(group: list[Expr]) -> None:
        remaining = list(group)
        while remaining:
            lifted = [
                _relational_lift(item, context, frame) for item in remaining
            ]
            consequent = _relational_query(lifted)
            query = run_query(_relational_query([
                antecedent,
                Op("not", (consequent,)),
            ]))
            if query.get("solver_status") == "unsat":
                proved.extend(remaining)
                return
            failed: list[Expr] = []
            if query.get("solver_status") == "sat" and isinstance(
                query.get("exact_values"), dict
            ):
                for candidate, lifted_candidate in zip(remaining, lifted):
                    try:
                        holds = replay_serialized_boolean_expression(
                            expr_to_dict(lifted_candidate),
                            query["exact_values"],
                        )
                    except (TypeError, ValueError, ZeroDivisionError):
                        holds = None
                    if holds is False:
                        failed.append(candidate)
            if failed:
                failed_set = set(failed)
                rejected.extend(failed)
                remaining = [
                    item for item in remaining if item not in failed_set
                ]
                continue
            if len(remaining) == 1:
                rejected.extend(remaining)
                return
            middle = len(remaining) // 2
            check(remaining[:middle])
            check(remaining[middle:])
            return

    check(candidates)
    return proved, rejected


def _batch_proof_record(
    candidates: list[Expr],
    query: dict[str, Any],
) -> dict[str, Any]:
    return {
        "candidates": [expr_to_dict(item) for item in candidates],
        "candidate_sha256": [expression_hash(item) for item in candidates],
        "query": query,
    }


def _prepare_shared_relational_region(
    model: EquationModel,
    context: ReachabilityContext,
    *,
    timeout_ms: int,
) -> _SharedRelationalRegion:
    deadline = monotonic() + (timeout_ms / 1000.0)
    context_record = _shared_context_record(context)
    context_sha256 = shared_reachability_context_sha256(context)
    initial = _relational_query([
        _relational_lift(item, context, 0)
        for item in context.initial_constraints
    ])
    domain = _relational_lift(context.domain, context, 0)
    transition = _relational_transition(context)
    candidates = _candidate_invariants(context)
    query_runner = _IncrementalSmtQueryRunner(
        model,
        context,
        _relational_query([
            initial,
            domain,
            transition,
            *[
                _relational_lift(item, context, frame)
                for frame in (0, 1)
                for item in candidates
            ],
        ]),
    )
    query_cache: dict[str, dict[str, Any]] = {}
    query_cache_hits = 0

    def run_query(expression: Expr) -> dict[str, Any]:
        nonlocal query_cache_hits
        key = expression_hash(expression)
        if key in query_cache:
            query_cache_hits += 1
            return query_cache[key]
        result = _run_smt_query(
            model,
            context,
            expression,
            timeout_ms=_remaining_timeout_ms(deadline),
            replay_sat_query=False,
            runner=query_runner,
        )
        query_cache[key] = result
        return result

    initiated, _rejected_initial = _batched_candidate_filter(
        candidates,
        initial,
        context,
        0,
        run_query,
    )

    active = list(initiated)
    changed = True
    while changed and active:
        current_invariant = _relational_query([
            _relational_lift(item, context, 0) for item in active
        ])
        preservation_antecedent = _relational_query([
            current_invariant,
            domain,
            transition,
        ])
        preserved, rejected = _batched_candidate_filter(
            active,
            preservation_antecedent,
            context,
            1,
            run_query,
        )
        changed = bool(rejected)
        active = preserved

    if not active:
        raise ProofDeferred(
            "INVARIANT_NOT_FOUND",
            "no generated state constraint was both initial and preserved",
        )

    preserved_candidate_count = len(active)
    implication_removals: list[dict[str, Any]] = []
    reduced = list(active)
    for candidate in list(active):
        remaining = [item for item in reduced if item != candidate]
        if not remaining:
            continue
        implication_query = _relational_query([
            *[_relational_lift(item, context, 0) for item in remaining],
            Op("not", (_relational_lift(candidate, context, 0),)),
        ])
        query = run_query(implication_query)
        if query.get("solver_status") != "unsat":
            continue
        reduced = remaining
        implication_removals.append({
            "removed_candidate": expr_to_dict(candidate),
            "removed_candidate_sha256": expression_hash(candidate),
            "remaining_invariant_sha256": [
                expression_hash(item) for item in remaining
            ],
            "query": query,
        })
    active = reduced

    current_invariant = _relational_query([
        _relational_lift(item, context, 0) for item in active
    ])
    initiation_expression = _relational_query([
        initial,
        Op("not", (_relational_query([
            _relational_lift(item, context, 0) for item in active
        ]),)),
    ])
    initiation_query = run_query(initiation_expression)
    if initiation_query.get("solver_status") != "unsat":
        raise ProofDeferred(
            str(initiation_query.get("reason_code") or "INVARIANT_NOT_INITIAL"),
            str(initiation_query.get("detail") or "the retained state constraints are not initial"),
        )

    preservation_expression = _relational_query([
        current_invariant,
        domain,
        transition,
        Op("not", (_relational_query([
            _relational_lift(item, context, 1) for item in active
        ]),)),
    ])
    preservation_query = run_query(preservation_expression)
    if preservation_query.get("solver_status") != "unsat":
        raise ProofDeferred(
            str(preservation_query.get("reason_code") or "INVARIANT_NOT_PRESERVED"),
            str(preservation_query.get("detail") or "the retained state constraints are not preserved"),
        )

    record = {
        "rule": "shared_relational_reachable_region_v2",
        "context": context_record,
        "context_sha256": context_sha256,
        "initial_expression": expr_to_dict(initial),
        "initial_expression_sha256": expression_hash(initial),
        "domain_expression": expr_to_dict(domain),
        "domain_expression_sha256": expression_hash(domain),
        "transition_expression": expr_to_dict(transition),
        "transition_expression_sha256": expression_hash(transition),
        "candidate_count": len(candidates),
        "initiated_candidate_count": len(initiated),
        "preserved_candidate_count": preserved_candidate_count,
        "invariant": [expr_to_dict(item) for item in active],
        "invariant_sha256": [expression_hash(item) for item in active],
        "implication_removals": implication_removals,
        "initiation_batches": [_batch_proof_record(active, initiation_query)],
        "preservation_batches": [_batch_proof_record(active, preservation_query)],
        "unique_query_count": len(query_cache),
        "query_cache_hits": query_cache_hits,
    }
    return _SharedRelationalRegion(
        context_sha256=context_sha256,
        invariant=tuple(active),
        record=record,
        query_runner=query_runner,
    )


def _run_relational_invariant_checker(
    model: EquationModel,
    reduced_case: ReducedCase,
    context: ReachabilityContext,
    *,
    timeout_ms: int,
    cache: SharedReachabilityCache,
) -> dict[str, Any]:
    if reduced_case.obligation != "physical_interval":
        return {
            "outcome": "DEFERRED",
            "reason_code": "BLOCKED_INPUT",
            "detail": "the relational invariant applies only to physical interval cases",
            "applicability_checks": {"accepted": False},
        }
    if z3 is None:
        return {
            "outcome": "DEFERRED",
            "reason_code": "BLOCKED_INPUT",
            "detail": "z3-solver is unavailable",
            "applicability_checks": {"accepted": False},
        }

    deadline = monotonic() + (timeout_ms / 1000.0)
    source_hash = expression_hash(reduced_case.expression)
    reachability_hash = expression_hash(
        reduced_case.reachability_expression or reduced_case.expression
    )
    context_sha256 = shared_reachability_context_sha256(context)
    if context_sha256 in cache.region_failures:
        return cache.region_failures[context_sha256]
    shared = cache.regions.get(context_sha256)
    if shared is None:
        shared = _prepare_shared_relational_region(
            model,
            context,
            timeout_ms=_remaining_timeout_ms(deadline),
        )
        cache.regions[context_sha256] = shared
    domain = _relational_lift(context.domain, context, 0)
    current_invariant = _relational_query([
        _relational_lift(item, context, 0) for item in shared.invariant
    ])
    unsafe = reduced_case.reachability_expression or reduced_case.expression
    mode = _mode_condition(reduced_case, {
        name: f"rel_action_0__{name}"
        for name in context.action_variables
    })
    safety_expression = _relational_query([
        current_invariant,
        domain,
        mode,
        _relational_lift(unsafe, context, 0),
    ])
    safety_expression_sha256 = expression_hash(safety_expression)
    safety_key = hashlib.sha256(
        f"{context_sha256}:{safety_expression_sha256}".encode("utf-8")
    ).hexdigest()
    safety_record = cache.safety_queries.get(safety_key)
    reused_safety_query = safety_record is not None
    merge_rule = "identical_expression" if reused_safety_query else "new_query"
    if safety_record is None:
        current_conjuncts = _conjunct_hashes(safety_expression)
        for existing_key, existing_expression in cache.safety_expressions.items():
            existing_record = cache.safety_queries[existing_key]
            if existing_record.get("context_sha256") != context_sha256:
                continue
            if existing_record.get("query", {}).get("solver_status") != "unsat":
                continue
            if _conjunct_hashes(existing_expression) <= current_conjuncts:
                safety_key = existing_key
                safety_record = existing_record
                reused_safety_query = True
                merge_rule = "conjunct_containment"
                break
    if safety_record is None:
        safety_query = _run_smt_query(
            model,
            context,
            safety_expression,
            timeout_ms=_remaining_timeout_ms(deadline),
            runner=shared.query_runner,
        )
        safety_record = {
            "rule": "merged_relational_safety_query_v1",
            "context_sha256": context_sha256,
            "safety_expression": expr_to_dict(safety_expression),
            "safety_expression_sha256": safety_expression_sha256,
            "case_ids": [],
            "covered_case_expressions": [],
            "query": safety_query,
        }
        cache.safety_queries[safety_key] = safety_record
        cache.safety_expressions[safety_key] = safety_expression
    safety_record["case_ids"].append(reduced_case.case_id)
    safety_record["covered_case_expressions"].append({
        "case_id": reduced_case.case_id,
        "expression": expr_to_dict(safety_expression),
        "expression_sha256": safety_expression_sha256,
        "merge_rule": merge_rule,
    })
    safety_query = safety_record["query"]
    proof = {
        "rule": "shared_relational_inductive_invariant_v2",
        "case_id": reduced_case.case_id,
        "case_expression_sha256": source_hash,
        "reachability_expression_sha256": reachability_hash,
        "shared_reachable_region_sha256": context_sha256,
        "merged_safety_query_sha256": safety_key,
        "case_safety_expression_sha256": safety_expression_sha256,
        "merge_rule": merge_rule,
        "reused_safety_query": reused_safety_query,
    }
    if safety_query.get("solver_status") == "unsat":
        return {
            "outcome": "CERTIFIED",
            "reason_code": "",
            "detail": "a checked relational invariant excludes the unsafe physical interval",
            "applicability_checks": {
                "accepted": True,
                "case_id": reduced_case.case_id,
                "complete_initialization": True,
                "complete_transition_encoding": True,
                "candidate_count": shared.record["candidate_count"],
                "invariant_clause_count": len(shared.invariant),
                "shared_reachable_region": True,
                "merged_identical_case": reused_safety_query,
            },
            "proof": proof,
        }
    return {
        "outcome": "DEFERRED",
        "reason_code": str(safety_query.get("reason_code") or "INVARIANT_TOO_WEAK"),
        "detail": str(safety_query.get("detail") or "the preserved state constraint intersects the unsafe interval"),
        "applicability_checks": {
            "accepted": True,
            "case_id": reduced_case.case_id,
        },
        "proof": proof,
    }


def _run_relational_invariant_group(
    model: EquationModel,
    reduced_cases: list[ReducedCase],
    context: ReachabilityContext,
    *,
    timeout_ms: int,
    cache: SharedReachabilityCache,
) -> dict[str, dict[str, Any]]:
    if not reduced_cases:
        return {}
    if any(item.obligation != "physical_interval" for item in reduced_cases):
        return {
            item.case_id: {
                "outcome": "DEFERRED",
                "reason_code": "BLOCKED_INPUT",
                "detail": "grouped relational checks require physical interval cases",
                "applicability_checks": {"accepted": False},
            }
            for item in reduced_cases
        }
    if z3 is None:
        return {
            item.case_id: {
                "outcome": "DEFERRED",
                "reason_code": "BLOCKED_INPUT",
                "detail": "z3-solver is unavailable",
                "applicability_checks": {"accepted": False},
            }
            for item in reduced_cases
        }

    deadline = monotonic() + (timeout_ms / 1000.0)
    context_sha256 = shared_reachability_context_sha256(context)
    if context_sha256 in cache.region_failures:
        failure = cache.region_failures[context_sha256]
        return {item.case_id: dict(failure) for item in reduced_cases}
    shared = cache.regions.get(context_sha256)
    if shared is None:
        shared = _prepare_shared_relational_region(
            model,
            context,
            timeout_ms=_remaining_timeout_ms(deadline),
        )
        cache.regions[context_sha256] = shared

    domain = _relational_lift(context.domain, context, 0)
    current_invariant = _relational_query([
        _relational_lift(item, context, 0) for item in shared.invariant
    ])
    case_safety: list[tuple[ReducedCase, Expr]] = []
    for reduced_case in reduced_cases:
        unsafe = reduced_case.reachability_expression or reduced_case.expression
        mode = _mode_condition(reduced_case, {
            name: f"rel_action_0__{name}"
            for name in context.action_variables
        })
        case_safety.append((
            reduced_case,
            _relational_query([
                current_invariant,
                domain,
                mode,
                _relational_lift(unsafe, context, 0),
            ]),
        ))
    group_expression = simplify(Op(
        "or",
        tuple(expression for _case, expression in case_safety),
    ))
    group_expression_sha256 = expression_hash(group_expression)
    safety_key = hashlib.sha256(
        f"{context_sha256}:{group_expression_sha256}".encode("utf-8")
    ).hexdigest()
    safety_record = cache.safety_queries.get(safety_key)
    if safety_record is None:
        query = _run_smt_query(
            model,
            context,
            group_expression,
            timeout_ms=_remaining_timeout_ms(deadline),
            runner=shared.query_runner,
        )
        safety_record = {
            "rule": "grouped_relational_safety_query_v2",
            "context_sha256": context_sha256,
            "safety_expression": expr_to_dict(group_expression),
            "safety_expression_sha256": group_expression_sha256,
            "case_ids": [item.case_id for item, _expression in case_safety],
            "covered_case_expressions": [
                {
                    "case_id": item.case_id,
                    "expression": expr_to_dict(expression),
                    "expression_sha256": expression_hash(expression),
                    "merge_rule": "group_disjunction",
                }
                for item, expression in case_safety
            ],
            "query": query,
        }
        cache.safety_queries[safety_key] = safety_record
        cache.safety_expressions[safety_key] = group_expression
    query = safety_record["query"]
    certified = query.get("solver_status") == "unsat"
    attempts: dict[str, dict[str, Any]] = {}
    for reduced_case, safety_expression in case_safety:
        proof = {
            "rule": "shared_relational_inductive_invariant_v2",
            "case_id": reduced_case.case_id,
            "case_expression_sha256": expression_hash(reduced_case.expression),
            "reachability_expression_sha256": expression_hash(
                reduced_case.reachability_expression or reduced_case.expression
            ),
            "shared_reachable_region_sha256": context_sha256,
            "merged_safety_query_sha256": safety_key,
            "case_safety_expression_sha256": expression_hash(safety_expression),
            "merge_rule": "group_disjunction",
            "reused_safety_query": len(reduced_cases) > 1,
        }
        attempts[reduced_case.case_id] = {
            "outcome": "CERTIFIED" if certified else "DEFERRED",
            "reason_code": "" if certified else str(
                query.get("reason_code") or "GROUP_SAFETY_INCONCLUSIVE"
            ),
            "detail": (
                f"one checked relational query excludes {len(reduced_cases)} unsafe cases"
                if certified
                else "the grouped unsafe cases require automatic subdivision"
            ),
            "applicability_checks": {
                "accepted": True,
                "case_id": reduced_case.case_id,
                "group_case_count": len(reduced_cases),
                "complete_initialization": True,
                "complete_transition_encoding": True,
                "candidate_count": shared.record["candidate_count"],
                "invariant_clause_count": len(shared.invariant),
                "shared_reachable_region": True,
            },
            "proof": proof,
        }
    return attempts


def run_relational_invariant_group_checker(
    model: EquationModel,
    reduced_cases: list[ReducedCase],
    context: ReachabilityContext,
    *,
    timeout_ms: int,
    cache: SharedReachabilityCache,
) -> dict[str, dict[str, Any]]:
    """Certify groups and divide only groups whose combined query is inconclusive."""

    results: dict[str, dict[str, Any]] = {}

    def check(group: list[ReducedCase]) -> None:
        try:
            with _hard_timeout(timeout_ms):
                attempts = _run_relational_invariant_group(
                    model,
                    group,
                    context,
                    timeout_ms=timeout_ms,
                    cache=cache,
                )
        except ProofDeferred as exc:
            attempts = {
                item.case_id: {
                    "outcome": "DEFERRED",
                    "reason_code": exc.reason_code,
                    "detail": exc.detail,
                    "applicability_checks": {
                        "accepted": True,
                        "case_id": item.case_id,
                    },
                }
                for item in group
            }
            context_sha256 = shared_reachability_context_sha256(context)
            cache.region_failures.setdefault(
                context_sha256,
                next(iter(attempts.values())),
            )
        if attempts and all(
            item.get("outcome") == "CERTIFIED" for item in attempts.values()
        ):
            results.update(attempts)
            return
        if len(group) == 1:
            results.update(attempts)
            return
        middle = len(group) // 2
        check(group[:middle])
        check(group[middle:])

    check(list(reduced_cases))
    return results


def run_relational_invariant_checker(
    model: EquationModel,
    reduced_case: ReducedCase,
    context: ReachabilityContext,
    *,
    timeout_ms: int,
    cache: SharedReachabilityCache | None = None,
) -> dict[str, Any]:
    """Prove safety from a compact current-to-next-state invariant."""

    shared_cache = cache if cache is not None else SharedReachabilityCache()
    context_sha256 = shared_reachability_context_sha256(context)
    try:
        with _hard_timeout(timeout_ms):
            return _run_relational_invariant_checker(
                model,
                reduced_case,
                context,
                timeout_ms=timeout_ms,
                cache=shared_cache,
            )
    except ProofDeferred as exc:
        failure = {
            "outcome": "DEFERRED",
            "reason_code": exc.reason_code,
            "detail": exc.detail,
            "applicability_checks": {
                "accepted": True,
                "case_id": reduced_case.case_id,
            },
        }
        shared_cache.region_failures.setdefault(context_sha256, failure)
        return failure
