"""Solver-independent logical obligations for the buffered Markov proof.

This module gives the paired-execution proof an explicit constraint-logic
meaning.  It compiles one model instance; it does not validate the general
proof-rule schemas.  General rule validation lives in the separate
``proof_logic_validation`` program and is not rerun for each model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .constraint_logic import (
    LOGIC_PROFILE,
    LogicSequent,
    LogicSort,
    LogicTerm,
    apply,
    compile_expression,
    compile_scenario_domain,
    conjunction,
    function,
    literal,
    lower_to_z3,
    sort_from_sysml,
    symbol,
)
from .ot_markov import MAX_SMT2_BYTES, OTMarkovModel, _parameter_maps, _z3


MARKOV_LOGIC_PROFILE = "ot-markov-constraint-logic-0.1"


def _equal(left: LogicTerm, right: LogicTerm) -> LogicTerm:
    return apply("eq", left, right)


def _all_equal(
    left: dict[str, LogicTerm],
    right: dict[str, LogicTerm],
) -> LogicTerm:
    return conjunction(_equal(left[name], right[name]) for name in left)


@dataclass(frozen=True, slots=True)
class OTMarkovLogicQuery:
    model: OTMarkovModel
    initialization: LogicSequent
    shield_totality: LogicSequent
    shield_uniqueness: LogicSequent
    markov: LogicSequent

    def sequents(self) -> dict[str, LogicSequent]:
        return {
            "initialization": self.initialization,
            "shield_totality": self.shield_totality,
            "shield_uniqueness": self.shield_uniqueness,
            "markov": self.markov,
        }

    def run(self, *, timeout_ms: int = 5_000) -> dict[str, object]:
        """Refute each model-specific logical counterexample with Z3."""

        z3 = _z3()
        formulas = {
            name: lower_to_z3(sequent.counterexample(), z3)
            for name, sequent in self.sequents().items()
        }
        sizes: dict[str, int] = {}
        fingerprints = {
            name: sequent.fingerprint()
            for name, sequent in self.sequents().items()
        }
        for name, formula in formulas.items():
            solver = z3.Solver()
            solver.add(formula)
            sizes[name] = len(solver.to_smt2().encode("utf-8"))
        if max(sizes.values(), default=0) > MAX_SMT2_BYTES:
            return {
                "classification": "NO_RESULT",
                "reason": "formula_budget",
                "logic_profile": LOGIC_PROFILE,
                "logic_fingerprints": fingerprints,
                "smt2_bytes": sizes,
            }
        results: dict[str, str] = {}
        for name, formula in formulas.items():
            solver = z3.Solver()
            solver.set(timeout=timeout_ms)
            solver.add(formula)
            results[name] = str(solver.check())
        if any(value != "unsat" for value in results.values()):
            return {
                "classification": "NO_RESULT",
                "reason": "obligation_not_discharged",
                "logic_profile": LOGIC_PROFILE,
                "logic_fingerprints": fingerprints,
                "obligations": results,
                "smt2_bytes": sizes,
            }
        return {
            "classification": "CERTIFIED_UNDER_PROFILE",
            "profile": MARKOV_LOGIC_PROFILE,
            "logic_profile": LOGIC_PROFILE,
            "source_sha256": self.model.source_sha256,
            "certified_process": (
                "controller observation, completion, and successor buffer"
            ),
            "implementation_condition": (
                "any downstream reward must be a deterministic function of the "
                "current buffer, proposal or executed action, completion, and "
                "successor observation"
            ),
            "candidate": {
                "b_obs": self.model.candidate.b_obs,
                "b_act": self.model.candidate.b_act,
                "evidence": tuple({
                    "component": item.component,
                    "source": item.source,
                    "lag": item.lag,
                    "equation": item.equation,
                } for item in self.model.candidate.evidence),
            },
            "logic_fingerprints": fingerprints,
            "obligations": results,
            "smt2_bytes": sizes,
        }


def build_markov_logic_query(model: OTMarkovModel) -> OTMarkovLogicQuery:
    """Compile one paired buffered-process query into explicit logic."""

    shielded = model.action_execution.mode == "keep_or_replace"
    _parameters, types = _parameter_maps(model.parser)
    scenario = {
        key: symbol(
            "fixed::" + key.replace("::", ":"),
            sort_from_sysml(types.get(key, "Real")),
        )
        for key in model.scenario_parameters
    }
    domain = compile_scenario_domain(model, scenario)
    left_obs = {
        field.name: symbol(
            "left::obs::" + field.name, sort_from_sysml(field.type_name)
        )
        for field in model.observation
    }
    right_obs = {
        field.name: symbol(
            "right::obs::" + field.name, sort_from_sysml(field.type_name)
        )
        for field in model.observation
    }
    proposal = {
        name: symbol("proposal::" + name, LogicSort.BOOL)
        for name in model.action_names
    }
    safe_left = (
        {name: symbol("left::safe::" + name, LogicSort.BOOL)
         for name in model.action_names}
        if shielded else {}
    )
    safe_right = (
        {name: symbol("right::safe::" + name, LogicSort.BOOL)
         for name in model.action_names}
        if shielded else {}
    )
    prior_left = (
        {name: symbol("left::prior::" + name, LogicSort.BOOL)
         for name in model.action_names}
        if model.candidate.b_act else {}
    )
    prior_right = (
        {name: symbol("right::prior::" + name, LogicSort.BOOL)
         for name in model.action_names}
        if model.candidate.b_act else {}
    )

    def valid(
        observation: dict[str, LogicTerm],
        action: dict[str, LogicTerm],
    ) -> LogicTerm:
        if model.policy_requirement is None:
            return literal(True)
        return compile_expression(
            model, model.policy_requirement, observation=observation,
            action=action, prior_action={}, scenario=scenario,
            context=model.controller_fqn,
        )

    valid_proposal_left = valid(left_obs, proposal)
    valid_proposal_right = valid(right_obs, proposal)
    valid_safe_left = valid(left_obs, safe_left) if shielded else literal(True)
    valid_safe_right = valid(right_obs, safe_right) if shielded else literal(True)
    executed_left = (
        {name: apply("ite", valid_proposal_left, proposal[name], safe_left[name])
         for name in model.action_names}
        if shielded else dict(proposal)
    )
    executed_right = (
        {name: apply("ite", valid_proposal_right, proposal[name], safe_right[name])
         for name in model.action_names}
        if shielded else dict(proposal)
    )

    fixed_left = conjunction(
        _equal(left_obs[field.name], scenario[field.fixed_scenario_source])
        for field in model.observation if field.fixed_scenario_source
    )
    fixed_right = conjunction(
        _equal(right_obs[field.name], scenario[field.fixed_scenario_source])
        for field in model.observation if field.fixed_scenario_source
    )
    completion_left = compile_expression(
        model, model.completion, observation=left_obs, action={},
        prior_action=prior_left, scenario=scenario, context=model.controller_fqn,
    )
    completion_right = compile_expression(
        model, model.completion, observation=right_obs, action={},
        prior_action=prior_right, scenario=scenario, context=model.controller_fqn,
    )

    left_arguments = (
        *left_obs.values(), *scenario.values(), *executed_left.values()
    )
    right_arguments = (
        *right_obs.values(), *scenario.values(), *executed_right.values()
    )
    next_left = {
        field.name: function(
            "source_next::" + field.name,
            sort_from_sysml(field.type_name),
            *left_arguments,
        )
        for field in model.observation
    }
    next_right = {
        field.name: function(
            "source_next::" + field.name,
            sort_from_sysml(field.type_name),
            *right_arguments,
        )
        for field in model.observation
    }
    completion_equal = _equal(completion_left, completion_right)
    successor_equal = _all_equal(next_left, next_right)
    if model.candidate.b_act:
        successor_equal = apply(
            "and", successor_equal, _all_equal(executed_left, executed_right)
        )
    result_equal = apply(
        "and",
        completion_equal,
        apply("or", completion_left, successor_equal),
    )
    markov = LogicSequent(
        (
            domain,
            fixed_left,
            fixed_right,
            _all_equal(left_obs, right_obs),
            _all_equal(prior_left, prior_right),
            valid_safe_left,
            valid_safe_right,
        ),
        result_equal,
    )

    initialization_conclusion = (
        apply("exists", *scenario.values(), domain)
        if scenario else domain
    )
    initialization = LogicSequent((), initialization_conclusion)
    if shielded:
        total_action = {
            name: symbol("total::" + name, LogicSort.BOOL)
            for name in model.action_names
        }
        shield_totality = LogicSequent(
            (domain,),
            apply("exists", *total_action.values(), valid(left_obs, total_action)),
        )
        unique_a = {
            name: symbol("unique_a::" + name, LogicSort.BOOL)
            for name in model.action_names
        }
        unique_b = {
            name: symbol("unique_b::" + name, LogicSort.BOOL)
            for name in model.action_names
        }
        shield_uniqueness = LogicSequent(
            (domain, valid(left_obs, unique_a), valid(left_obs, unique_b)),
            _all_equal(unique_a, unique_b),
        )
    else:
        shield_totality = LogicSequent((), literal(True))
        shield_uniqueness = LogicSequent((), literal(True))
    return OTMarkovLogicQuery(
        model=model,
        initialization=initialization,
        shield_totality=shield_totality,
        shield_uniqueness=shield_uniqueness,
        markov=markov,
    )


__all__ = [
    "MARKOV_LOGIC_PROFILE",
    "OTMarkovLogicQuery",
    "build_markov_logic_query",
]
