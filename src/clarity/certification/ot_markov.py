"""Generic direct-SysML buffered-controller Markov certificate prototype.

The source model is the input.  This module does not dispatch on package names,
source hashes, or model-specific proof implementations, and it never executes
the simulator.  It accepts only the structural OT profile documented in
``docs/OT_MARKOV_SYSML_PROFILE.md`` and fails closed outside that profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
from pathlib import Path
from typing import Any, Iterable

from clarity.sysml.parser import (
    AssignStmt,
    BinaryExpr,
    Expr,
    ExpressionParser,
    IfStmt,
    InputBindingStmt,
    LiteralExpr,
    PerformStmt,
    RefExpr,
    SubactionCallStmt,
    TernaryExpr,
    UnaryExpr,
    SysMLParser,
)

from .dependencies import SysMLModel
from .symbolic_process import (
    BufferCandidate,
    ReconstructionRequirement,
    derive_buffer_candidate,
)


PROFILE = "ot-markov-sysml-profile-0.1"
MAX_SMT2_BYTES = 65_536


class UnsupportedOTProfile(ValueError):
    """The supplied source cannot be certified by the supported profile."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ObservationField:
    name: str
    type_name: str
    source: Expr
    state_sources: tuple[str, ...]
    fixed_scenario_source: str | None


@dataclass(frozen=True, slots=True)
class _FixedValue:
    value: Any


@dataclass(frozen=True, slots=True)
class ActionExecutionRelation:
    """How a controller proposal becomes the action executed by the plant."""

    mode: str
    proposal_names: tuple[str, ...]
    executed_names: tuple[str, ...]
    admissibility: Expr | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"identity", "keep_or_replace"}:
            raise ValueError(f"unsupported action execution mode {self.mode}")
        if self.proposal_names != self.executed_names:
            raise ValueError("profile 0.1 requires proposal/executed action alignment")
        if self.mode == "identity" and self.admissibility is not None:
            raise ValueError("identity execution cannot carry shield admissibility")
        if self.mode == "keep_or_replace" and self.admissibility is None:
            raise ValueError("shield execution requires an admissibility relation")


@dataclass(frozen=True, slots=True)
class OTMarkovModel:
    model_path: str
    source_sha256: str
    package_name: str
    observation: tuple[ObservationField, ...]
    action_names: tuple[str, ...]
    action_types: tuple[str, ...]
    scenario_parameters: tuple[str, ...]
    candidate: BufferCandidate
    dependency_witness: tuple[tuple[str, tuple[str, ...]], ...]
    parser: Any
    dependency_model: Any
    controller_fqn: str
    policy_subject: str | None
    action_execution: ActionExecutionRelation
    completion: Expr
    observation_paths: tuple[tuple[tuple[str, ...], str], ...]
    prior_action_latches: tuple[tuple[str, str], ...]

    @property
    def policy_requirement(self) -> Expr | None:
        """Compatibility view of the optional shield admissibility relation."""

        return self.action_execution.admissibility

    def summary(self) -> dict[str, object]:
        return {
            "profile": PROFILE,
            "source_sha256": self.source_sha256,
            "package": self.package_name,
            "observation": tuple(field.name for field in self.observation),
            "actions": self.action_names,
            "action_execution": self.action_execution.mode,
            "fixed_context": self.scenario_parameters,
            "candidate": {
                "b_obs": self.candidate.b_obs,
                "b_act": self.candidate.b_act,
            },
            "dependency_witness": self.dependency_witness,
        }


@dataclass(frozen=True, slots=True)
class OTMarkovQuery:
    model: OTMarkovModel
    initialization_empty: Any
    shield_not_total: Any
    shield_not_unique: Any
    markov_counterexample: Any

    def smt2_sizes(self) -> dict[str, int]:
        z3 = _z3()
        result: dict[str, int] = {}
        for name, formula in self.formulas().items():
            solver = z3.Solver(); solver.add(formula)
            result[name] = len(solver.to_smt2().encode("utf-8"))
        return result

    def formulas(self) -> dict[str, Any]:
        return {
            "initialization": self.initialization_empty,
            "shield_totality": self.shield_not_total,
            "shield_uniqueness": self.shield_not_unique,
            "markov": self.markov_counterexample,
        }

    def run(self, *, timeout_ms: int = 5_000) -> dict[str, object]:
        z3 = _z3()
        sizes = self.smt2_sizes()
        if max(sizes.values(), default=0) > MAX_SMT2_BYTES:
            return {
                "classification": "NO_RESULT",
                "reason": "formula_budget",
                "smt2_bytes": sizes,
            }
        results: dict[str, str] = {}
        for name, formula in self.formulas().items():
            solver = z3.Solver(); solver.set(timeout=timeout_ms); solver.add(formula)
            results[name] = str(solver.check())
        if any(value != "unsat" for value in results.values()):
            return {
                "classification": "NO_RESULT",
                "reason": "obligation_not_discharged",
                "obligations": results,
                "smt2_bytes": sizes,
            }
        return {
            "classification": "CERTIFIED_UNDER_PROFILE",
            "profile": PROFILE,
            "source_sha256": self.model.source_sha256,
            "certified_process": "controller observation, completion, and successor buffer",
            "implementation_condition": (
                "any downstream reward must be a deterministic function of the current "
                "buffer, proposal or executed action, completion, and successor observation"
            ),
            "candidate": {
                "b_obs": self.model.candidate.b_obs,
                "b_act": self.model.candidate.b_act,
                "evidence": tuple(
                    {
                        "component": item.component,
                        "source": item.source,
                        "lag": item.lag,
                        "equation": item.equation,
                    }
                    for item in self.model.candidate.evidence
                ),
            },
            "obligations": results,
            "smt2_bytes": sizes,
        }


def _z3() -> Any:
    try:
        import z3
    except ImportError as exc:  # pragma: no cover - workstation dependency
        raise RuntimeError("the pinned z3-solver environment is required") from exc
    return z3


def _walk(stmts: Iterable[Any], actions: dict[str, list[Any]]) -> Iterable[Any]:
    for stmt in stmts:
        if isinstance(stmt, PerformStmt):
            if stmt.action_name not in actions:
                raise UnsupportedOTProfile(
                    "UNSUPPORTED_SEMANTICS", f"unresolved performed action {stmt.action_name}"
                )
            yield from _walk(actions[stmt.action_name], actions)
            continue
        yield stmt
        if isinstance(stmt, IfStmt):
            yield from _walk(stmt.body, actions)
            yield from _walk(stmt.else_body, actions)


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


def _replace(expr: Expr, replacements: dict[tuple[str, ...], Expr]) -> Expr:
    if isinstance(expr, RefExpr):
        return replacements.get(tuple(expr.path), expr)
    if isinstance(expr, BinaryExpr):
        return BinaryExpr(expr.op, _replace(expr.left, replacements),
                          _replace(expr.right, replacements))
    if isinstance(expr, UnaryExpr):
        return UnaryExpr(expr.op, _replace(expr.operand, replacements))
    if isinstance(expr, TernaryExpr):
        return TernaryExpr(_replace(expr.condition, replacements),
                           _replace(expr.true_expr, replacements),
                           _replace(expr.false_expr, replacements))
    return expr


def _conjuncts(expr: Expr) -> tuple[Expr, ...]:
    if isinstance(expr, BinaryExpr) and expr.op == "and":
        return _conjuncts(expr.left) + _conjuncts(expr.right)
    return (expr,)


def _parameter_maps(parser: SysMLParser) -> tuple[dict[str, Any], dict[str, str]]:
    values = {item.qualified_name: item for item in parser.parameters}
    types: dict[str, str] = {}
    for fqn, instance in parser.part_instances.items():
        definition = parser.part_defs.get(instance.part_type)
        if definition is None:
            continue
        for name, type_name in definition.attributes.items():
            types[f"{fqn}::{name}"] = type_name
    for item in parser.derived_attributes:
        if isinstance(item.expression, LiteralExpr):
            values.setdefault(item.qualified_name, _FixedValue(item.expression.value))
    return values, types


def _parameter_key(model: OTMarkovModel, path: tuple[str, ...], context: str) -> str | None:
    parser = model.parser
    values, _types = _parameter_maps(parser)
    if path and path[0] == parser.system_part:
        candidate = "::".join(path)
    elif path and path[0] in {item.name for item in parser.part_instances.values()}:
        candidate = f"{parser.system_part}::" + "::".join(path)
    else:
        candidate = context + "::" + "::".join(path)
    if candidate in values:
        return candidate
    suffix = "::" + "::".join(path)
    matches = [key for key in values if key.endswith(suffix)]
    return matches[0] if len(matches) == 1 else None


def _find_policy(parser: SysMLParser) -> tuple[Any, Any, Any, list[Any], int]:
    if parser.controller_part is None:
        raise UnsupportedOTProfile("UNSUPPORTED_PROFILE", "missing #Neural owner")
    controller = parser.part_instances[parser.controller_part]
    definition = parser.part_defs[controller.part_type]
    policies = [item for item in definition.action_defs if "Neural" in item.metadata]
    if len(policies) != 1:
        raise UnsupportedOTProfile(
            "UNSUPPORTED_PROFILE", f"expected one #Neural Policy, found {len(policies)}"
        )
    policy = policies[0]
    actions = {item.name: item.body for item in definition.actions}
    calls_by_identity: dict[int, tuple[Any, list[Any], int]] = {}
    for action in definition.actions:
        flat = list(_walk(action.body, actions))
        for index, stmt in enumerate(flat):
            if isinstance(stmt, SubactionCallStmt) and stmt.type_name == policy.name:
                calls_by_identity.setdefault(id(stmt), (stmt, flat, index))
    calls = list(calls_by_identity.values())
    if len(calls) != 1:
        raise UnsupportedOTProfile(
            "UNSUPPORTED_PROFILE", f"expected one Policy invocation, found {len(calls)}"
        )
    neural_requirements = [item for item in definition.requirements
                           if "NeuralRequirement" in item[4]]
    if len(neural_requirements) > 1:
        raise UnsupportedOTProfile(
            "UNSUPPORTED_PROFILE", "expected at most one #NeuralRequirement"
        )
    requirement = neural_requirements[0] if neural_requirements else None
    return policy, calls[0][0], requirement, calls[0][1], calls[0][2]


def _copy_closure(graph: SysMLModel, key: str) -> tuple[str, ...]:
    result = [key]
    seen = {key}
    while key in graph.copies and len(graph.nsupp.get(key, ())) == 1:
        nxt = next(iter(graph.nsupp[key]))
        if nxt in seen:
            break
        result.append(nxt); seen.add(nxt); key = nxt
    return tuple(result)


def _affine_dynamic_coefficients(
    graph: SysMLModel, expr: Expr, context: list[str]
) -> dict[str, Fraction] | None:
    """Recognize an exact affine sample with literal nonzero scale.

    Fixed model parameters may occur additively.  Multiplication and division
    are accepted only by numeric literals, so a coefficient cannot silently
    become zero for some process context.
    """

    if isinstance(expr, LiteralExpr):
        return {}
    if isinstance(expr, RefExpr):
        dynamic = graph._collect_one(list(expr.path), context)
        if not dynamic:
            return {}
        if len(dynamic) != 1:
            return None
        return {next(iter(dynamic)): Fraction(1)}
    if isinstance(expr, UnaryExpr) and expr.op == "-":
        inner = _affine_dynamic_coefficients(graph, expr.operand, context)
        return None if inner is None else {key: -value for key, value in inner.items()}
    if isinstance(expr, BinaryExpr) and expr.op in {"+", "-"}:
        left = _affine_dynamic_coefficients(graph, expr.left, context)
        right = _affine_dynamic_coefficients(graph, expr.right, context)
        if left is None or right is None:
            return None
        sign = 1 if expr.op == "+" else -1
        result = dict(left)
        for key, value in right.items():
            result[key] = result.get(key, Fraction(0)) + sign * value
            if result[key] == 0:
                del result[key]
        return result
    if isinstance(expr, BinaryExpr) and expr.op in {"*", "/"}:
        if isinstance(expr.right, LiteralExpr) and not isinstance(expr.right.value, bool):
            scale = Fraction(str(expr.right.value))
            if scale == 0:
                return None
            left = _affine_dynamic_coefficients(graph, expr.left, context)
            if left is None:
                return None
            factor = scale if expr.op == "*" else Fraction(1, 1) / scale
            return {key: factor * value for key, value in left.items()}
        if (expr.op == "*" and isinstance(expr.left, LiteralExpr)
                and not isinstance(expr.left.value, bool)):
            scale = Fraction(str(expr.left.value))
            right = _affine_dynamic_coefficients(graph, expr.right, context)
            if scale == 0 or right is None:
                return None
            return {key: scale * value for key, value in right.items()}
    return None


def _transition_witness(graph: SysMLModel, observed: set[str]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    memo: dict[str, bool] = {}

    def supported(key: str, stack: set[str]) -> bool:
        if key in observed or key in graph.ACTIONS:
            return True
        if key in memo:
            return memo[key]
        if key not in graph.STATE or key not in graph.nsupp or key in stack:
            return False
        dependencies = graph.nsupp[key]
        answer = bool(dependencies) and all(
            dep == key and key in observed or supported(dep, stack | {key})
            for dep in dependencies
        )
        memo[key] = answer
        return answer

    witness: list[tuple[str, tuple[str, ...]]] = []
    for source in sorted(observed):
        dependencies = tuple(sorted(graph.nsupp.get(source, ())))
        if not dependencies:
            # A physical source reached through an action-local affine sample is
            # itself the future observation source.
            dependencies = (source,)
        if not all(dep == source or supported(dep, {source}) for dep in dependencies):
            raise UnsupportedOTProfile(
                "UNSUPPORTED_RECONSTRUCTION",
                f"next observation source {source} has an unresolved hidden dependency",
            )
        witness.append((source, dependencies))
    return tuple(witness)


def compile_ot_model(model_path: str | Path) -> OTMarkovModel:
    """Compile one supplied SysML model into the narrow generic proof profile."""

    selected = Path(model_path)
    parser = SysMLParser(str(selected)); parser.parse()
    if not parser.system_part or not parser.package_name:
        raise UnsupportedOTProfile("UNSUPPORTED_PROFILE", "missing package or root system")
    graph = SysMLModel(str(selected))
    policy, call, requirement, flat, call_index = _find_policy(parser)
    if any(item.type_name.lower() not in {"bool", "boolean"}
           for item in policy.out_params):
        raise UnsupportedOTProfile(
            "UNSUPPORTED_ACTION_DOMAIN", "profile 0.1 requires Boolean Policy outputs"
        )

    bindings = {item.name: item.expr for item in call.bindings
                if isinstance(item, InputBindingStmt)}
    completion_parameters = [item for item in policy.in_params
                             if "Completion" in item.metadata]
    if len(completion_parameters) != 1 or completion_parameters[0].name not in bindings:
        raise UnsupportedOTProfile("UNSUPPORTED_PROFILE", "missing unique #Completion binding")
    completion_name = completion_parameters[0].name

    prior_assignments: dict[tuple[str, ...], Expr] = {}
    for stmt in flat[:call_index]:
        if isinstance(stmt, AssignStmt):
            prior_assignments[tuple(stmt.target)] = stmt.expr
    latch_map: dict[str, str] = {}
    for stmt in flat[call_index + 1:]:
        if (isinstance(stmt, AssignStmt) and len(stmt.target) == 1
                and isinstance(stmt.expr, RefExpr)
                and len(stmt.expr.path) == 2 and stmt.expr.path[0] == call.name):
            latch_map[stmt.target[0]] = stmt.expr.path[1]

    scenario = tuple(sorted(item.qualified_name for item in parser.parameters
                            if "ScenarioInput" in item.metadata))
    observation: list[ObservationField] = []
    observation_paths: list[tuple[tuple[str, ...], str]] = []
    reconstructed: set[str] = set()
    evidence: list[ReconstructionRequirement] = []
    context = parser.controller_part.split("::")
    parameter_values, _parameter_types = _parameter_maps(parser)
    for parameter in policy.in_params:
        if parameter.name == completion_name:
            continue
        if parameter.name not in bindings:
            raise UnsupportedOTProfile(
                "UNSUPPORTED_PROFILE", f"unbound Policy input {parameter.name}"
            )
        raw = bindings[parameter.name]
        expanded = _replace(raw, prior_assignments)
        coefficients = _affine_dynamic_coefficients(graph, expanded, context)
        if coefficients is None or len(coefficients) > 1:
            raise UnsupportedOTProfile(
                "UNSUPPORTED_RECONSTRUCTION",
                f"Policy input {parameter.name} is not an invertible affine sample",
            )
        dynamic = set(coefficients)
        for ref in _refs(expanded):
            if graph._collect_one(list(ref), context):
                continue
            direct = parser.controller_part + "::" + "::".join(ref)
            suffix = "::" + "::".join(ref)
            matches = [key for key in parameter_values if key.endswith(suffix)]
            if direct not in parameter_values and len(matches) != 1:
                raise UnsupportedOTProfile(
                    "UNSUPPORTED_RECONSTRUCTION",
                    f"unclassified fixed-offset dependency {'.'.join(ref)}",
                )
        state_sources: tuple[str, ...] = ()
        if dynamic:
            source = next(iter(dynamic))
            state_sources = _copy_closure(graph, source)
            reconstructed.update(state_sources)
            evidence.append(ReconstructionRequirement(
                parameter.name, "current_observation", 0,
                "Policy input is an invertible direct or fixed-offset sample of "
                + state_sources[-1],
            ))
        fixed_source = None
        if isinstance(raw, RefExpr):
            candidate = parser.controller_part + "::" + "::".join(raw.path)
            if candidate in scenario:
                fixed_source = candidate
        observation.append(ObservationField(
            parameter.name, parameter.type_name, expanded, state_sources, fixed_source
        ))
        if isinstance(raw, RefExpr):
            observation_paths.append((tuple(raw.path), parameter.name))
        if not dynamic and fixed_source is not None:
            evidence.append(ReconstructionRequirement(
                parameter.name, "fixed_context", 0,
                "Policy input is a declared ScenarioInput fixed for the process instance",
            ))
        if not dynamic and fixed_source is None:
            # A non-dynamic input must be a declared parameter; arbitrary local
            # expressions are not silently classified as fixed context.
            refs = _refs(expanded)
            for ref in refs:
                qname = parser.controller_part + "::" + "::".join(ref)
                if qname not in parameter_values:
                    raise UnsupportedOTProfile(
                        "UNSUPPORTED_RECONSTRUCTION",
                        f"unclassified Policy input dependency {'.'.join(ref)}",
                    )
            evidence.append(ReconstructionRequirement(
                parameter.name, "fixed_context", 0,
                "Policy input is fixed by declared model parameters",
            ))

    completion = bindings[completion_name]
    observed_paths = dict(observation_paths)
    needs_prior_action = False
    for ref in _refs(completion):
        if ref in observed_paths:
            continue
        if len(ref) == 1 and ref[0] in latch_map:
            needs_prior_action = True
            continue
        qname = parser.controller_part + "::" + "::".join(ref)
        if qname not in parameter_values:
            raise UnsupportedOTProfile(
                "UNSUPPORTED_RECONSTRUCTION",
                f"completion depends on unclassified value {'.'.join(ref)}",
            )
    if needs_prior_action:
        evidence.append(ReconstructionRequirement(
            "pre-decision controller latches", "prior_action", 1,
            "controller latch assignments copy the preceding executed Policy output",
        ))

    dependency_witness = _transition_witness(graph, reconstructed)
    candidate = derive_buffer_candidate(tuple(evidence))
    requirement_ast = (
        ExpressionParser(requirement[3]).parse() if requirement is not None else None
    )
    execution = ActionExecutionRelation(
        mode="keep_or_replace" if requirement_ast is not None else "identity",
        proposal_names=tuple(item.name for item in policy.out_params),
        executed_names=tuple(item.name for item in policy.out_params),
        admissibility=requirement_ast,
    )
    return OTMarkovModel(
        model_path=str(selected),
        source_sha256=hashlib.sha256(selected.read_bytes()).hexdigest(),
        package_name=parser.package_name,
        observation=tuple(observation),
        action_names=tuple(item.name for item in policy.out_params),
        action_types=tuple(item.type_name for item in policy.out_params),
        scenario_parameters=scenario,
        candidate=candidate,
        dependency_witness=dependency_witness,
        parser=parser,
        dependency_model=graph,
        controller_fqn=parser.controller_part,
        policy_subject=requirement[1] if requirement is not None else None,
        action_execution=execution,
        completion=completion,
        observation_paths=tuple(observation_paths),
        prior_action_latches=tuple(sorted(latch_map.items())),
    )


def _sort_value(z3: Any, type_name: str, name: str) -> Any:
    lowered = type_name.lower()
    if lowered in {"bool", "boolean"}:
        return z3.Bool(name)
    if lowered in {"int", "integer"}:
        return z3.Int(name)
    if lowered in {"real", "float"}:
        return z3.Real(name)
    raise UnsupportedOTProfile("UNSUPPORTED_SORT", f"unsupported sort {type_name}")


def _literal(z3: Any, value: Any) -> Any:
    if isinstance(value, bool):
        return z3.BoolVal(value)
    if isinstance(value, int):
        return z3.IntVal(value)
    fraction = Fraction(str(value))
    return z3.RealVal(f"{fraction.numerator}/{fraction.denominator}")


def _lower(
    model: OTMarkovModel,
    expr: Expr,
    *,
    observation: dict[str, Any],
    action: dict[str, Any],
    prior_action: dict[str, Any],
    scenario: dict[str, Any],
    context: str,
) -> Any:
    z3 = _z3()
    path_to_observation = dict(model.observation_paths)
    latches = dict(model.prior_action_latches)
    parameters, _types = _parameter_maps(model.parser)
    if isinstance(expr, LiteralExpr):
        return _literal(z3, expr.value)
    if isinstance(expr, RefExpr):
        path = tuple(expr.path)
        if len(path) == 2 and path[0] == model.policy_subject:
            name = path[1]
            if name in observation:
                return observation[name]
            if name in action:
                return action[name]
        if path in path_to_observation:
            return observation[path_to_observation[path]]
        if len(path) == 1 and path[0] in latches:
            output = latches[path[0]]
            if output in prior_action:
                return prior_action[output]
        key = _parameter_key(model, path, context)
        if key in scenario:
            return scenario[key]
        if key in parameters:
            return _literal(z3, parameters[key].value)
        raise UnsupportedOTProfile(
            "UNSUPPORTED_EXPRESSION", f"unresolved reference {'.'.join(path)}"
        )
    if isinstance(expr, UnaryExpr):
        value = _lower(model, expr.operand, observation=observation, action=action,
                       prior_action=prior_action, scenario=scenario, context=context)
        if expr.op == "not":
            return z3.Not(value)
        if expr.op == "-":
            return -value
    if isinstance(expr, BinaryExpr):
        left = _lower(model, expr.left, observation=observation, action=action,
                      prior_action=prior_action, scenario=scenario, context=context)
        right = _lower(model, expr.right, observation=observation, action=action,
                       prior_action=prior_action, scenario=scenario, context=context)
        operations = {
            "+": lambda: left + right, "-": lambda: left - right,
            "*": lambda: left * right, "/": lambda: left / right,
            "==": lambda: left == right, ">=": lambda: left >= right,
            "<=": lambda: left <= right, ">": lambda: left > right,
            "<": lambda: left < right,
            "and": lambda: z3.And(left, right), "or": lambda: z3.Or(left, right),
            "implies": lambda: z3.Implies(left, right),
        }
        if expr.op in operations:
            return operations[expr.op]()
    if isinstance(expr, TernaryExpr):
        return z3.If(
            _lower(model, expr.condition, observation=observation, action=action,
                   prior_action=prior_action, scenario=scenario, context=context),
            _lower(model, expr.true_expr, observation=observation, action=action,
                   prior_action=prior_action, scenario=scenario, context=context),
            _lower(model, expr.false_expr, observation=observation, action=action,
                   prior_action=prior_action, scenario=scenario, context=context),
        )
    raise UnsupportedOTProfile("UNSUPPORTED_EXPRESSION", f"unsupported expression {expr!r}")


def _scenario_domain(model: OTMarkovModel, scenario: dict[str, Any]) -> Any:
    z3 = _z3()
    accepted: list[Any] = []
    scenario_keys = set(model.scenario_parameters)
    for constraint in model.parser.parsed_constraints:
        if "ScenarioConstraint" not in constraint.metadata:
            continue
        for conjunct in _conjuncts(constraint.expression):
            keys = {_parameter_key(model, ref, constraint.context) for ref in _refs(conjunct)}
            if None not in keys and keys.issubset(scenario_keys):
                accepted.append(_lower(
                    model, conjunct, observation={}, action={}, prior_action={},
                    scenario=scenario, context=constraint.context,
                ))
    return z3.And(*accepted) if accepted else z3.BoolVal(True)


def build_markov_query(model: OTMarkovModel) -> OTMarkovQuery:
    """Build the generic paired proof from one compiled source profile."""

    z3 = _z3()
    shielded = model.action_execution.mode == "keep_or_replace"
    _parameters, types = _parameter_maps(model.parser)
    scenario = {
        key: _sort_value(z3, types.get(key, "Real"), "fixed::" + key.replace("::", ":"))
        for key in model.scenario_parameters
    }
    domain = _scenario_domain(model, scenario)
    left_obs = {field.name: _sort_value(z3, field.type_name, "left::obs::" + field.name)
                for field in model.observation}
    right_obs = {field.name: _sort_value(z3, field.type_name, "right::obs::" + field.name)
                 for field in model.observation}
    proposal = {name: z3.Bool("proposal::" + name) for name in model.action_names}
    safe_left = (
        {name: z3.Bool("left::safe::" + name) for name in model.action_names}
        if shielded else {}
    )
    safe_right = (
        {name: z3.Bool("right::safe::" + name) for name in model.action_names}
        if shielded else {}
    )
    prior_left = ({name: z3.Bool("left::prior::" + name) for name in model.action_names}
                  if model.candidate.b_act else {})
    prior_right = ({name: z3.Bool("right::prior::" + name) for name in model.action_names}
                   if model.candidate.b_act else {})

    def valid(obs: dict[str, Any], action: dict[str, Any]) -> Any:
        if model.policy_requirement is None:
            return z3.BoolVal(True)
        return _lower(
            model, model.policy_requirement, observation=obs, action=action,
            prior_action={}, scenario=scenario, context=model.controller_fqn,
        )

    valid_proposal_left = valid(left_obs, proposal)
    valid_proposal_right = valid(right_obs, proposal)
    valid_safe_left = valid(left_obs, safe_left) if shielded else z3.BoolVal(True)
    valid_safe_right = valid(right_obs, safe_right) if shielded else z3.BoolVal(True)
    executed_left = (
        {
            name: z3.If(valid_proposal_left, proposal[name], safe_left[name])
            for name in model.action_names
        }
        if shielded else dict(proposal)
    )
    executed_right = (
        {
            name: z3.If(valid_proposal_right, proposal[name], safe_right[name])
            for name in model.action_names
        }
        if shielded else dict(proposal)
    )

    observation_equal = z3.And(*(left_obs[name] == right_obs[name]
                                 for name in left_obs))
    prior_equal = z3.And(*(prior_left[name] == prior_right[name]
                           for name in prior_left))
    fixed_observation_left = z3.And(*(
        left_obs[field.name] == scenario[field.fixed_scenario_source]
        for field in model.observation if field.fixed_scenario_source
    ))
    fixed_observation_right = z3.And(*(
        right_obs[field.name] == scenario[field.fixed_scenario_source]
        for field in model.observation if field.fixed_scenario_source
    ))
    completion_left = _lower(
        model, model.completion, observation=left_obs, action={},
        prior_action=prior_left, scenario=scenario, context=model.controller_fqn,
    )
    completion_right = _lower(
        model, model.completion, observation=right_obs, action={},
        prior_action=prior_right, scenario=scenario, context=model.controller_fqn,
    )

    argument_sorts = [item.sort() for item in left_obs.values()]
    argument_sorts += [item.sort() for item in scenario.values()]
    argument_sorts += [z3.BoolSort() for _name in model.action_names]
    left_arguments = [*left_obs.values(), *scenario.values(), *executed_left.values()]
    right_arguments = [*right_obs.values(), *scenario.values(), *executed_right.values()]
    next_left: list[Any] = []
    next_right: list[Any] = []
    for field in model.observation:
        result_sort = _sort_value(z3, field.type_name, "sort-probe").sort()
        function = z3.Function("source_next::" + field.name, *argument_sorts, result_sort)
        next_left.append(function(*left_arguments))
        next_right.append(function(*right_arguments))

    executed_difference = z3.Or(*(executed_left[name] != executed_right[name]
                                  for name in model.action_names))
    next_difference = z3.Or(*(left != right for left, right in zip(next_left, next_right)))
    result_difference = z3.Or(
        completion_left != completion_right,
        z3.And(z3.Not(completion_left), z3.Not(completion_right),
               z3.Or(next_difference,
                     executed_difference if model.candidate.b_act else z3.BoolVal(False))),
    )
    counterexample = z3.And(
        domain, fixed_observation_left, fixed_observation_right,
        observation_equal, prior_equal, valid_safe_left, valid_safe_right,
        result_difference,
    )

    all_scenario = list(scenario.values())
    initialization_empty = z3.Not(
        z3.Exists(all_scenario, domain) if all_scenario else domain
    )
    if shielded:
        total_action = {name: z3.Bool("total::" + name) for name in model.action_names}
        shield_not_total = z3.And(
            domain,
            z3.Not(z3.Exists(list(total_action.values()), valid(left_obs, total_action))),
        )
        unique_a = {name: z3.Bool("unique_a::" + name) for name in model.action_names}
        unique_b = {name: z3.Bool("unique_b::" + name) for name in model.action_names}
        shield_not_unique = z3.And(
            domain, valid(left_obs, unique_a), valid(left_obs, unique_b),
            z3.Or(*(unique_a[name] != unique_b[name] for name in model.action_names)),
        )
    else:
        shield_not_total = z3.BoolVal(False)
        shield_not_unique = z3.BoolVal(False)
    return OTMarkovQuery(
        model, initialization_empty, shield_not_total,
        shield_not_unique, counterexample,
    )


def prove_markov(model_path: str | Path, *, timeout_ms: int = 5_000) -> dict[str, object]:
    """Compile one supplied model and check its explicit logical obligations."""

    try:
        model = compile_ot_model(model_path)
        # Import lazily to keep the source compiler independent of solver and
        # logic backends while preserving the public one-model entry point.
        from .markov_logic import build_markov_logic_query

        query = build_markov_logic_query(model)
        result = query.run(timeout_ms=timeout_ms)
        return {"model": model.summary(), **result}
    except UnsupportedOTProfile as exc:
        return {
            "classification": "UNSUPPORTED",
            "reason": exc.code,
            "detail": exc.detail,
        }


__all__ = [
    "MAX_SMT2_BYTES",
    "OTMarkovModel",
    "OTMarkovQuery",
    "ObservationField",
    "PROFILE",
    "UnsupportedOTProfile",
    "build_markov_query",
    "compile_ot_model",
    "prove_markov",
]
