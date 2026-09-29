"""Assemble one complete full-model discretization reduction."""

from __future__ import annotations

from typing import Any

from clarity.certification.equations import Const, Equation, EquationModel, Expr, Op, Var

from .case_reduction import factored_obligation
from .constraint_reduction import _normalize_constraint_expression
from .expressions import INTERVAL_TIME, _and, expression_hash, simplify
from .physical_reduction import (
    _equation,
    _expand_post_update,
    _trajectory,
)
from .proof_rules import (
    ProofDeferred,
    expand_definitions,
    expr_to_dict,
    expression_symbols,
    substitute,
)
from .reduction_types import ReachabilityContext, ReducedCase


def _equation_inventory(
    model: EquationModel,
    included_targets: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for equation in sorted(model.all_equations(), key=lambda item: (item.kind, item.target)):
        included = equation.target in included_targets
        rows.append({
            "target": equation.target,
            "kind": equation.kind,
            "source": equation.source,
            "expression": expr_to_dict(equation.expr),
            "included": included,
            "reason": (
                "dependency closure of the interval counterexample"
                if included
                else "outside the dependency closure of the interval counterexample"
            ),
        })
    return rows


def _dependency_closure(model: EquationModel, seeds: set[str]) -> set[str]:
    closure = set(seeds)
    queue = list(sorted(seeds))
    while queue:
        name = queue.pop(0)
        equation = _equation(model, name)
        if equation is None:
            continue
        for reference in sorted(expression_symbols(equation.expr)):
            if reference not in closure:
                closure.add(reference)
                queue.append(reference)
    return closure


def build_reduction(
    extractor,
    model: EquationModel,
    equation: Equation,
    shield_expression: Expr,
    guards: list[Expr],
    scenario_constraints: list[Expr],
    scenario_initial_constraints: list[Expr],
    constant_values: dict[str, Expr],
    continuous: set[str],
    boolean_variables: set[str],
    integer_variables: set[str],
    dt_record: dict[str, Any],
) -> tuple[list[ReducedCase], dict[str, Any], ReachabilityContext]:
    """Build the full physical interval counterexample and its reduction trace."""

    original = expand_definitions(model, equation.expr)
    terminal_equation = model.terminals.get("env.completion.done")
    terminal = (
        expand_definitions(model, terminal_equation.expr)
        if terminal_equation is not None
        else Const(False)
    )
    pairs = model.state_value_pairs
    needed_physical = {pair["physical_value"] for pair in pairs} | (
        expression_symbols(original) & continuous
    )
    missing_annotations = needed_physical - continuous
    if missing_annotations:
        raise ProofDeferred("MISSING_WITHIN_STEP_MEANING", ", ".join(sorted(missing_annotations)))
    trajectories: dict[str, Expr] = {}
    trajectory_rows: list[dict[str, Any]] = []
    for target in sorted(needed_physical):
        trajectory, dt_symbols = _trajectory(
            model, target, continuous, constant_values
        )
        trajectories[target] = trajectory
        trajectory_rows.append({
            "physical_value": target,
            "source_assignment": expr_to_dict(model.transitions[target].expr),
            "dt_symbols": dt_symbols,
            "trajectory": expr_to_dict(trajectory),
            "rule": "continuous_rate_assignment_with_held_effective_action_v1",
        })

    physical_start: dict[str, Expr] = {}  # No equality between sampled and current values.
    shield_at_start = simplify(substitute(shield_expression, physical_start))
    terminal_at_start = _expand_post_update(
        model,
        substitute(terminal, physical_start),
        continuous,
    )
    terminal_at_start = simplify(substitute(
        terminal_at_start,
        constant_values,
    ))

    interval_values: dict[str, Expr] = dict(trajectories)
    interval_property = _expand_post_update(
        model,
        substitute(original, physical_start),
        continuous,
    )
    interval_property = simplify(substitute(interval_property, interval_values))
    interval_property = simplify(substitute(interval_property, constant_values))

    start_values: dict[str, Expr] = {}
    start_values.update({target: Var(target) for target in continuous})
    start_property = _expand_post_update(
        model,
        substitute(original, physical_start),
        continuous,
    )
    start_property = simplify(substitute(start_property, start_values))
    start_property = simplify(substitute(start_property, constant_values))

    dt_exact = Const(dt_record["canonical"])
    controller_premises = [
        simplify(substitute(shield_at_start, constant_values)),
        *(simplify(substitute(item, constant_values)) for item in guards),
        *(simplify(substitute(item, constant_values)) for item in scenario_constraints),
    ]
    sampled_point_counterexample = simplify(_and([
        *controller_premises,
        Op("not", (start_property,)),
    ]))
    sampled_pre_case_removals: list[dict[str, Any]] = []
    sampled_point_counterexample = _normalize_constraint_expression(
        sampled_point_counterexample,
        boolean_variables,
        sampled_pre_case_removals,
    )
    interval_premises = [
        *controller_premises,
        start_property,
        Op(">=", (Var(INTERVAL_TIME), Const(0))),
        Op("<=", (Var(INTERVAL_TIME), dt_exact)),
    ]
    counterexample = simplify(_and([
        *interval_premises,
        Op("not", (interval_property,)),
    ]))
    interval_pre_case_removals: list[dict[str, Any]] = []
    counterexample = _normalize_constraint_expression(
        counterexample,
        boolean_variables,
        interval_pre_case_removals,
    )
    sampled_cases, sampled_coverage = factored_obligation(
        sampled_point_counterexample,
        equation.target,
        "sampled_point",
    )
    interval_cases, interval_coverage = factored_obligation(
        counterexample,
        equation.target,
        "physical_interval",
    )
    cases = sampled_cases + interval_cases
    coverage = {
        "rule": "sampled_point_and_physical_interval_obligations_v2",
        "complete": sampled_coverage["complete"] and interval_coverage["complete"],
        "case_count": len(cases),
        "cases": sampled_coverage["cases"] + interval_coverage["cases"],
        "obligations": {
            "sampled_point": sampled_coverage,
            "physical_interval": interval_coverage,
        },
    }

    dependency_seeds = expression_symbols(counterexample)
    for record in pairs:
        dependency_seeds.add(record["sampled_value"])
        dependency_seeds.add(record["physical_value"])
    included_targets = _dependency_closure(model, dependency_seeds)

    domain = simplify(_and([
        *controller_premises,
        Op("not", (terminal_at_start,)),
    ]))
    specified_initial_constraints = tuple(
        Op("==", (Var(target), Const(value)))
        for target, value in sorted(model.initial_values.items())
    )
    initial_constraints = (
        specified_initial_constraints
        + tuple(scenario_constraints)
        + tuple(scenario_initial_constraints)
    )
    reachability_targets = set(model.transitions)
    reachability_targets.update(expression_symbols(domain) & model.state)
    for case in cases:
        reachability_targets.update(expression_symbols(case.expression) & model.state)
        if case.reachability_expression is not None:
            reachability_targets.update(
                expression_symbols(case.reachability_expression) & model.state
            )
    post_values: dict[str, Expr] = {}
    pending = list(sorted(reachability_targets))
    while pending:
        target = pending.pop(0)
        if target in post_values:
            continue
        if target in continuous:
            trajectory = trajectories.get(target)
            if trajectory is None:
                trajectory, _dt_symbols = _trajectory(
                    model, target, continuous, constant_values
                )
            post = simplify(substitute(
                trajectory,
                {INTERVAL_TIME: dt_exact},
            ))
        else:
            transition = model.transitions.get(target)
            if transition is None:
                post = Var(target)
            else:
                post = simplify(substitute(
                    _expand_post_update(
                        model,
                        transition.expr,
                        continuous,
                    ),
                    constant_values,
                ))
        post_values[target] = post
        for reference in sorted(expression_symbols(post) & model.state):
            if reference not in post_values and reference not in pending:
                pending.append(reference)
    cycle_order = [
        "queued actuator state machine changes",
        "same cycle constraint propagation",
        *[
            f"owned step action {index}: {fqn}"
            for index, (fqn, _body) in enumerate(extractor.parser.step_action_bodies)
        ],
    ]
    record = {
        "kind": "full_sysml_interval_reduction_v4",
        "property_id": equation.target.removeprefix("status."),
        "annotation": equation.source,
        "original_property": expr_to_dict(original),
        "physical_start_property": expr_to_dict(start_property),
        "physical_interval_property": expr_to_dict(interval_property),
        "sampled_point_counterexample": expr_to_dict(sampled_point_counterexample),
        "sampled_point_counterexample_sha256": expression_hash(
            sampled_point_counterexample
        ),
        "interval_counterexample": expr_to_dict(counterexample),
        "interval_counterexample_sha256": expression_hash(counterexample),
        "pre_case_constraint_reduction": {
            "rule": "recursive_canonical_constraint_reduction_v1",
            "sampled_point_removed_constraints": sampled_pre_case_removals,
            "physical_interval_removed_constraints": interval_pre_case_removals,
        },
        "interval": {
            "time_variable": INTERVAL_TIME,
            "lower": "0/1",
            "upper": dt_record["canonical"],
            "fixed_dt": dt_record,
        },
        "cycle_order": cycle_order,
        "action_use": (
            "The controller output selected at the current reading is held through "
            "the following physical interval and is substituted through actuator equations."
        ),
        "state_value_pairs": pairs,
        "sampled_values_held": sorted(model.sampled_state),
        "trajectories": trajectory_rows,
        "sampled_point_premise": {
            "controller_contract": expr_to_dict(shield_at_start),
            "physical_property_at_interval_start": expr_to_dict(start_property),
            "controller_call_guards": [expr_to_dict(item) for item in guards],
            "scenario_constraints": [expr_to_dict(item) for item in scenario_constraints],
            "proof_rule": (
                "The controller contract and controller call guards must imply the "
                "physical property at every controller update."
            ),
        },
        "endpoint_checks": [
            {
                "physical_value": target,
                "trajectory_at_dt": expr_to_dict(simplify(substitute(
                    trajectory, {INTERVAL_TIME: dt_exact}
                ))),
                "extracted_next_value": expr_to_dict(simplify(substitute(
                    _expand_post_update(model, model.transitions[target].expr, continuous),
                    constant_values,
                ))),
                "matches": simplify(substitute(
                    trajectory, {INTERVAL_TIME: dt_exact}
                )) == simplify(substitute(
                    _expand_post_update(model, model.transitions[target].expr, continuous),
                    constant_values,
                )),
            }
            for target, trajectory in sorted(trajectories.items())
        ],
        "equation_inventory": _equation_inventory(model, included_targets),
        "case_coverage": coverage,
        "reachability_mapping": {
            "rule": "exact_initial_values_and_complete_sampled_transition_v1",
            "domain": expr_to_dict(domain),
            "completion_condition": expr_to_dict(terminal_at_start),
            "nonterminal_intervals_only": True,
            "initial_constraints": [
                expr_to_dict(item) for item in initial_constraints
            ],
            "specified_initial_constraints": [
                expr_to_dict(item) for item in specified_initial_constraints
            ],
            "scenario_initial_constraints": [
                expr_to_dict(item) for item in scenario_initial_constraints
            ],
            "initial_variables": sorted(model.initial_values),
            "post_values": {
                target: expr_to_dict(value)
                for target, value in sorted(post_values.items())
            },
            "action_variables": sorted(model.actions),
            "boolean_variables": sorted(boolean_variables),
            "integer_variables": sorted(integer_variables),
            "complete_for_case_symbols": True,
        },
        "human_description": (
            "Sampled readings and current physical values occupy distinct storage. "
            "Held samples remain unchanged within each sample-free interval. The "
            "annotated physical equations then define every listed trajectory for "
            "all times from zero through dt. The cases are the exhaustive unsafe "
            "alternatives of that complete interval statement."
        ),
    }
    if not all(item["matches"] for item in record["endpoint_checks"]):
        raise ProofDeferred(
            "MISSING_WITHIN_STEP_MEANING",
            "a physical trajectory does not match its extracted sampled endpoint",
        )
    reachability = ReachabilityContext(
        domain=domain,
        initial_constraints=initial_constraints,
        initial_variables=tuple(sorted(model.initial_values)),
        post_values=tuple(sorted(post_values.items())),
        action_variables=tuple(sorted(model.actions)),
        boolean_variables=tuple(sorted(boolean_variables)),
        integer_variables=tuple(sorted(integer_variables)),
    )
    return cases, record, reachability
