"""One combined semantic validation of all structural proof methods."""

from __future__ import annotations

from .method_implementation_validation import validate_method_implementations
from .markov_rule_validation import markov_rule_schemas
from .structural_rule_validation import (
    structural_rule_schemas,
    validate_rule_schemas,
)


METHOD_VALIDATION_PROFILE = "symbolic-proof-method-validation-0.1"


def validate_symbolic_proof_methods(
    *, timeout_ms: int = 5_000,
) -> dict[str, object]:
    """Validate every logical rule schema once, independently of any model."""

    discretization = structural_rule_schemas()
    markov = markov_rule_schemas()
    rules = validate_rule_schemas(
        discretization + markov,
        profile=METHOD_VALIDATION_PROFILE,
        timeout_ms=timeout_ms,
    )
    implementation = validate_method_implementations(timeout_ms=timeout_ms)
    passed = (
        rules.get("classification") == "VALIDATED"
        and implementation.get("classification") == "VALIDATED"
    )
    return {
        **rules,
        "classification": "VALIDATED" if passed else "NOT_VALIDATED",
        "methods": {
            "discretization_safety": len(discretization),
            "buffered_markov": len(markov),
        },
        "implementation_validation": implementation,
        "execution_policy": (
            "run when the symbolic logic, translation, or positive proof rules "
            "change; ordinary SysML model certification does not invoke this check"
        ),
    }


__all__ = [
    "METHOD_VALIDATION_PROFILE",
    "validate_symbolic_proof_methods",
]
