"""Analytical controller-class selection directly from a SysML OT model.

This module is deliberately separate from the Markov prover.  It reads the
same direct-source profile, never imports or executes the simulator, and makes
only syntactically justified recommendations.  Unknown structure remains
unknown instead of triggering training experiments or semantic guesses.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from clarity.sysml.parser import (
    BinaryExpr,
    Expr,
    IfStmt,
    LiteralExpr,
    RefExpr,
    TernaryExpr,
    UnaryExpr,
)

from .ot_markov import OTMarkovModel, compile_ot_model


ANALYSIS_VERSION = "controller-class-analysis-0.1"


@dataclass(frozen=True, slots=True)
class OutputRule:
    output: str
    expression: str
    geometry: str
    atomic_predicates: int


@dataclass(frozen=True, slots=True)
class ControllerClassReport:
    analysis_version: str
    source_sha256: str
    package_name: str
    buffer: dict[str, int]
    memory_architecture: str
    observations: tuple[str, ...]
    action_outputs: tuple[str, ...]
    action_relation: str
    action_uniqueness: str
    action_availability: str
    output_rules: tuple[OutputRule, ...]
    decision_region_upper_bound: int | None
    plant_dynamics: str
    exact_synthesis: tuple[str, ...]
    learning_route: tuple[str, ...]
    proven_incompatible: tuple[str, ...]
    not_certified: tuple[str, ...]
    objective_status: str
    assumptions: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _refs(expr: Expr) -> tuple[tuple[str, ...], ...]:
    result: list[tuple[str, ...]] = []

    def visit(node: Expr) -> None:
        if isinstance(node, RefExpr):
            result.append(tuple(node.path))
        elif isinstance(node, BinaryExpr):
            visit(node.left); visit(node.right)
        elif isinstance(node, UnaryExpr):
            visit(node.operand)
        elif isinstance(node, TernaryExpr):
            visit(node.condition); visit(node.true_expr); visit(node.false_expr)

    visit(expr)
    return tuple(result)


def _conjuncts(expr: Expr) -> tuple[Expr, ...]:
    if isinstance(expr, BinaryExpr) and expr.op == "and":
        return _conjuncts(expr.left) + _conjuncts(expr.right)
    return (expr,)


def _output_name(model: OTMarkovModel, expr: Expr) -> str | None:
    if not isinstance(expr, RefExpr) or len(expr.path) != 2:
        return None
    if expr.path[0] != model.policy_subject or expr.path[1] not in model.action_names:
        return None
    return expr.path[1]


def _extract_definitions(model: OTMarkovModel) -> dict[str, Expr]:
    definitions: dict[str, Expr] = {}
    aliases: list[tuple[str, str]] = []
    if model.policy_requirement is None:
        return definitions
    for term in _conjuncts(model.policy_requirement):
        if not isinstance(term, BinaryExpr) or term.op != "==":
            continue
        left = _output_name(model, term.left)
        right = _output_name(model, term.right)
        if left and right:
            aliases.append((left, right))
        elif left:
            definitions.setdefault(left, term.right)
        elif right:
            definitions.setdefault(right, term.left)

    changed = True
    while changed:
        changed = False
        for left, right in aliases:
            if left in definitions and right not in definitions:
                definitions[right] = definitions[left]; changed = True
            elif right in definitions and left not in definitions:
                definitions[left] = definitions[right]; changed = True
    return definitions


def _is_definition_term(model: OTMarkovModel, term: Expr) -> bool:
    return (
        isinstance(term, BinaryExpr)
        and term.op == "=="
        and (
            _output_name(model, term.left) is not None
            or _output_name(model, term.right) is not None
        )
    )


def _degree(
    model: OTMarkovModel,
    expr: Expr,
    *,
    dynamic_policy_inputs: bool,
    source_context: tuple[str, ...] | None = None,
) -> int | None:
    """Polynomial degree, or None for non-polynomial/Boolean structure."""

    if isinstance(expr, LiteralExpr):
        return 0
    if isinstance(expr, RefExpr):
        if (dynamic_policy_inputs and len(expr.path) == 2
                and expr.path[0] == model.policy_subject
                and expr.path[1] in {item.name for item in model.observation}):
            return 1
        if not dynamic_policy_inputs:
            context = list(source_context or tuple(model.controller_fqn.split("::")))
            dependencies = model.dependency_model._collect_one(list(expr.path), context)
            return 1 if dependencies else 0
        return 0
    if isinstance(expr, UnaryExpr) and expr.op == "-":
        return _degree(
            model,
            expr.operand,
            dynamic_policy_inputs=dynamic_policy_inputs,
            source_context=source_context,
        )
    if isinstance(expr, BinaryExpr):
        left = _degree(
            model,
            expr.left,
            dynamic_policy_inputs=dynamic_policy_inputs,
            source_context=source_context,
        )
        right = _degree(
            model,
            expr.right,
            dynamic_policy_inputs=dynamic_policy_inputs,
            source_context=source_context,
        )
        if left is None or right is None:
            return None
        if expr.op in {"+", "-"}:
            return max(left, right)
        if expr.op == "*":
            return left + right
        if expr.op == "/":
            return left if right == 0 else None
    return None


def _is_affine_predicate(model: OTMarkovModel, expr: Expr) -> bool:
    if isinstance(expr, UnaryExpr) and expr.op == "not":
        return _is_affine_predicate(model, expr.operand)
    if isinstance(expr, BinaryExpr) and expr.op in {">", ">=", "<", "<=", "=="}:
        left = _degree(model, expr.left, dynamic_policy_inputs=True)
        right = _degree(model, expr.right, dynamic_policy_inputs=True)
        return left is not None and right is not None and max(left, right) <= 1
    return False


def _boolean_geometry(model: OTMarkovModel, expr: Expr) -> str:
    if _is_affine_predicate(model, expr):
        return "single_affine_threshold"
    if isinstance(expr, UnaryExpr) and expr.op == "not":
        return _boolean_geometry(model, expr.operand)
    if isinstance(expr, BinaryExpr) and expr.op in {"and", "or", "implies"}:
        left = _boolean_geometry(model, expr.left)
        right = _boolean_geometry(model, expr.right)
        if left in {"single_affine_threshold", "polyhedral_boolean"} and right in {
            "single_affine_threshold", "polyhedral_boolean"
        }:
            return "polyhedral_boolean"
    return "nonlinear_or_unsupported"


def _atoms(model: OTMarkovModel, expr: Expr) -> tuple[str, ...]:
    if _is_affine_predicate(model, expr):
        return (_format(expr),)
    if isinstance(expr, UnaryExpr):
        return _atoms(model, expr.operand)
    if isinstance(expr, BinaryExpr):
        return _atoms(model, expr.left) + _atoms(model, expr.right)
    if isinstance(expr, TernaryExpr):
        return _atoms(model, expr.condition) + _atoms(model, expr.true_expr) + _atoms(
            model, expr.false_expr
        )
    return ()


def _format(expr: Expr) -> str:
    if isinstance(expr, LiteralExpr):
        if isinstance(expr.value, bool):
            return str(expr.value).lower()
        return str(expr.value)
    if isinstance(expr, RefExpr):
        return ".".join(expr.path)
    if isinstance(expr, UnaryExpr):
        return f"({expr.op} {_format(expr.operand)})"
    if isinstance(expr, BinaryExpr):
        return f"({_format(expr.left)} {expr.op} {_format(expr.right)})"
    if isinstance(expr, TernaryExpr):
        return (
            f"({_format(expr.condition)} ? {_format(expr.true_expr)}"
            f" : {_format(expr.false_expr)})"
        )
    return repr(expr)


def _plant_dynamics(model: OTMarkovModel) -> str:
    maximum_degree = 0
    rational = False
    for step in model.parser.step_actions:
        degree = _degree(
            model,
            step.expression,
            dynamic_policy_inputs=False,
            source_context=tuple(step.context.split("::")),
        )
        if degree is None:
            rational = True
        else:
            maximum_degree = max(maximum_degree, degree)
    hybrid = bool(model.parser.state_machines) or any(
        isinstance(statement, IfStmt)
        for _fqn, body in model.parser.step_action_bodies
        for statement in _walk_statements(body)
    )
    base = (
        "rational_or_general_nonlinear" if rational
        else "affine" if maximum_degree <= 1
        else f"polynomial_degree_{maximum_degree}"
    )
    return "hybrid_" + base if hybrid else base


def _walk_statements(statements: Iterable[Any]) -> Iterable[Any]:
    for statement in statements:
        yield statement
        if isinstance(statement, IfStmt):
            yield from _walk_statements(statement.body)
            yield from _walk_statements(statement.else_body)


def analyze_controller_class(model_path: str | Path) -> ControllerClassReport:
    """Return the least-complex analytically justified controller route."""

    model = compile_ot_model(model_path)
    definitions = _extract_definitions(model)
    uniqueness = (
        "at_most_one_action_proved_by_source_definitional_equations"
        if set(definitions) == set(model.action_names)
        else "not_proved_syntactically"
    )
    availability = (
        "proved_by_total_source_definitions"
        if uniqueness.startswith("at_most_one")
        and model.policy_requirement is not None
        and all(_is_definition_term(model, term)
                for term in _conjuncts(model.policy_requirement))
        else "requires_markov_shield_totality_obligation"
    )
    rules: list[OutputRule] = []
    all_atoms: set[str] = set()
    for output in model.action_names:
        expression = definitions.get(output)
        if expression is None:
            rules.append(OutputRule(output, "unresolved", "unresolved", 0))
            continue
        atoms = set(_atoms(model, expression)); all_atoms.update(atoms)
        rules.append(OutputRule(
            output=output,
            expression=_format(expression),
            geometry=_boolean_geometry(model, expression),
            atomic_predicates=len(atoms),
        ))

    geometries = {rule.geometry for rule in rules}
    if geometries == {"single_affine_threshold"}:
        relation = "independent_or_aliased_affine_thresholds"
    elif geometries.issubset({"single_affine_threshold", "polyhedral_boolean"}):
        relation = "polyhedral_boolean_partition"
    elif "unresolved" in geometries:
        relation = "unresolved"
    else:
        relation = "nonlinear_or_unsupported"

    candidate = model.candidate
    if candidate.b_obs == 0 and candidate.b_act == 0:
        memory = "memoryless_feedforward"
    else:
        memory = (
            f"explicit_finite_buffer(obs_lag={candidate.b_obs},"
            f"action_lag={candidate.b_act})"
        )

    exact: list[str] = []
    learning: list[str] = []
    not_certified: list[str] = []
    exact_prefix = "source-derived" if availability.startswith("proved") else "candidate source-derived"
    if uniqueness.startswith("at_most_one") and relation == "independent_or_aliased_affine_thresholds":
        exact.extend((f"{exact_prefix} threshold rules", "small decision tree"))
        learning.append(
            "no learning required for requirement-satisfying behavior"
            if availability.startswith("proved")
            else "if the Markov shield-totality obligation passes, no learning is required"
        )
    elif uniqueness.startswith("at_most_one") and relation == "polyhedral_boolean_partition":
        exact.extend((f"{exact_prefix} Boolean rule system", "polyhedral decision tree"))
        learning.append("optional feedforward ReLU approximation only if deployment requires it")
        not_certified.append("one independent affine threshold per output")
    else:
        learning.append("an objective and a stronger relation analysis are required")
        not_certified.append("exact source-level controller synthesis")
    if not availability.startswith("proved"):
        not_certified.append(
            "total action availability until the Markov shield-totality obligation passes"
        )
    if candidate.b_obs or candidate.b_act:
        exact.append("feedforward policy over the analytically derived explicit buffer")
        not_certified.append("current-observation-only policy")
    else:
        exact.append("feedforward policy over the current observation")
    learning.append(
        "GRU is unnecessary if the derived finite buffer is certified and supplied"
    )

    proven_incompatible = (
        "unthresholded real-valued affine output for Boolean Policy outputs",
    ) if model.action_names else ()
    upper_bound = 2 ** len(all_atoms) if len(all_atoms) <= 20 else None
    return ControllerClassReport(
        analysis_version=ANALYSIS_VERSION,
        source_sha256=model.source_sha256,
        package_name=model.package_name,
        buffer={"b_obs": candidate.b_obs, "b_act": candidate.b_act},
        memory_architecture=memory,
        observations=tuple(item.name for item in model.observation),
        action_outputs=model.action_names,
        action_relation=relation,
        action_uniqueness=uniqueness,
        action_availability=availability,
        output_rules=tuple(rules),
        decision_region_upper_bound=upper_bound,
        plant_dynamics=_plant_dynamics(model),
        exact_synthesis=tuple(dict.fromkeys(exact)),
        learning_route=tuple(dict.fromkeys(learning)),
        proven_incompatible=proven_incompatible,
        not_certified=tuple(dict.fromkeys(not_certified)),
        objective_status=(
            "no source reward/objective: safety-rule representation is classified, "
            "but performance-optimal algorithm selection is not determined"
        ),
        assumptions=(
            "analysis uses only the supplied SysML source and the accepted OT profile",
            "action uniqueness means at most one action; it does not imply availability",
            "buffer sufficiency remains a separate Markov proof obligation",
            "recommendations concern exact representability, not optimizer convergence",
            "unknown structure is not treated as evidence that a controller class works",
        ),
    )


__all__ = [
    "ANALYSIS_VERSION",
    "ControllerClassReport",
    "OutputRule",
    "analyze_controller_class",
]
