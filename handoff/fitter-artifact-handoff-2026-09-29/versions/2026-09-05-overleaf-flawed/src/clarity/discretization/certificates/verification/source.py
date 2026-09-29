"""Validate a recorded analysis against verifier-owned source reconstruction."""

from __future__ import annotations

from typing import Any

from clarity.certification.equations import Const, Op, Var
from clarity.certification.strict_extract import CertificationExtractor

from .expressions import (
    INTERVAL_TIME,
    _factored_expr_from_dict,
    expr_to_dict,
    expression_symbols,
    simplify,
    substitute,
)
from .source_reconstruction import (
    CHECKER_ORDER,
    _and,
    _apply_recorded_removals,
    _boolean_variables,
    _constant_values,
    _continuous_targets,
    _dependency_closure,
    _expand_definitions,
    _expand_post_update,
    _expanded_references,
    _integer_variables,
    _inventory,
    _physical_aliases,
    _policy_call_guards,
    _required_mapping_guards,
    _scenario_constraints,
    _shield_expression,
    _timing_record,
    _trajectory,
)


def verify_analysis_source(
    model_path: str,
    mdp_certificate: dict[str, Any],
    dt_record: dict[str, Any],
    analysis: dict[str, Any],
) -> list[str]:
    """Verify that every recorded proof input is reconstructed from SysML."""

    errors: list[str] = []
    try:
        extractor = CertificationExtractor(model_path)
        model = extractor.extract()
        continuous, continuous_records = _continuous_targets(extractor, model)
        constants = _constant_values(extractor, model, dt_record)
        booleans = _boolean_variables(extractor, model, mdp_certificate)
        integers = _integer_variables(extractor, model)
        shield = substitute(
            _shield_expression(extractor, model, mdp_certificate),
            constants,
        )
        guards = [
            substitute(item, constants)
            for item in _policy_call_guards(extractor, model)
        ]
        scenarios, initial_scenarios = _scenario_constraints(extractor, model)
        scenarios = [substitute(item, constants) for item in scenarios]
        initial_scenarios = [
            substitute(item, constants) for item in initial_scenarios
        ]
    except (KeyError, TypeError, ValueError) as exc:
        return [f"source reconstruction failed: {exc}"]

    if analysis.get("timing") != _timing_record(mdp_certificate, dt_record):
        errors.append("analysis timing does not match source evidence")
    if analysis.get("continuous_rate_assignments") != continuous_records:
        errors.append("continuous rate assignments do not match the SysML model")
    if analysis.get("specified_constant_values") != {
        name: expr_to_dict(value) for name, value in sorted(constants.items())
    }:
        errors.append("specified constant values do not match the SysML model")
    if analysis.get("checker_order") != CHECKER_ORDER:
        errors.append("checker order is invalid")
    if analysis.get("markov_process_evidence") != {
        "buffer": mdp_certificate.get("buffer", {}),
        "reconstructed_state": mdp_certificate.get("sets", {}).get("q", []),
        "executed_actions": mdp_certificate.get("sets", {}).get("actions", []),
        "time_variables": mdp_certificate.get("sets", {}).get("time_vars", []),
    }:
        errors.append("Markov process evidence does not match its certificate")

    requirements = [
        equation
        for _target, equation in sorted(model.requirements.items())
        if equation.source in {"Prohibition", "Obligation"}
    ]
    properties = analysis.get("properties")
    if not isinstance(properties, list):
        return errors + ["analysis properties are malformed"]
    expected_ids = [
        equation.target.removeprefix("status.") for equation in requirements
    ]
    if [item.get("property_id") for item in properties] != expected_ids:
        return errors + ["analysis properties do not match the SysML requirements"]

    for equation, property_record in zip(requirements, properties):
        property_id = equation.target.removeprefix("status.")

        def mismatch(field: str) -> None:
            errors.append(f"property {property_id} {field} does not match the source")

        if property_record.get("annotation") != equation.source:
            mismatch("annotation")
        if property_record.get("source") != equation.pretty():
            mismatch("equation")
        if property_record.get("dependencies") != sorted(
            _expanded_references(model, equation)
        ):
            mismatch("dependencies")
        reduction = property_record.get("reduction")
        if not isinstance(reduction, dict) or reduction.get("outcome") == "DEFERRED":
            continue
        original = _expand_definitions(model, equation.expr)
        terminal_equation = model.terminals.get("env.completion.done")
        terminal = (
            _expand_definitions(model, terminal_equation.expr)
            if terminal_equation is not None
            else Const(False)
        )
        changing = {
            target
            for target, transition in model.transitions.items()
            if any(
                name == "dt" or name.endswith("_dt")
                for name in expression_symbols(transition.expr)
            )
        }
        aliases, alias_records = _physical_aliases(
            model,
            [original, shield, terminal],
            changing,
        )
        guard_evidence: list[dict[str, Any]] = []
        for mapping in alias_records:
            required = _required_mapping_guards(
                model,
                mapping["sampled_value"],
                mapping["physical_value"],
            )
            mapping["guard_evidence"] = [expr_to_dict(item) for item in required]
            guard_evidence.extend({
                "sampled_value": mapping["sampled_value"],
                "guard": expr_to_dict(item),
                "matched_controller_call_guard": item in guards,
            } for item in required)
        needed_physical = set(aliases.values()) | (
            expression_symbols(original) & continuous
        )
        trajectories: dict[str, Expr] = {}
        trajectory_rows: list[dict[str, Any]] = []
        for target in sorted(needed_physical):
            trajectory, dt_symbols = _trajectory(
                model,
                target,
                continuous,
                constants,
            )
            trajectories[target] = trajectory
            trajectory_rows.append({
                "physical_value": target,
                "source_assignment": expr_to_dict(model.transitions[target].expr),
                "dt_symbols": dt_symbols,
                "trajectory": expr_to_dict(trajectory),
                "rule": "continuous_rate_assignment_with_held_effective_action_v1",
            })
        physical_start = {
            sampled: Var(target) for sampled, target in aliases.items()
        }
        shield_at_start = simplify(substitute(shield, physical_start))
        terminal_at_start = simplify(substitute(
            _expand_post_update(
                model,
                substitute(terminal, physical_start),
                continuous,
            ),
            constants,
        ))
        interval_values = {
            sampled: trajectories[target]
            for sampled, target in aliases.items()
            if target in trajectories
        }
        interval_values.update(trajectories)
        interval_property = simplify(substitute(
            simplify(substitute(
                _expand_post_update(
                    model,
                    substitute(original, physical_start),
                    continuous,
                ),
                interval_values,
            )),
            constants,
        ))
        start_values = {
            sampled: Var(target) for sampled, target in aliases.items()
        }
        start_values.update({target: Var(target) for target in continuous})
        start_property = simplify(substitute(
            simplify(substitute(
                _expand_post_update(
                    model,
                    substitute(original, physical_start),
                    continuous,
                ),
                start_values,
            )),
            constants,
        ))
        if reduction.get("original_property") != expr_to_dict(original):
            mismatch("original property")
        if reduction.get("physical_start_property") != expr_to_dict(start_property):
            mismatch("physical start property")
        if reduction.get("physical_interval_property") != expr_to_dict(
            interval_property
        ):
            mismatch("physical interval property")
        if reduction.get("sensor_to_physical_mappings") != alias_records:
            mismatch("sensor mapping")
        if reduction.get("sensor_mapping_guard_evidence") != guard_evidence:
            mismatch("sensor guard evidence")
        if reduction.get("trajectories") != trajectory_rows:
            mismatch("trajectory reconstruction")

        premises = [shield_at_start, *guards, *scenarios]
        sampled_raw = simplify(_and([
            *premises,
            Op("not", (start_property,)),
        ]))
        interval_raw = simplify(_and([
            *premises,
            start_property,
            Op(">=", (Var(INTERVAL_TIME), Const(0))),
            Op("<=", (Var(INTERVAL_TIME), Const(dt_record["canonical"]))),
            Op("not", (interval_property,)),
        ]))
        removals = reduction.get("pre_case_constraint_reduction") or {}
        try:
            sampled_expected = _apply_recorded_removals(
                sampled_raw,
                removals.get("sampled_point_removed_constraints"),
            )
            interval_expected = _apply_recorded_removals(
                interval_raw,
                removals.get("physical_interval_removed_constraints"),
            )
        except ValueError:
            mismatch("constraint reduction")
            sampled_expected = sampled_raw
            interval_expected = interval_raw
        if reduction.get("sampled_point_counterexample") != expr_to_dict(
            sampled_expected
        ):
            mismatch("sampled point counterexample")
        if reduction.get("interval_counterexample") != expr_to_dict(
            interval_expected
        ):
            mismatch("interval counterexample")

        sampled_premise = reduction.get("sampled_point_premise") or {}
        if sampled_premise.get("controller_contract") != expr_to_dict(
            shield_at_start
        ):
            mismatch("controller contract")
        if sampled_premise.get("physical_property_at_interval_start") != expr_to_dict(
            start_property
        ):
            mismatch("sampled physical property")
        if sampled_premise.get("controller_call_guards") != [
            expr_to_dict(item) for item in guards
        ]:
            mismatch("controller call guards")
        if sampled_premise.get("scenario_constraints") != [
            expr_to_dict(item) for item in scenarios
        ]:
            mismatch("scenario constraints")

        endpoint_checks = [
            {
                "physical_value": target,
                "trajectory_at_dt": expr_to_dict(simplify(substitute(
                    trajectory,
                    {INTERVAL_TIME: Const(dt_record["canonical"])},
                ))),
                "extracted_next_value": expr_to_dict(simplify(substitute(
                    _expand_post_update(
                        model,
                        model.transitions[target].expr,
                        continuous,
                    ),
                    constants,
                ))),
                "matches": simplify(substitute(
                    trajectory,
                    {INTERVAL_TIME: Const(dt_record["canonical"])},
                )) == simplify(substitute(
                    _expand_post_update(
                        model,
                        model.transitions[target].expr,
                        continuous,
                    ),
                    constants,
                )),
            }
            for target, trajectory in sorted(trajectories.items())
        ]
        if reduction.get("endpoint_checks") != endpoint_checks:
            mismatch("endpoint checks")

        recorded_counterexample = reduction.get("interval_counterexample")
        try:
            dependency_seeds = expression_symbols(
                _factored_expr_from_dict(recorded_counterexample)
            )
        except ValueError:
            dependency_seeds = set()
        for mapping in alias_records:
            dependency_seeds.update({
                mapping["sampled_value"],
                mapping["physical_value"],
            })
        included = _dependency_closure(model, dependency_seeds)
        if reduction.get("equation_inventory") != _inventory(model, included):
            mismatch("equation inventory")

        domain = simplify(_and([*premises, Op("not", (terminal_at_start,))]))
        specified_initial = tuple(
            Op("==", (Var(target), Const(value)))
            for target, value in sorted(model.initial_values.items())
        )
        initial_constraints = specified_initial + tuple(scenarios) + tuple(
            initial_scenarios
        )
        reachability = reduction.get("reachability_mapping") or {}
        targets = set(model.transitions)
        targets.update(expression_symbols(domain) & model.state)
        for case in property_record.get("cases", []):
            try:
                targets.update(expression_symbols(
                    _factored_expr_from_dict(case.get("expression"))
                ) & model.state)
                targets.update(expression_symbols(
                    _factored_expr_from_dict(case.get("reachability_expression"))
                ) & model.state)
            except ValueError:
                mismatch("case expression")
        post_values: dict[str, Expr] = {}
        pending = list(sorted(targets))
        while pending:
            target = pending.pop(0)
            if target in post_values:
                continue
            if target in continuous:
                trajectory = trajectories.get(target)
                if trajectory is None:
                    trajectory, _symbols = _trajectory(
                        model,
                        target,
                        continuous,
                        constants,
                    )
                post = simplify(substitute(
                    trajectory,
                    {INTERVAL_TIME: Const(dt_record["canonical"])},
                ))
            else:
                transition = model.transitions.get(target)
                post = (
                    Var(target)
                    if transition is None
                    else simplify(substitute(
                        _expand_post_update(
                            model,
                            transition.expr,
                            continuous,
                        ),
                        constants,
                    ))
                )
            post_values[target] = post
            for reference in sorted(expression_symbols(post) & model.state):
                if reference not in post_values and reference not in pending:
                    pending.append(reference)
        expected_reachability = {
            "rule": "exact_initial_values_and_complete_sampled_transition_v1",
            "domain": expr_to_dict(domain),
            "completion_condition": expr_to_dict(terminal_at_start),
            "nonterminal_intervals_only": True,
            "initial_constraints": [
                expr_to_dict(item) for item in initial_constraints
            ],
            "specified_initial_constraints": [
                expr_to_dict(item) for item in specified_initial
            ],
            "scenario_initial_constraints": [
                expr_to_dict(item) for item in initial_scenarios
            ],
            "initial_variables": sorted(model.initial_values),
            "post_values": {
                target: expr_to_dict(value)
                for target, value in sorted(post_values.items())
            },
            "action_variables": sorted(model.actions),
            "boolean_variables": sorted(booleans),
            "integer_variables": sorted(integers),
            "complete_for_case_symbols": True,
        }
        if reachability != expected_reachability:
            mismatch("reachability mapping")
    return errors
