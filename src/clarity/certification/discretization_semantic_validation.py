"""Independent SMT validation of positive structural safety obligations.

The structural checker remains the certificate producer.  This module lowers
each source-derived obligation separately and asks Z3 for a counterexample.  It
is a validation oracle for tests and CI, not evidence that an unknown/timeout is
safe and not a replacement for the checked source-to-obligation mapping.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .ot_markov import (
    UnsupportedOTProfile,
    _lower,
    _parameter_maps,
    _scenario_domain,
    _sort_value,
    _z3,
)
from .structural_discretization import (
    PROFILE,
    compile_structural_discretization_obligations,
)


@dataclass(frozen=True, slots=True)
class SemanticObligationResult:
    name: str
    result: str
    smt2_bytes: int
    reason_unknown: str | None


def validate_structural_discretization_semantically(
    model_path: str | Path,
    *,
    timeout_ms: int = 5_000,
) -> dict[str, object]:
    """Refute every compiled property counterexample with an independent solver."""

    try:
        model, mapped_domain, _records, obligations = (
            compile_structural_discretization_obligations(model_path)
        )
        z3 = _z3()
        _parameters, types = _parameter_maps(model.parser)
        scenario = {
            key: _sort_value(
                z3, types.get(key, "Real"), "validation::fixed::" + key.replace("::", ":")
            )
            for key in model.scenario_parameters
        }
        observation = {
            field.name: _sort_value(
                z3, field.type_name, "validation::obs::" + field.name
            )
            for field in model.observation
        }
        action = {
            name: z3.Bool("validation::action::" + name)
            for name in model.action_names
        }
        source_domain = _scenario_domain(model, scenario)
        mapped_domain_z3 = _lower(
            model,
            mapped_domain,
            observation=observation,
            action=action,
            prior_action={},
            scenario=scenario,
            context=model.controller_fqn,
        )
        fixed_observation = z3.And(*(
            observation[field.name] == scenario[field.fixed_scenario_source]
            for field in model.observation
            if field.fixed_scenario_source
        ))
        if model.policy_requirement is None:
            raise UnsupportedOTProfile(
                "MISSING_SAFETY_ACTION_RELATION",
                "semantic validation requires the checked shield relation",
            )
        valid_action = _lower(
            model,
            model.policy_requirement,
            observation=observation,
            action=action,
            prior_action={},
            scenario=scenario,
            context=model.controller_fqn,
        )

        results: list[SemanticObligationResult] = []
        for obligation in obligations:
            property_formula = _lower(
                model,
                obligation.expression,
                observation=observation,
                action=action,
                prior_action={},
                scenario=scenario,
                context=model.controller_fqn,
            )
            counterexample = z3.And(
                source_domain,
                mapped_domain_z3,
                fixed_observation,
                valid_action,
                z3.Not(property_formula),
            )
            solver = z3.Solver()
            solver.set(timeout=timeout_ms)
            solver.add(counterexample)
            smt2_bytes = len(solver.to_smt2().encode("utf-8"))
            outcome = solver.check()
            result = str(outcome)
            results.append(SemanticObligationResult(
                name=obligation.name,
                result=result,
                smt2_bytes=smt2_bytes,
                reason_unknown=(solver.reason_unknown() if result == "unknown" else None),
            ))
        passed = bool(results) and all(item.result == "unsat" for item in results)
        return {
            "classification": "VALIDATED" if passed else "NOT_VALIDATED",
            "profile": PROFILE,
            "source_sha256": model.source_sha256,
            "scope": (
                "semantic cross-check of source-derived boundary obligations; "
                "unknown and timeout never validate"
            ),
            "obligations": tuple(asdict(item) for item in results),
        }
    except UnsupportedOTProfile as exc:
        return {
            "classification": "NOT_VALIDATED",
            "profile": PROFILE,
            "reason": exc.code,
            "detail": exc.detail,
        }


__all__ = [
    "SemanticObligationResult",
    "validate_structural_discretization_semantically",
]
