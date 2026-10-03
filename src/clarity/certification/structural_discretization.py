"""Sound, incomplete structural proof of inter-decision safety.

This module proves only literal ``#Prohibition`` and ``#Obligation`` expressions
that can be reduced to the controller Policy interface by checked source copy
chains.  It does not execute a simulator, infer an ODE, or claim safety for an
unstated physical property.  Unsupported aliases, effects, or arithmetic return
``None`` so a later proof method may try the model without weakening the claim.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
from itertools import combinations
from pathlib import Path
import re
from typing import Iterable

from clarity.sysml.parser import (
    BinaryExpr,
    Expr,
    LiteralExpr,
    RefExpr,
    TernaryExpr,
    UnaryExpr,
)

from .dependencies import _refs as dependency_refs
from .ot_markov import OTMarkovModel, UnsupportedOTProfile, _conjuncts, compile_ot_model
from .structural_markov import (
    _bound,
    _clause_contradiction,
    _dnf,
    _policy_is_total_function,
    _prove_true,
    _replace_actions,
)


PROFILE = "direct-sysml-structural-discretization-0.1"


def _strip_sysml_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


def _validate_safety_inventory(model: OTMarkovModel) -> None:
    """Fail if a marked source requirement was not represented by the parser."""

    source = _strip_sysml_comments(Path(model.model_path).read_text())
    markers = re.findall(r"#(?:Prohibition|Obligation)\b", source)
    declarations = re.findall(
        r"#(?:Prohibition|Obligation)\s+requirement\s+def\b", source
    )
    parsed = [
        requirement for requirement in model.parser.parsed_requirements
        if set(requirement.metadata) & {"Prohibition", "Obligation"}
    ]
    if len(markers) != len(declarations) or len(declarations) != len(parsed):
        raise UnsupportedOTProfile(
            "INCOMPLETE_SAFETY_INVENTORY",
            "every marked source safety requirement must parse exactly once",
        )


@dataclass(frozen=True, slots=True)
class SourceMappingWitness:
    source_reference: str
    symbolic_reference: str
    rule: str
    checked_chain: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PropertyProof:
    name: str
    annotation: str
    rule: str
    source_expression: str
    stronger_symbolic_expression: str
    ignored_guards: tuple[str, ...]
    mappings: tuple[SourceMappingWitness, ...]
    held_symbols: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StructuralDiscretizationCertificate:
    profile: str
    source_sha256: str
    package_name: str
    theorem: str
    action_execution: str
    semantic_scope: tuple[str, ...]
    domain_assumptions: tuple[str, ...]
    properties: tuple[PropertyProof, ...]

    def as_result(self) -> dict[str, object]:
        return {
            "classification": "CERTIFIED_STRUCTURALLY",
            "profile": self.profile,
            "source_sha256": self.source_sha256,
            "certified_scope": (
                "literal parsed #Prohibition and #Obligation predicates after each "
                "completed Policy-containing action and throughout the following "
                "quiescent interval"
            ),
            "limitations": (
                "does not certify unstated physical properties, continuous ODE "
                "semantics, floating-point equivalence, or behavior outside the "
                "checked direct-SysML profile"
            ),
            "certificate": asdict(self),
        }


@dataclass(frozen=True, slots=True)
class _Mapped:
    expression: Expr
    witnesses: tuple[SourceMappingWitness, ...]
    held_symbols: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompiledStructuralObligation:
    """Internal source-derived formula shared with the independent SMT check."""

    name: str
    annotation: str
    expression: Expr
    ignored_guards: tuple[str, ...]
    witnesses: tuple[SourceMappingWitness, ...]
    held_symbols: tuple[str, ...]


def _and(items: Iterable[Expr]) -> Expr:
    values = tuple(items)
    if not values:
        return LiteralExpr(True)
    result = values[0]
    for value in values[1:]:
        result = BinaryExpr("and", result, value)
    return result


def _implies(left: Expr, right: Expr) -> Expr:
    return BinaryExpr("implies", left, right)


def _unit_farkas_contradiction(
    model: OTMarkovModel,
    clause: tuple[tuple[Expr, bool], ...],
    context: str,
) -> bool:
    """Recognize a tiny, exact Farkas certificate with unit multipliers.

    Each selected source inequality is added once.  If the variable
    coefficients cancel and the resulting constant inequality is impossible,
    the original clause is impossible.  This is sound but intentionally
    incomplete; it does not search arbitrary rational multipliers.
    """

    inequalities: list[tuple[dict[str, Fraction], Fraction, bool]] = []
    for predicate, negate in clause:
        bound = _bound(model, predicate, context, negate=negate)
        if bound is False:
            return True
        if bound is True:
            continue
        if bound is None:
            return False
        key, operation, threshold = bound
        coefficients = dict(key)

        def append(sign: int, *, strict: bool) -> None:
            inequalities.append((
                {name: Fraction(sign) * value
                 for name, value in coefficients.items()},
                Fraction(sign) * threshold,
                strict,
            ))

        if operation == "<=":
            append(1, strict=False)
        elif operation == "<":
            append(1, strict=True)
        elif operation == ">=":
            append(-1, strict=False)
        elif operation == ">":
            append(-1, strict=True)
        elif operation == "==":
            append(1, strict=False)
            append(-1, strict=False)
        else:  # pragma: no cover - guarded by _bound's contract
            return False

    # The profile budget prevents an accidental exponential search.  Larger
    # clauses simply fall through to the semantic checker.
    for width in range(2, min(4, len(inequalities)) + 1):
        for selected in combinations(inequalities, width):
            coefficients: dict[str, Fraction] = {}
            threshold = Fraction(0)
            strict = False
            for row, bound_value, row_strict in selected:
                threshold += bound_value
                strict = strict or row_strict
                for name, value in row.items():
                    coefficients[name] = coefficients.get(name, Fraction(0)) + value
                    if coefficients[name] == 0:
                        del coefficients[name]
            if not coefficients and (threshold < 0 or (strict and threshold == 0)):
                return True
    return False


def _prove_structural_tautology(
    model: OTMarkovModel,
    theorem: Expr,
    counterexample: Expr,
) -> bool:
    if _prove_true(model, theorem, model.controller_fqn):
        return True
    clauses = _dnf(counterexample)
    return clauses is not None and all(
        _clause_contradiction(model, clause, model.controller_fqn)
        or _unit_farkas_contradiction(model, clause, model.controller_fqn)
        for clause in clauses
    )


def _policy_ref(model: OTMarkovModel, name: str) -> RefExpr:
    if model.policy_subject is None:
        raise ValueError("a structural Policy reference requires a Policy subject")
    return RefExpr([model.policy_subject, name])


def _trace_single_copy(
    model: OTMarkovModel,
    start: str,
    targets: set[str],
    *,
    checked_only: bool = True,
) -> tuple[str, ...] | None:
    """Follow only explicit, unique copy edges; never choose among dependencies."""

    graph = model.dependency_model
    chain = [start]
    seen: set[str] = set()
    current = start
    accepted_copies = (
        graph.checked_copies
        if checked_only and hasattr(graph, "checked_copies")
        else graph.copies
    )
    while current not in seen:
        if current in targets:
            return tuple(chain)
        seen.add(current)
        if current not in accepted_copies:
            return None
        dependencies = graph.nsupp.get(current, set())
        if len(dependencies) != 1:
            return None
        current = next(iter(dependencies))
        chain.append(current)
    return None


class _SourceMapper:
    def __init__(self, model: OTMarkovModel):
        self.model = model
        self.graph = model.dependency_model
        self.controller = model.controller_fqn.split("::")[-1]
        self.observation_paths = dict(model.observation_paths)
        self.action_by_key = {
            key: key.removeprefix(
                f"{self.controller}_{self.graph.neural_call}_"
            )
            for key in self.graph.ACTIONS
        }
        self.observation_by_key: dict[str, str] = {}
        for key in self.graph.OBS:
            matches = [
                field.name for field in model.observation
                if key in field.state_sources
            ]
            if len(matches) == 1:
                self.observation_by_key[key] = matches[0]

    def _dynamic_key(
        self,
        parts: tuple[str, ...],
        context: str,
    ) -> str | None:
        dependencies = self.graph.dependency_keys(list(parts), context.split("::"))
        return next(iter(dependencies)) if len(dependencies) == 1 else None

    def _map_action_effect(self, expression: Expr) -> tuple[Expr, tuple[str, ...]] | None:
        if isinstance(expression, LiteralExpr):
            return expression, ()
        if isinstance(expression, RefExpr):
            if (
                len(expression.path) == 2
                and expression.path[0] == self.graph.neural_call
                and expression.path[1] in self.model.action_names
            ):
                name = expression.path[1]
                return _policy_ref(self.model, name), (name,)
            return None
        if isinstance(expression, UnaryExpr):
            operand = self._map_action_effect(expression.operand)
            return None if operand is None else (
                UnaryExpr(expression.op, operand[0]), operand[1]
            )
        if isinstance(expression, BinaryExpr):
            left = self._map_action_effect(expression.left)
            right = self._map_action_effect(expression.right)
            return None if left is None or right is None else (
                BinaryExpr(expression.op, left[0], right[0]),
                left[1] + right[1],
            )
        if isinstance(expression, TernaryExpr):
            condition = self._map_action_effect(expression.condition)
            true_value = self._map_action_effect(expression.true_expr)
            false_value = self._map_action_effect(expression.false_expr)
            return None if any(
                item is None for item in (condition, true_value, false_value)
            ) else (
                TernaryExpr(condition[0], true_value[0], false_value[0]),
                condition[1] + true_value[1] + false_value[1],
            )
        return None

    def _reference(
        self,
        ref: RefExpr,
        *,
        context: str,
        subject_var: str | None,
    ) -> _Mapped | None:
        original = tuple(ref.path)
        parts = original
        if subject_var and parts and parts[0] == subject_var:
            parts = parts[1:]
        if not parts:
            return None
        source = ".".join(original)

        # Direct Policy-input source inside the controller action.
        if parts[0] == self.controller:
            relative = tuple(parts[1:])
            observation = self.observation_paths.get(relative)
            if observation is not None:
                target = _policy_ref(self.model, observation)
                witness = SourceMappingWitness(
                    source, ".".join(target.path),
                    "controller_policy_input_binding",
                    ("controller::" + "::".join(relative),),
                )
                return _Mapped(target, (witness,), ("observation::" + observation,))

        dynamic = self._dynamic_key(parts, context)
        if dynamic is not None:
            effect = self.graph.action_effects.get(dynamic)
            if effect is not None:
                mapped_effect = self._map_action_effect(effect)
                if mapped_effect is None:
                    return None
                symbolic, action_names = mapped_effect
                witness = SourceMappingWitness(
                    source, repr(symbolic), "verified_actuator_command_effect",
                    (dynamic,) + tuple(
                        f"{self.controller}_{self.graph.neural_call}_{name}"
                        for name in sorted(set(action_names))
                    ),
                )
                return _Mapped(
                    symbolic, (witness,), tuple(
                        "action::" + name for name in sorted(set(action_names))
                    ),
                )
            action_chain = _trace_single_copy(
                self.model, dynamic, set(self.action_by_key)
            )
            if action_chain is not None:
                output = self.action_by_key[action_chain[-1]]
                target = _policy_ref(self.model, output)
                witness = SourceMappingWitness(
                    source, ".".join(target.path), "unique_action_copy_chain",
                    action_chain,
                )
                return _Mapped(target, (witness,), ("action::" + output,))

            observation_chain = _trace_single_copy(
                self.model, dynamic, set(self.observation_by_key)
            )
            if observation_chain is not None:
                observation = self.observation_by_key[observation_chain[-1]]
                target = _policy_ref(self.model, observation)
                witness = SourceMappingWitness(
                    source, ".".join(target.path), "unique_sample_copy_chain",
                    observation_chain,
                )
                return _Mapped(target, (witness,), ("observation::" + observation,))
            return None

        # A controller attribute with no dynamic dependency is a declared fixed
        # parameter.  Keep it relative to the controller so the exact structural
        # arithmetic checker can resolve its declared value or symbolic input.
        if parts[0] == self.controller and len(parts) >= 2:
            relative = tuple(parts[1:])
            observation = self.observation_paths.get(relative)
            if observation is not None:
                target = _policy_ref(self.model, observation)
                witness = SourceMappingWitness(
                    source, ".".join(target.path),
                    "fixed_context_policy_input_binding",
                    ("controller::" + "::".join(relative),),
                )
                return _Mapped(target, (witness,), ("observation::" + observation,))
            target = RefExpr(list(relative))
            witness = SourceMappingWitness(
                source, ".".join(target.path), "declared_fixed_controller_parameter",
                ("controller::" + "::".join(relative),),
            )
            return _Mapped(target, (witness,), ("fixed::" + "::".join(relative),))
        return None

    def expression(
        self,
        expr: Expr,
        *,
        context: str,
        subject_var: str | None,
    ) -> _Mapped | None:
        if isinstance(expr, LiteralExpr):
            return _Mapped(expr, (), ())
        if isinstance(expr, RefExpr):
            return self._reference(expr, context=context, subject_var=subject_var)
        if isinstance(expr, UnaryExpr):
            operand = self.expression(
                expr.operand, context=context, subject_var=subject_var
            )
            if operand is None:
                return None
            return _Mapped(
                UnaryExpr(expr.op, operand.expression),
                operand.witnesses,
                operand.held_symbols,
            )
        if isinstance(expr, BinaryExpr):
            left = self.expression(expr.left, context=context, subject_var=subject_var)
            right = self.expression(expr.right, context=context, subject_var=subject_var)
            if left is None or right is None:
                return None
            return _Mapped(
                BinaryExpr(expr.op, left.expression, right.expression),
                left.witnesses + right.witnesses,
                left.held_symbols + right.held_symbols,
            )
        if isinstance(expr, TernaryExpr):
            condition = self.expression(
                expr.condition, context=context, subject_var=subject_var
            )
            true_value = self.expression(
                expr.true_expr, context=context, subject_var=subject_var
            )
            false_value = self.expression(
                expr.false_expr, context=context, subject_var=subject_var
            )
            if condition is None or true_value is None or false_value is None:
                return None
            return _Mapped(
                TernaryExpr(
                    condition.expression,
                    true_value.expression,
                    false_value.expression,
                ),
                condition.witnesses + true_value.witnesses + false_value.witnesses,
                condition.held_symbols
                + true_value.held_symbols
                + false_value.held_symbols,
            )
        return None


def _mapped_domain(model: OTMarkovModel, mapper: _SourceMapper) -> tuple[Expr, tuple[str, ...]]:
    accepted: list[Expr] = []
    records: list[str] = []
    for constraint in model.parser.parsed_constraints:
        if "ScenarioConstraint" not in constraint.metadata:
            continue
        for conjunct in _conjuncts(constraint.expression):
            mapped = mapper.expression(
                conjunct, context=constraint.context, subject_var=None
            )
            if mapped is None:
                # Omitting an assumption weakens the premise and is sound.  It may
                # make the structural proof inconclusive, never falsely positive.
                continue
            accepted.append(mapped.expression)
            records.append(repr(mapped.expression))
    return _and(accepted), tuple(records)


def _prove_formula(
    model: OTMarkovModel,
    mapper: _SourceMapper,
    definitions: dict[str, Expr],
    domain: Expr,
    expression: Expr,
    *,
    context: str,
    subject_var: str | None,
) -> CompiledStructuralObligation | None:
    mapped = mapper.expression(
        expression, context=context, subject_var=subject_var
    )
    if mapped is not None:
        # Prove the implication by refuting its counterexample.  The bounded
        # structural engine deliberately recognizes contradiction-normal form;
        # it does not contain a second, partially overlapping implication
        # prover.  This rewrite is an exact Boolean equivalence:
        #
        #     D -> P  ==  not (D and not P)
        counterexample = BinaryExpr(
            "and", domain, UnaryExpr("not", mapped.expression)
        )
        reduced_counterexample = _replace_actions(
            model, counterexample, definitions
        )
        theorem = UnaryExpr("not", reduced_counterexample)
        if _prove_structural_tautology(
            model, theorem, reduced_counterexample
        ):
            return CompiledStructuralObligation(
                name="", annotation="", expression=mapped.expression,
                ignored_guards=(), witnesses=mapped.witnesses,
                held_symbols=tuple(sorted(set(mapped.held_symbols))),
            )

    # A true consequent makes an implication true even when its time/event guard
    # changes.  This is the only accepted way to ignore an unmapped guard.
    if isinstance(expression, BinaryExpr) and expression.op == "implies":
        consequent = _prove_formula(
            model, mapper, definitions, domain, expression.right,
            context=context, subject_var=subject_var,
        )
        if consequent is not None:
            return CompiledStructuralObligation(
                name="", annotation="", expression=consequent.expression,
                ignored_guards=(repr(expression.left),) + consequent.ignored_guards,
                witnesses=consequent.witnesses,
                held_symbols=consequent.held_symbols,
            )

    if isinstance(expression, BinaryExpr) and expression.op == "and":
        left = _prove_formula(
            model, mapper, definitions, domain, expression.left,
            context=context, subject_var=subject_var,
        )
        right = _prove_formula(
            model, mapper, definitions, domain, expression.right,
            context=context, subject_var=subject_var,
        )
        if left is not None and right is not None:
            return CompiledStructuralObligation(
                name="", annotation="",
                expression=BinaryExpr("and", left.expression, right.expression),
                ignored_guards=left.ignored_guards + right.ignored_guards,
                witnesses=left.witnesses + right.witnesses,
                held_symbols=tuple(sorted(set(
                    left.held_symbols + right.held_symbols
                ))),
            )
    return None


def compile_structural_discretization_obligations(
    model_path: str | Path,
) -> tuple[OTMarkovModel, Expr, tuple[str, ...], tuple[CompiledStructuralObligation, ...]]:
    """Compile only obligations discharged by the structural proof rules."""

    model = compile_ot_model(model_path)
    _validate_safety_inventory(model)
    policy = _policy_is_total_function(model)
    if policy is None:
        raise UnsupportedOTProfile(
            "UNSUPPORTED_SAFETY_ACTION_RELATION",
            "structural safety requires a checked total functional shield relation",
        )
    definitions, _policy_evidence = policy
    mapper = _SourceMapper(model)
    domain, domain_records = _mapped_domain(model, mapper)
    requirements = [
        requirement for requirement in model.parser.parsed_requirements
        if set(requirement.metadata) & {"Prohibition", "Obligation"}
    ]
    if not requirements:
        raise UnsupportedOTProfile(
            "EMPTY_SAFETY_INVENTORY", "no #Prohibition or #Obligation was parsed"
        )
    obligations: list[CompiledStructuralObligation] = []
    for requirement in requirements:
        proof = _prove_formula(
            model, mapper, definitions, domain, requirement.expression,
            context=requirement.context, subject_var=requirement.subject_var,
        )
        if proof is None:
            raise UnsupportedOTProfile(
                "STRUCTURAL_SAFETY_INCONCLUSIVE",
                f"no structural proof for {requirement.name}",
            )
        obligations.append(CompiledStructuralObligation(
            name=requirement.name,
            annotation=next((item for item in requirement.metadata
                             if item in {"Prohibition", "Obligation"}), ""),
            expression=proof.expression,
            ignored_guards=proof.ignored_guards,
            witnesses=proof.witnesses,
            held_symbols=proof.held_symbols,
        ))
    return model, domain, domain_records, tuple(obligations)


def try_prove_discretization_structurally(
    model_path: str | Path,
) -> StructuralDiscretizationCertificate | None:
    """Return a positive source certificate or ``None``; never return a negative claim."""

    try:
        model, _domain, domain_records, obligations = (
            compile_structural_discretization_obligations(model_path)
        )
    except (UnsupportedOTProfile, OSError, ValueError):
        return None
    properties = tuple(PropertyProof(
        name=item.name,
        annotation=item.annotation,
        rule=(
            "guard_consequent_plus_boundary_substitution_plus_interval_frame"
            if item.ignored_guards
            else "boundary_substitution_plus_interval_frame"
        ),
        source_expression=next(
            requirement.raw_text for requirement in model.parser.parsed_requirements
            if requirement.name == item.name
        ),
        stronger_symbolic_expression=repr(item.expression),
        ignored_guards=item.ignored_guards,
        mappings=item.witnesses,
        held_symbols=item.held_symbols,
    ) for item in obligations)
    return StructuralDiscretizationCertificate(
        profile=PROFILE,
        source_sha256=model.source_sha256,
        package_name=model.package_name,
        theorem=(
            "the checked shield establishes every literal source safety predicate "
            "at completion of the Policy-containing action, and every dependency "
            "retained by the proof is held until the next such action begins"
        ),
        action_execution=model.action_execution.mode,
        semantic_scope=(
            "exact source arithmetic",
            "synchronous source copy/send/accept chains accepted by the OT profile",
            "one Policy-containing source action is an atomic logical boundary",
            "certified intervals exclude implementation-visible internal microsteps",
            "only unique copy chains may identify source values",
        ),
        domain_assumptions=domain_records,
        properties=properties,
    )


def prove_discretization_structurally(model_path: str | Path) -> dict[str, object]:
    certificate = try_prove_discretization_structurally(model_path)
    if certificate is None:
        return {
            "classification": "INCONCLUSIVE",
            "profile": PROFILE,
            "reason": (
                "the source is outside the sound structural rules; no safety claim "
                "was produced"
            ),
        }
    return certificate.as_result()


__all__ = [
    "CompiledStructuralObligation",
    "PROFILE",
    "PropertyProof",
    "SourceMappingWitness",
    "StructuralDiscretizationCertificate",
    "compile_structural_discretization_obligations",
    "prove_discretization_structurally",
    "try_prove_discretization_structurally",
]
