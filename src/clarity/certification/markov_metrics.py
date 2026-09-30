"""Pre-SMT structural metrics and fail-closed complexity budgets."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .markov_interval import FiniteDecisionInterval
from .markov_obligations import ObligationManifest
from .markov_slice import TheoremSlice


@dataclass(frozen=True)
class FormulaBudget:
    max_solver_queries: int = 512
    max_state_terms_per_query: int = 4096
    max_event_instances_per_query: int = 4096
    max_control_selectors_per_query: int = 8192


@dataclass(frozen=True)
class FormulaMetrics:
    history_length: int
    history_cases: int
    solver_queries: int
    structural_predicates: int
    retained_state_terms_per_run: int
    shared_buffer_slots: int
    estimated_state_terms_per_query: int
    estimated_event_instances_per_query: int
    estimated_control_selectors_per_query: int
    native_sort_counts: tuple[tuple[str, int], ...]
    generic_runtime_sorts: int


@dataclass(frozen=True)
class BudgetReport:
    accepted: bool
    violations: tuple[str, ...]


def formula_metrics(
    slice_: TheoremSlice,
    interval: FiniteDecisionInterval,
    manifest: ObligationManifest,
) -> FormulaMetrics:
    length = max(manifest.b_obs, manifest.b_act)
    observation_width = sum(
        term.name.startswith("next_observation.") for term in slice_.visible_terms
    )
    shared_slots = observation_width * (manifest.b_obs + 1) + manifest.b_act
    transition_copies = length + 1  # history window plus the current transition
    per_run = len(slice_.storages)
    state_terms = 2 * per_run * transition_copies + shared_slots
    event_instances = 2 * len(slice_.events) * transition_copies
    selectors = 2 * len(interval.states) * transition_copies
    sorts = Counter(item.identity.native_sort.value for item in slice_.storages)
    structural = len(manifest.structural_obligations)
    generic = sum(sorts[name] for name in
                  {"runtime_record", "runtime_queue", "runtime_stack"})
    return FormulaMetrics(
        history_length=length,
        history_cases=len(manifest.history_cases),
        solver_queries=len(manifest.queries),
        structural_predicates=structural,
        retained_state_terms_per_run=per_run,
        shared_buffer_slots=shared_slots,
        estimated_state_terms_per_query=state_terms,
        estimated_event_instances_per_query=event_instances,
        estimated_control_selectors_per_query=selectors,
        native_sort_counts=tuple(sorted(sorts.items())),
        generic_runtime_sorts=generic,
    )


def check_formula_budget(
    metrics: FormulaMetrics,
    budget: FormulaBudget = FormulaBudget(),
) -> BudgetReport:
    violations = []
    checks = (
        (metrics.solver_queries, budget.max_solver_queries, "solver queries"),
        (metrics.estimated_state_terms_per_query, budget.max_state_terms_per_query,
         "estimated state terms per query"),
        (metrics.estimated_event_instances_per_query, budget.max_event_instances_per_query,
         "estimated event instances per query"),
        (metrics.estimated_control_selectors_per_query,
         budget.max_control_selectors_per_query, "estimated control selectors per query"),
    )
    for actual, limit, label in checks:
        if actual > limit:
            violations.append(f"{label}: {actual} exceeds budget {limit}")
    if metrics.generic_runtime_sorts:
        violations.append(
            f"generic runtime sorts: expected 0, found {metrics.generic_runtime_sorts}"
        )
    return BudgetReport(not violations, tuple(violations))
