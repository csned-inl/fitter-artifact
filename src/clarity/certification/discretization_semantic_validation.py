"""Independent logical and SMT validation of structural safety obligations.

The structural checker remains the certificate producer.  This module first
translates the accepted symbolic process into the explicit, solver-independent
constraint logic, then asks Z3 for a counterexample to each resulting sequent.
It is a validation oracle for tests and CI, not evidence that an unknown or
timeout is safe.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .constraint_logic import (
    LOGIC_PROFILE,
    LogicSequent,
    LogicSort,
    apply,
    compile_expression,
    compile_scenario_domain,
    conjunction,
    lower_to_z3,
    sort_from_sysml,
    symbol,
)
from .ot_markov import UnsupportedOTProfile, _parameter_maps, _z3
from .structural_discretization import (
    PROFILE,
    compile_structural_discretization_obligations,
)


@dataclass(frozen=True, slots=True)
class SemanticObligationResult:
    name: str
    result: str
    logic_fingerprint: str
    smt2_bytes: int
    reason_unknown: str | None


@dataclass(frozen=True, slots=True)
class NamedLogicObligation:
    """One source property interpreted as an explicit logical sequent."""

    name: str
    sequent: LogicSequent


def compile_structural_discretization_logic(
    model_path: str | Path,
) -> tuple[object, tuple[NamedLogicObligation, ...]]:
    """Translate accepted symbolic obligations into solver-independent logic."""

    model, mapped_domain, _records, obligations = (
        compile_structural_discretization_obligations(model_path)
    )
    _parameters, types = _parameter_maps(model.parser)
    scenario = {
        key: symbol(
            "validation::fixed::" + key.replace("::", ":"),
            sort_from_sysml(types.get(key, "Real")),
        )
        for key in model.scenario_parameters
    }
    observation = {
        field.name: symbol(
            "validation::obs::" + field.name,
            sort_from_sysml(field.type_name),
        )
        for field in model.observation
    }
    action = {
        name: symbol("validation::action::" + name, LogicSort.BOOL)
        for name in model.action_names
    }
    source_domain = compile_scenario_domain(model, scenario)
    mapped_domain_logic = compile_expression(
        model, mapped_domain, observation=observation, action=action,
        prior_action={}, scenario=scenario, context=model.controller_fqn,
    )
    fixed_observation = conjunction(
        apply("eq", observation[field.name], scenario[field.fixed_scenario_source])
        for field in model.observation
        if field.fixed_scenario_source
    )
    if model.policy_requirement is None:
        raise UnsupportedOTProfile(
            "MISSING_SAFETY_ACTION_RELATION",
            "semantic validation requires the checked shield relation",
        )
    valid_action = compile_expression(
        model, model.policy_requirement, observation=observation, action=action,
        prior_action={}, scenario=scenario, context=model.controller_fqn,
    )
    result: list[NamedLogicObligation] = []
    for obligation in obligations:
        property_formula = compile_expression(
            model, obligation.expression, observation=observation, action=action,
            prior_action={}, scenario=scenario, context=model.controller_fqn,
        )
        result.append(NamedLogicObligation(
            obligation.name,
            LogicSequent(
                (source_domain, mapped_domain_logic, fixed_observation, valid_action),
                property_formula,
            ),
        ))
    return model, tuple(result)


def validate_structural_discretization_semantically(
    model_path: str | Path,
    *,
    timeout_ms: int = 5_000,
) -> dict[str, object]:
    """Refute every compiled property counterexample with an independent solver."""

    try:
        model, obligations = compile_structural_discretization_logic(model_path)
        z3 = _z3()
        results: list[SemanticObligationResult] = []
        for obligation in obligations:
            solver = z3.Solver()
            solver.set(timeout=timeout_ms)
            solver.add(lower_to_z3(obligation.sequent.counterexample(), z3))
            smt2_bytes = len(solver.to_smt2().encode("utf-8"))
            outcome = solver.check()
            result = str(outcome)
            results.append(SemanticObligationResult(
                name=obligation.name,
                result=result,
                logic_fingerprint=obligation.sequent.fingerprint(),
                smt2_bytes=smt2_bytes,
                reason_unknown=(solver.reason_unknown() if result == "unknown" else None),
            ))
        passed = bool(results) and all(item.result == "unsat" for item in results)
        return {
            "classification": "VALIDATED" if passed else "NOT_VALIDATED",
            "profile": PROFILE,
            "logic_profile": LOGIC_PROFILE,
            "source_sha256": model.source_sha256,
            "scope": (
                "typed constraint-logic interpretation and semantic cross-check "
                "of source-derived boundary obligations; unknown and timeout "
                "never validate"
            ),
            "obligations": tuple(asdict(item) for item in results),
        }
    except UnsupportedOTProfile as exc:
        return {
            "classification": "NOT_VALIDATED",
            "profile": PROFILE,
            "logic_profile": LOGIC_PROFILE,
            "reason": exc.code,
            "detail": exc.detail,
        }


__all__ = [
    "NamedLogicObligation",
    "SemanticObligationResult",
    "compile_structural_discretization_logic",
    "validate_structural_discretization_semantically",
]
