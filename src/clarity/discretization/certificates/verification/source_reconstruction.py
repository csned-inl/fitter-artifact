"""Reconstruct the source-facing proof record without invoking the producer."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from clarity.certification.equations import Const, Equation, EquationModel, Expr, Ite, Op, Var
from clarity.certification.strict_extract import CertificationExtractor
from clarity.sysml.parser import IfStmt, PerformStmt, SubactionCallStmt

from .expressions import (
    INTERVAL_TIME,
    _factored_expr_from_dict,
    expr_to_dict,
    expression_hash,
    expression_symbols,
    simplify,
    substitute,
)


CHECKER_ORDER = [
    "linear",
    "convex",
    "reachability_linear",
    "reachability_convex",
    "exact_symbolic",
    "smt_fallback",
    "relational_invariant",
    "smt_reachability",
]


def _expand_definitions(
    model: EquationModel,
    expression: Expr,
    *,
    seen: set[str] | None = None,
) -> Expr:
    visited = set(seen or set())
    if isinstance(expression, Var) and expression.name in model.definitions:
        if expression.name in visited:
            raise ValueError(f"cyclic definition {expression.name}")
        return _expand_definitions(
            model,
            model.definitions[expression.name].expr,
            seen=visited | {expression.name},
        )
    if isinstance(expression, Op):
        return Op(
            expression.op,
            tuple(
                _expand_definitions(model, item, seen=set(visited))
                for item in expression.args
            ),
        )
    if isinstance(expression, Ite):
        return Ite(
            _expand_definitions(model, expression.cond, seen=set(visited)),
            _expand_definitions(model, expression.then_expr, seen=set(visited)),
            _expand_definitions(model, expression.else_expr, seen=set(visited)),
        )
    return expression


def _expanded_references(model: EquationModel, equation: Equation) -> set[str]:
    expanded: set[str] = set()
    pending = list(equation.refs())
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        definition = model.definitions.get(name)
        if definition is None or name in visited:
            expanded.add(name)
            continue
        visited.add(name)
        pending.extend(definition.refs())
    return expanded


def _equation(model: EquationModel, name: str) -> Equation | None:
    return (model.definitions.get(name) or model.transitions.get(name)
            or model.sample_events.get(name))


def _expand_post_update(
    model: EquationModel,
    expression: Expr,
    continuous: set[str],
    *,
    seen: set[str] | None = None,
) -> Expr:
    visited = set(seen or set())
    if isinstance(expression, Var):
        if expression.name in model.sampled_state:
            return expression
        if expression.name in model.definitions:
            if expression.name in visited:
                raise ValueError(f"cyclic definition {expression.name}")
            return _expand_post_update(
                model,
                model.definitions[expression.name].expr,
                continuous,
                seen=visited | {expression.name},
            )
        if expression.name in model.state and expression.name not in continuous:
            transition = model.transitions.get(expression.name)
            if transition is not None and expression.name not in visited:
                references = _expand_definitions(model, transition.expr).refs()
                if references & model.state or not references & model.actions:
                    return expression
                return _expand_post_update(
                    model,
                    transition.expr,
                    continuous,
                    seen=visited | {expression.name},
                )
        return expression
    if isinstance(expression, Op):
        return Op(
            expression.op,
            tuple(
                _expand_post_update(model, item, continuous, seen=set(visited))
                for item in expression.args
            ),
        )
    if isinstance(expression, Ite):
        return Ite(
            _expand_post_update(model, expression.cond, continuous, seen=set(visited)),
            _expand_post_update(
                model,
                expression.then_expr,
                continuous,
                seen=set(visited),
            ),
            _expand_post_update(
                model,
                expression.else_expr,
                continuous,
                seen=set(visited),
            ),
        )
    return expression


def _continuous_targets(
    extractor: CertificationExtractor,
    model: EquationModel,
) -> tuple[set[str], list[dict[str, Any]]]:
    targets: set[str] = set()
    records: list[dict[str, Any]] = []
    for action in extractor.parser.step_actions:
        target = extractor.canonical_name(action.target_key.split("::"))
        equation = model.transitions.get(target)
        if equation is None or "ContinuousRate" not in action.metadata:
            continue
        symbols = expression_symbols(equation.expr)
        targets.add(target)
        records.append({
            "target": target,
            "metadata": list(action.metadata),
            "equation": equation.pretty(),
            "uses_dt": any(
                name == "dt" or name.endswith("_dt") for name in symbols
            ),
        })
    return targets, records


def _constant_values(
    extractor: CertificationExtractor,
    model: EquationModel,
    dt_record: dict[str, Any],
) -> dict[str, Expr]:
    values: dict[str, Expr] = {}
    for parameter in extractor.parser.parameters:
        target = extractor.canonical_name(parameter.qualified_name.split("::"))
        if target in model.constants and "ScenarioInput" not in parameter.metadata:
            values[target] = Const(parameter.value)
    for target in model.constants:
        if target == "dt" or target.endswith("_dt"):
            values[target] = Const(dt_record["canonical"])
    return values


def _boolean_variables(
    extractor: CertificationExtractor,
    model: EquationModel,
    mdp_certificate: dict[str, Any],
) -> set[str]:
    variables: set[str] = set()
    for fqn, instance in extractor.parser.part_instances.items():
        part = extractor.parser.part_defs.get(instance.part_type)
        if part is None:
            continue
        for attribute, type_name in part.attributes.items():
            if type_name.lower() in {"bool", "boolean"}:
                variables.add(
                    extractor.canonical_name(fqn.split("::") + [attribute])
                )
    shield = mdp_certificate.get("mdp_obligations", {}).get("shield", {})
    interface = shield.get("interface") or {}
    output_types = interface.get("output_param_types") or {}
    for row in shield.get("output_action_mapping", []):
        if str(output_types.get(row.get("param"), "")).lower() in {
            "bool",
            "boolean",
        }:
            variables.update(row.get("action_vars", []))
    return variables & (model.state | model.actions | set(model.definitions))


def _integer_variables(
    extractor: CertificationExtractor,
    model: EquationModel,
) -> set[str]:
    variables: set[str] = set()
    for fqn, instance in extractor.parser.part_instances.items():
        part = extractor.parser.part_defs.get(instance.part_type)
        if part is None:
            continue
        for attribute, type_name in part.attributes.items():
            if type_name.lower() == "integer":
                variables.add(
                    extractor.canonical_name(fqn.split("::") + [attribute])
                )
    return variables & (
        model.state
        | model.actions
        | model.constants
        | set(model.definitions)
        | set(model.observations)
    )


def _shield_expression(
    extractor: CertificationExtractor,
    model: EquationModel,
    mdp_certificate: dict[str, Any],
) -> Expr:
    shield = mdp_certificate["mdp_obligations"]["shield"]
    predicate = shield.get("predicate_ast")
    if not isinstance(predicate, dict):
        raise ValueError("shield predicate AST is missing")
    interface = shield.get("interface") or {}
    subject = str(shield.get("subject_var") or "p")
    input_sources = {
        row["param"]: row["source_target"]
        for row in shield.get("input_coverage", [])
        if row.get("covered_by_q_and_action") is True
    }
    output_sources = {
        row["param"]: row["action_vars"][0]
        for row in shield.get("output_action_mapping", [])
        if row.get("unique_action_var") is True
        and len(row.get("action_vars", [])) == 1
    }
    input_params = set(interface.get("input_params", []))
    output_params = set(interface.get("output_params", []))

    def convert(node: dict[str, Any]) -> Expr:
        node_type = node.get("type")
        if node_type == "literal":
            return Const(node.get("value"))
        if node_type == "ref":
            path = list(node.get("path", []))
            if len(path) == 2 and path[0] == subject:
                parameter = path[1]
                if parameter in input_params:
                    source = input_sources.get(parameter)
                    observation = str(source).removeprefix("obs.")
                    if observation in model.observations:
                        return model.observations[observation].expr
                    if source in model.terminals:
                        return model.terminals[source].expr
                    raise ValueError(f"shield input {parameter} has no equation")
                if parameter in output_params:
                    action = output_sources.get(parameter)
                    if action is None:
                        raise ValueError(
                            f"shield output {parameter} has no action variable"
                        )
                    return Var(action)
            return extractor.resolve_reference(
                path,
                extractor.controller_context(),
                allow_legacy_fallback=False,
            )
        if node_type == "unary":
            return Op(str(node.get("op")), (convert(node["operand"]),))
        if node_type == "binary":
            return Op(
                str(node.get("op")),
                (convert(node["left"]), convert(node["right"])),
            )
        if node_type == "ternary":
            return Ite(
                convert(node["condition"]),
                convert(node["true"]),
                convert(node["false"]),
            )
        raise ValueError(f"unsupported shield node {node_type}")

    return _expand_definitions(model, convert(predicate))


def _policy_call_guards(
    extractor: CertificationExtractor,
    model: EquationModel,
) -> list[Expr]:
    controller = extractor.controller_part_definition()
    neural = extractor.neural_action_definition()
    if controller is None or neural is None:
        raise ValueError("controller or neural action is missing")
    actions = {action.name: action for action in controller.actions}
    guards: list[Expr] = []

    def walk(statements, conditions: list[Expr], seen: set[str]) -> None:
        for statement in statements:
            if isinstance(statement, SubactionCallStmt) and statement.type_name == neural.name:
                guards.extend(conditions)
            elif isinstance(statement, IfStmt):
                condition = extractor.expression(
                    statement.condition,
                    extractor.controller_context(),
                    allow_legacy_fallback=False,
                )
                walk(statement.body, conditions + [condition], seen)
                if statement.else_body:
                    walk(
                        statement.else_body,
                        conditions + [Op("not", (condition,))],
                        seen,
                    )
            elif isinstance(statement, PerformStmt):
                if statement.action_name in seen:
                    raise ValueError(f"recursive action {statement.action_name}")
                action = actions.get(statement.action_name)
                if action is not None:
                    walk(action.body, conditions, seen | {statement.action_name})

    for action in controller.actions:
        if action.name == "step":
            walk(action.body, [], {"step"})
    unique: list[Expr] = []
    for guard in guards:
        expanded = _expand_definitions(model, guard)
        if expanded not in unique:
            unique.append(expanded)
    return unique


def _scenario_constraints(
    extractor: CertificationExtractor,
    model: EquationModel,
) -> tuple[list[Expr], list[Expr]]:
    parameters: list[Expr] = []
    initial: list[Expr] = []
    for constraint in extractor.parser.parsed_constraints:
        if "ScenarioConstraint" not in getattr(constraint, "metadata", []):
            continue
        for conjunct in extractor.conjuncts(constraint.expression):
            expression = _expand_definitions(
                model,
                extractor.expression(
                    conjunct,
                    extractor.context(constraint.context),
                    allow_legacy_fallback=False,
                ),
            )
            (initial if expression_symbols(expression) & model.state else parameters).append(
                expression
            )
    return parameters, initial


def _is_physical_target(name: str) -> bool:
    return "time" not in name.lower()


def _trajectory(
    model: EquationModel,
    target: str,
    continuous: set[str],
    constants: dict[str, Expr],
) -> tuple[Expr, list[str]]:
    equation = model.transitions[target]
    dt_symbols = sorted(
        name
        for name in expression_symbols(equation.expr)
        if name == "dt" or name.endswith("_dt")
    )
    if not dt_symbols:
        raise ValueError(f"continuous assignment {target} does not use dt")
    expression = substitute(
        equation.expr,
        {name: Var(INTERVAL_TIME) for name in dt_symbols},
    )
    expression = _expand_post_update(model, expression, continuous)
    expression = substitute(
        expression,
        {name: value for name, value in constants.items() if name not in dt_symbols},
    )
    return simplify(expression), dt_symbols


def _and(expressions: Iterable[Expr]) -> Expr:
    items = tuple(expressions)
    if not items:
        return Const(True)
    if len(items) == 1:
        return items[0]
    return Op("and", items)


def _apply_recorded_removals(
    expression: Expr,
    records: Any,
) -> Expr:
    counts = Counter(
        item.get("removed_expression_sha256")
        for item in records or []
        if isinstance(item, dict)
        and isinstance(item.get("removed_expression_sha256"), str)
    )

    def reduce(node: Expr) -> Expr | None:
        if isinstance(node, Op):
            children = [reduce(item) for item in node.args]
            normalized = simplify(
                Op(node.op, tuple(item for item in children if item is not None))
            )
            if isinstance(normalized, Op) and normalized.op in {"and", "or"}:
                unique: dict[str, Expr] = {}
                for item in normalized.args:
                    key = expression_hash(item)
                    if normalized.op == "and" and counts[key] > 0:
                        counts[key] -= 1
                    else:
                        unique[key] = item
                ordered = tuple(unique[key] for key in sorted(unique))
                normalized = simplify(Op(normalized.op, ordered))
            return normalized
        if isinstance(node, Ite):
            return simplify(Ite(
                reduce(node.cond) or Const(False),
                reduce(node.then_expr) or Const(False),
                reduce(node.else_expr) or Const(False),
            ))
        return node

    reduced = reduce(expression)
    if reduced is None or any(counts.values()):
        raise ValueError("recorded constraint removals do not match the source")
    return reduced


def _timing_record(
    mdp_certificate: dict[str, Any],
    dt_record: dict[str, Any],
) -> dict[str, Any]:
    sampled = []
    for fact in mdp_certificate.get("equation_proof", {}).get("facts", []):
        if fact.get("rule") != "sampled_memory_bound":
            continue
        detail = fact.get("detail") or {}
        sampled.append({
            "target": detail.get("target", fact.get("var")),
            "source": detail.get("source"),
            "max_delay_steps": int(detail.get("max_delay", 1)),
            "schedule_state": detail.get("schedule_state"),
        })
    return {
        "fixed_dt": dt_record,
        "sampled_memory_cases": sampled,
        "information_delay_treatment": (
            "The recorded observation and action history reconstructs the modeled "
            "state used at the controller update. It is retained as information "
            "history and is not converted into elapsed physical time."
        ),
        "interval_length_use": (
            "Every physical trajectory is checked for interval time from zero through "
            "the single fixed dt. Sensor and schedule guards are retained in the "
            "sampled point premise."
        ),
        "observation_defect_envelope": {
            "physical_time_upper": dt_record["canonical"],
            "fixed_dt": True,
            "information_history": sampled,
        },
    }


def _inventory(
    model: EquationModel,
    included: set[str],
) -> list[dict[str, Any]]:
    return [
        {
            "target": equation.target,
            "kind": equation.kind,
            "source": equation.source,
            "expression": expr_to_dict(equation.expr),
            "included": equation.target in included,
            "reason": (
                "dependency closure of the interval counterexample"
                if equation.target in included
                else "outside the dependency closure of the interval counterexample"
            ),
        }
        for equation in sorted(
            model.all_equations(),
            key=lambda item: (item.kind, item.target),
        )
    ]


def _dependency_closure(model: EquationModel, seeds: set[str]) -> set[str]:
    closure = set(seeds)
    pending = list(sorted(seeds))
    while pending:
        name = pending.pop(0)
        equation = _equation(model, name)
        if equation is None:
            continue
        for reference in sorted(expression_symbols(equation.expr)):
            if reference not in closure:
                closure.add(reference)
                pending.append(reference)
    return closure
