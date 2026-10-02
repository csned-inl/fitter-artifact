"""One-way structural Markov fast path for the narrow OT SysML profile.

This module is deliberately incomplete.  It returns a certificate only when
the compiled source has an explicitly total functional action relation and the
existing reconstruction witness proves that the successor buffer factors
through the current buffer, executed action, and shared fixed context.  Every
unrecognized case returns ``None`` and must use the ordinary semantic prover.

No simulator or SMT solver is imported or executed here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from clarity.sysml.parser import (
    BinaryExpr,
    Expr,
    LiteralExpr,
    RefExpr,
    TernaryExpr,
    UnaryExpr,
)

from .ot_markov import (
    OTMarkovModel,
    UnsupportedOTProfile,
    _conjuncts,
    _parameter_key,
    _parameter_maps,
    _refs,
    compile_ot_model,
    prove_markov,
)


STRUCTURAL_PROFILE = "ot-markov-structural-fast-path-0.1"
MAX_DNF_CLAUSES = 32
MAX_CLAUSE_ATOMS = 16


@dataclass(frozen=True, slots=True)
class StructuralMarkovCertificate:
    """Checkable evidence that the restricted factorization theorem applies."""

    profile: str
    source_sha256: str
    package_name: str
    theorem: str
    buffer: dict[str, int]
    observations: tuple[str, ...]
    actions: tuple[str, ...]
    action_definitions: tuple[str, ...]
    fixed_context: tuple[str, ...]
    reconstruction_evidence: tuple[tuple[str, str, int, str], ...]
    dependency_witness: tuple[tuple[str, tuple[str, ...]], ...]
    discharged_obligations: tuple[str, ...]

    def as_result(self) -> dict[str, object]:
        return {
            "classification": "CERTIFIED_STRUCTURALLY",
            "profile": self.profile,
            "source_sha256": self.source_sha256,
            "certified_process": (
                "controller observation, completion, and successor buffer"
            ),
            "implementation_condition": (
                "any downstream reward must be a deterministic function of the current "
                "buffer, proposal or executed action, completion, and successor observation"
            ),
            "certificate": asdict(self),
        }


def _output_name(model: OTMarkovModel, expr: Expr) -> str | None:
    if not isinstance(expr, RefExpr) or len(expr.path) != 2:
        return None
    if expr.path[0] != model.policy_subject or expr.path[1] not in model.action_names:
        return None
    return expr.path[1]


def _extract_total_definitions(model: OTMarkovModel) -> dict[str, Expr] | None:
    definitions: dict[str, Expr] = {}
    aliases: list[tuple[str, str]] = []
    for term in _conjuncts(model.policy_requirement):
        if not isinstance(term, BinaryExpr) or term.op != "==":
            continue
        left = _output_name(model, term.left)
        right = _output_name(model, term.right)
        if left and right:
            aliases.append((left, right))
            continue
        if not left and not right:
            continue
        output, expression = (left, term.right) if left else (right, term.left)
        assert output is not None
        previous = definitions.get(output)
        if previous is not None and previous != expression:
            return None
        definitions[output] = expression

    changed = True
    while changed:
        changed = False
        for left, right in aliases:
            if left in definitions and right not in definitions:
                definitions[right] = definitions[left]
                changed = True
            elif right in definitions and left not in definitions:
                definitions[left] = definitions[right]
                changed = True
            elif left in definitions and right in definitions:
                if definitions[left] != definitions[right]:
                    return None
    if set(definitions) != set(model.action_names):
        return None
    return definitions


def _replace_actions(model: OTMarkovModel, expr: Expr, definitions: dict[str, Expr]) -> Expr:
    if isinstance(expr, RefExpr):
        output = _output_name(model, expr)
        return definitions.get(output, expr) if output else expr
    if isinstance(expr, BinaryExpr):
        return BinaryExpr(
            expr.op,
            _replace_actions(model, expr.left, definitions),
            _replace_actions(model, expr.right, definitions),
        )
    if isinstance(expr, UnaryExpr):
        return UnaryExpr(expr.op, _replace_actions(model, expr.operand, definitions))
    if isinstance(expr, TernaryExpr):
        return TernaryExpr(
            _replace_actions(model, expr.condition, definitions),
            _replace_actions(model, expr.true_expr, definitions),
            _replace_actions(model, expr.false_expr, definitions),
        )
    return expr


def _fixed_value(
    model: OTMarkovModel,
    path: tuple[str, ...],
    context: str,
    *,
    allow_scenario_value: bool = False,
) -> Any | None:
    key = _parameter_key(model, path, context)
    if key is None:
        return None
    if key in model.scenario_parameters and not allow_scenario_value:
        # Scenario inputs are shared between paired histories, but they range
        # over their declared domain.  A default value cannot justify a policy
        # tautology for every permitted scenario.
        return None
    values, _types = _parameter_maps(model.parser)
    item = values.get(key)
    return getattr(item, "value", None)


Affine = tuple[dict[str, Fraction], Fraction]


def _affine(
    model: OTMarkovModel,
    expr: Expr,
    context: str,
    *,
    allow_scenario_values: bool = False,
) -> Affine | None:
    if isinstance(expr, LiteralExpr):
        if isinstance(expr.value, bool):
            return None
        return {}, Fraction(str(expr.value))
    if isinstance(expr, RefExpr):
        if (
            len(expr.path) == 2
            and expr.path[0] == model.policy_subject
            and expr.path[1] in {field.name for field in model.observation}
        ):
            return {"observation::" + expr.path[1]: Fraction(1)}, Fraction(0)
        value = _fixed_value(
            model,
            tuple(expr.path),
            context,
            allow_scenario_value=allow_scenario_values,
        )
        if value is None or isinstance(value, bool):
            return None
        return {}, Fraction(str(value))
    if isinstance(expr, UnaryExpr) and expr.op == "-":
        inner = _affine(
            model,
            expr.operand,
            context,
            allow_scenario_values=allow_scenario_values,
        )
        if inner is None:
            return None
        coefficients, constant = inner
        return {key: -value for key, value in coefficients.items()}, -constant
    if not isinstance(expr, BinaryExpr):
        return None
    left = _affine(
        model,
        expr.left,
        context,
        allow_scenario_values=allow_scenario_values,
    )
    right = _affine(
        model,
        expr.right,
        context,
        allow_scenario_values=allow_scenario_values,
    )
    if left is None or right is None:
        return None
    left_coefficients, left_constant = left
    right_coefficients, right_constant = right
    if expr.op in {"+", "-"}:
        sign = Fraction(1) if expr.op == "+" else Fraction(-1)
        coefficients = dict(left_coefficients)
        for key, value in right_coefficients.items():
            coefficients[key] = coefficients.get(key, Fraction(0)) + sign * value
            if coefficients[key] == 0:
                del coefficients[key]
        return coefficients, left_constant + sign * right_constant
    if expr.op == "*":
        if not left_coefficients:
            scale = left_constant
            return (
                {key: scale * value for key, value in right_coefficients.items()},
                scale * right_constant,
            )
        if not right_coefficients:
            scale = right_constant
            return (
                {key: scale * value for key, value in left_coefficients.items()},
                scale * left_constant,
            )
        return None
    if expr.op == "/" and not right_coefficients and right_constant != 0:
        return (
            {key: value / right_constant for key, value in left_coefficients.items()},
            left_constant / right_constant,
        )
    return None


_FLIPPED = {">": "<", ">=": "<=", "<": ">", "<=": ">=", "==": "=="}
_NEGATED = {">": "<=", ">=": "<", "<": ">=", "<=": ">", "==": "!="}


Bound = tuple[tuple[tuple[str, Fraction], ...], str, Fraction] | bool


def _bound(
    model: OTMarkovModel,
    predicate: Expr,
    context: str,
    *,
    negate: bool = False,
    allow_scenario_values: bool = False,
) -> Bound | None:
    if not isinstance(predicate, BinaryExpr) or predicate.op not in _NEGATED:
        return None
    operation = _NEGATED[predicate.op] if negate else predicate.op
    if operation == "!=":
        return None
    left = _affine(
        model,
        predicate.left,
        context,
        allow_scenario_values=allow_scenario_values,
    )
    right = _affine(
        model,
        predicate.right,
        context,
        allow_scenario_values=allow_scenario_values,
    )
    if left is None or right is None:
        return None
    coefficients = dict(left[0])
    for key, value in right[0].items():
        coefficients[key] = coefficients.get(key, Fraction(0)) - value
        if coefficients[key] == 0:
            del coefficients[key]
    constant = left[1] - right[1]
    if not coefficients:
        values = {
            ">": constant > 0,
            ">=": constant >= 0,
            "<": constant < 0,
            "<=": constant <= 0,
            "==": constant == 0,
        }
        return values[operation]

    first_key = sorted(coefficients)[0]
    factor = Fraction(1, 1) / coefficients[first_key]
    normalized = tuple(sorted(
        (key, value * factor) for key, value in coefficients.items()
    ))
    if factor < 0:
        operation = _FLIPPED[operation]
    threshold = -constant * factor
    return normalized, operation, threshold


Clause = tuple[tuple[Expr, bool], ...]


def _dnf(
    expr: Expr, *, negate: bool = False, clauses_limit: int = MAX_DNF_CLAUSES
) -> tuple[Clause, ...] | None:
    if isinstance(expr, LiteralExpr) and isinstance(expr.value, bool):
        value = not expr.value if negate else expr.value
        return ((),) if value else ()
    if isinstance(expr, UnaryExpr) and expr.op == "not":
        return _dnf(expr.operand, negate=not negate, clauses_limit=clauses_limit)
    if isinstance(expr, BinaryExpr) and expr.op in {">", ">=", "<", "<=", "=="}:
        return (((expr, negate),),)
    if not isinstance(expr, BinaryExpr) or expr.op not in {"and", "or", "implies"}:
        return None

    if expr.op == "implies":
        left = _dnf(expr.left, negate=not negate, clauses_limit=clauses_limit)
        right = _dnf(expr.right, negate=negate, clauses_limit=clauses_limit)
        operation = "or" if not negate else "and"
    else:
        operation = expr.op
        if negate:
            operation = "or" if operation == "and" else "and"
        left = _dnf(expr.left, negate=negate, clauses_limit=clauses_limit)
        right = _dnf(expr.right, negate=negate, clauses_limit=clauses_limit)
    if left is None or right is None:
        return None
    if operation == "or":
        result = left + right
        return result if len(result) <= clauses_limit else None
    result: list[Clause] = []
    for left_clause in left:
        for right_clause in right:
            combined = left_clause + right_clause
            if len(combined) > MAX_CLAUSE_ATOMS:
                return None
            result.append(combined)
            if len(result) > clauses_limit:
                return None
    return tuple(result)


def _clause_contradiction(model: OTMarkovModel, clause: Clause, context: str) -> bool:
    lower: dict[tuple[tuple[str, Fraction], ...], tuple[Fraction, bool]] = {}
    upper: dict[tuple[tuple[str, Fraction], ...], tuple[Fraction, bool]] = {}
    for predicate, negate in clause:
        bound = _bound(model, predicate, context, negate=negate)
        if bound is None:
            return False
        if bound is False:
            return True
        if bound is True:
            continue
        key, operation, threshold = bound
        if operation in {">", ">=", "=="}:
            candidate = (threshold, operation == ">")
            previous = lower.get(key)
            if previous is None or candidate[0] > previous[0]:
                lower[key] = candidate
            elif candidate[0] == previous[0]:
                lower[key] = (candidate[0], candidate[1] or previous[1])
        if operation in {"<", "<=", "=="}:
            candidate = (threshold, operation == "<")
            previous = upper.get(key)
            if previous is None or candidate[0] < previous[0]:
                upper[key] = candidate
            elif candidate[0] == previous[0]:
                upper[key] = (candidate[0], candidate[1] or previous[1])
    for key in set(lower) & set(upper):
        low, low_strict = lower[key]
        high, high_strict = upper[key]
        if low > high or (low == high and (low_strict or high_strict)):
            return True
    return False


def _prove_true(model: OTMarkovModel, expr: Expr, context: str) -> bool:
    if isinstance(expr, LiteralExpr) and expr.value is True:
        return True
    if (
        isinstance(expr, BinaryExpr)
        and expr.op == "=="
        and expr.left == expr.right
        and _dnf(expr.left) is not None
    ):
        return True
    if isinstance(expr, BinaryExpr) and expr.op == "and":
        return _prove_true(model, expr.left, context) and _prove_true(
            model, expr.right, context
        )
    if isinstance(expr, BinaryExpr) and expr.op == "or":
        return _prove_true(model, expr.left, context) or _prove_true(
            model, expr.right, context
        )
    if isinstance(expr, UnaryExpr) and expr.op == "not":
        clauses = _dnf(expr.operand)
        return clauses is not None and all(
            _clause_contradiction(model, clause, context) for clause in clauses
        )
    bound = _bound(model, expr, context)
    return bound is True


def _policy_is_total_function(
    model: OTMarkovModel,
) -> tuple[dict[str, Expr], tuple[str, ...]] | None:
    definitions = _extract_total_definitions(model)
    if definitions is None:
        return None
    for expression in definitions.values():
        if any(_output_name(model, RefExpr(list(path))) for path in _refs(expression)):
            return None
        if _dnf(expression) is None:
            return None
    checked: list[str] = []
    for index, term in enumerate(_conjuncts(model.policy_requirement)):
        reduced = _replace_actions(model, term, definitions)
        if not _prove_true(model, reduced, model.controller_fqn):
            return None
        checked.append(f"policy conjunct {index} is tautological after action substitution")
    return definitions, tuple(checked)


def _constant_truth(model: OTMarkovModel, expr: Expr, context: str) -> bool | None:
    if isinstance(expr, LiteralExpr) and isinstance(expr.value, bool):
        return expr.value
    if isinstance(expr, UnaryExpr) and expr.op == "not":
        value = _constant_truth(model, expr.operand, context)
        return None if value is None else not value
    if isinstance(expr, BinaryExpr) and expr.op in {"and", "or", "implies"}:
        left = _constant_truth(model, expr.left, context)
        right = _constant_truth(model, expr.right, context)
        if left is None or right is None:
            return None
        if expr.op == "and":
            return left and right
        if expr.op == "or":
            return left or right
        return (not left) or right
    bound = _bound(model, expr, context, allow_scenario_values=True)
    return bound if isinstance(bound, bool) else None


def _declared_scenario_is_witness(model: OTMarkovModel) -> bool:
    scenario = set(model.scenario_parameters)
    values, _types = _parameter_maps(model.parser)
    for key in scenario:
        if key not in values or getattr(values[key], "value", None) is None:
            return False
    for constraint in model.parser.parsed_constraints:
        if "ScenarioConstraint" not in constraint.metadata:
            continue
        for conjunct in _conjuncts(constraint.expression):
            keys = {
                _parameter_key(model, path, constraint.context)
                for path in _refs(conjunct)
            }
            if None in keys or not keys.issubset(scenario):
                continue
            if _constant_truth(model, conjunct, constraint.context) is not True:
                return False
    return True


def _completion_uses_only_buffer_and_context(model: OTMarkovModel) -> bool:
    observations = dict(model.observation_paths)
    latches = dict(model.prior_action_latches)
    for path in _refs(model.completion):
        if path in observations:
            continue
        if len(path) == 1 and path[0] in latches and model.candidate.b_act >= 1:
            continue
        if _parameter_key(model, path, model.controller_fqn) is not None:
            continue
        return False
    return True


def _transition_factors_through_buffer(model: OTMarkovModel) -> bool:
    if model.candidate.b_obs != 0 or model.candidate.b_act not in {0, 1}:
        return False
    dynamic_sources = {
        source
        for field in model.observation
        for source in field.state_sources
    }
    witnessed_sources = {source for source, _dependencies in model.dependency_witness}
    if not dynamic_sources or not dynamic_sources.issubset(witnessed_sources):
        return False
    allowed_evidence = {"current_observation", "prior_action", "fixed_context"}
    return all(item.source in allowed_evidence for item in model.candidate.evidence)


def try_prove_markov_structurally(
    model_path: str | Path,
) -> StructuralMarkovCertificate | None:
    """Return a positive certificate or ``None``; never return a negative claim."""

    try:
        model = compile_ot_model(model_path)
    except (UnsupportedOTProfile, OSError, ValueError):
        return None
    if not _declared_scenario_is_witness(model):
        return None
    policy = _policy_is_total_function(model)
    if policy is None:
        return None
    definitions, policy_evidence = policy
    if not _completion_uses_only_buffer_and_context(model):
        return None
    if not _transition_factors_through_buffer(model):
        return None
    return StructuralMarkovCertificate(
        profile=STRUCTURAL_PROFILE,
        source_sha256=model.source_sha256,
        package_name=model.package_name,
        theorem=(
            "total functional action, completion, and successor-buffer relations "
            "factor through current buffer, executed action, and shared fixed context"
        ),
        buffer={"b_obs": model.candidate.b_obs, "b_act": model.candidate.b_act},
        observations=tuple(field.name for field in model.observation),
        actions=model.action_names,
        action_definitions=tuple(sorted(definitions)),
        fixed_context=model.scenario_parameters,
        reconstruction_evidence=tuple(
            (item.component, item.source, item.lag, item.equation)
            for item in model.candidate.evidence
        ),
        dependency_witness=model.dependency_witness,
        discharged_obligations=(
            "declared scenario values witness a nonempty fixed-context domain",
            *policy_evidence,
            "completion references only current observations, certified prior actions, "
            "and fixed parameters",
            "successor observation dependency closure contains no unresolved hidden state",
        ),
    )


def prove_markov_with_fallback(
    model_path: str | Path, *, timeout_ms: int = 5_000
) -> dict[str, object]:
    """Use the one-way fast path, otherwise preserve the existing Z3 behavior."""

    certificate = try_prove_markov_structurally(model_path)
    if certificate is not None:
        return certificate.as_result()
    return prove_markov(model_path, timeout_ms=timeout_ms)


__all__ = [
    "STRUCTURAL_PROFILE",
    "StructuralMarkovCertificate",
    "prove_markov_with_fallback",
    "try_prove_markov_structurally",
]
