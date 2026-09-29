"""Extract controller obligations and timing context from the checked model."""

from __future__ import annotations

from typing import Any

from clarity.certification.equations import Const, EquationModel, Expr, Ite, Op, Var
from clarity.certification.strict_extract import CertificationExtractor
from clarity.sysml.parser import IfStmt, PerformStmt, SubactionCallStmt

from .model.proof_rules import (
    ProofDeferred,
    expr_to_dict,
    expand_definitions,
    expression_symbols,
)


def shield_expression(
    extractor: CertificationExtractor,
    model: EquationModel,
    mdp_certificate: dict[str, Any],
) -> tuple[Expr, dict[str, Any]]:
    shield = mdp_certificate["mdp_obligations"]["shield"]
    predicate = shield.get("predicate_ast")
    interface = shield.get("interface") or {}
    subject = str(shield.get("subject_var") or "p")
    if not isinstance(predicate, dict):
        raise ProofDeferred("BLOCKED_INPUT", "shield predicate AST is missing")

    input_sources = {
        row["param"]: row["source_target"]
        for row in shield.get("input_coverage", [])
        if row.get("covered_by_q_and_action") is True
    }
    output_sources = {
        row["param"]: row["action_vars"][0]
        for row in shield.get("output_action_mapping", [])
        if row.get("unique_action_var") is True and len(row.get("action_vars", [])) == 1
    }
    input_params = set(interface.get("input_params", []))
    output_params = set(interface.get("output_params", []))
    ctrl_ctx = extractor.controller_context()

    def convert(node: dict[str, Any]) -> Expr:
        node_type = node.get("type")
        if node_type == "literal":
            return Const(node.get("value"))
        if node_type == "ref":
            path = list(node.get("path", []))
            if len(path) == 2 and path[0] == subject:
                param = path[1]
                if param in input_params:
                    source = input_sources.get(param)
                    observation_key = str(source).removeprefix("obs.")
                    if observation_key in model.observations:
                        return model.observations[observation_key].expr
                    if source in model.terminals:
                        return model.terminals[source].expr
                    raise ProofDeferred(
                        "BLOCKED_INPUT",
                        f"shield input {param} has no checked equation",
                    )
                if param in output_params:
                    action = output_sources.get(param)
                    if action is None:
                        raise ProofDeferred(
                            "BLOCKED_INPUT",
                            f"shield output {param} has no unique action variable",
                        )
                    return Var(action)
            return extractor.resolve_reference(
                path,
                ctrl_ctx,
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
        raise ProofDeferred("UNSUPPORTED_EXPRESSION", f"shield node {node_type}")

    expression = expand_definitions(model, convert(predicate))
    return expression, {
        "input_sources": input_sources,
        "output_sources": output_sources,
        "predicate": expr_to_dict(expression),
    }


def policy_call_guards(
    extractor: CertificationExtractor,
    model: EquationModel,
) -> list[Expr]:
    controller = extractor.controller_part_definition()
    neural = extractor.neural_action_definition()
    if controller is None or neural is None:
        raise ProofDeferred("BLOCKED_INPUT", "controller or neural action is missing")
    actions = {action.name: action for action in controller.actions}
    guards: list[Expr] = []

    def walk(statements, conditions: list[Expr], seen_actions: set[str]) -> None:
        for statement in statements:
            if isinstance(statement, SubactionCallStmt) and statement.type_name == neural.name:
                guards.extend(conditions)
            elif isinstance(statement, IfStmt):
                condition = extractor.expression(
                    statement.condition,
                    extractor.controller_context(),
                    allow_legacy_fallback=False,
                )
                walk(statement.body, conditions + [condition], seen_actions)
                if statement.else_body:
                    walk(
                        statement.else_body,
                        conditions + [Op("not", (condition,))],
                        seen_actions,
                    )
            elif isinstance(statement, PerformStmt):
                if statement.action_name in seen_actions:
                    raise ProofDeferred(
                        "UNSUPPORTED_EXPRESSION",
                        f"recursive performed action {statement.action_name}",
                    )
                action = actions.get(statement.action_name)
                if action is not None:
                    walk(action.body, conditions, seen_actions | {statement.action_name})

    for action in controller.actions:
        if action.name == "step":
            walk(action.body, [], {"step"})
    unique: list[Expr] = []
    for guard in guards:
        guard = expand_definitions(model, guard)
        if guard not in unique:
            unique.append(guard)
    return unique


def scenario_constraints(
    extractor: CertificationExtractor,
    model: EquationModel,
) -> tuple[list[Expr], list[Expr]]:
    parameter_constraints: list[Expr] = []
    initial_state_constraints: list[Expr] = []
    for constraint in extractor.parser.parsed_constraints:
        if "ScenarioConstraint" not in getattr(constraint, "metadata", []):
            continue
        ctx = extractor.context(constraint.context)
        for conjunct in extractor.conjuncts(constraint.expression):
            expression = expand_definitions(
                model,
                extractor.expression(
                    conjunct,
                    ctx,
                    allow_legacy_fallback=False,
                ),
            )
            if expression_symbols(expression) & model.state:
                initial_state_constraints.append(expression)
            else:
                parameter_constraints.append(expression)
    return parameter_constraints, initial_state_constraints


def boolean_variables(
    extractor: CertificationExtractor,
    model: EquationModel,
    mdp_certificate: dict[str, Any],
) -> set[str]:
    variables = set()
    for fqn, instance in extractor.parser.part_instances.items():
        part = extractor.parser.part_defs.get(instance.part_type)
        if part is None:
            continue
        for attribute, type_name in part.attributes.items():
            if type_name.lower() in {"bool", "boolean"}:
                variables.add(extractor.canonical_name(fqn.split("::") + [attribute]))
    interface = mdp_certificate["mdp_obligations"]["shield"].get("interface") or {}
    output_types = interface.get("output_param_types") or {}
    output_mapping = mdp_certificate["mdp_obligations"]["shield"].get(
        "output_action_mapping", []
    )
    for row in output_mapping:
        if str(output_types.get(row.get("param"), "")).lower() in {"bool", "boolean"}:
            variables.update(row.get("action_vars", []))
    return variables & (model.state | model.actions | set(model.definitions))


def integer_variables(
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


def continuous_targets(
    extractor: CertificationExtractor,
    model: EquationModel,
) -> tuple[set[str], set[str], list[dict[str, Any]]]:
    annotated: set[str] = set()
    dt_updated: set[str] = set()
    records: list[dict[str, Any]] = []
    for action in extractor.parser.step_actions:
        target = extractor.canonical_name(action.target_key.split("::"))
        equation = model.transitions.get(target)
        if equation is None:
            continue
        symbols = expression_symbols(equation.expr)
        uses_dt = any(name == "dt" or name.endswith("_dt") for name in symbols)
        if uses_dt:
            dt_updated.add(target)
        if "ContinuousRate" in action.metadata:
            annotated.add(target)
            records.append({
                "target": target,
                "metadata": list(action.metadata),
                "equation": equation.pretty(),
                "uses_dt": uses_dt,
            })
    return annotated, dt_updated, records


def specified_constant_values(
    extractor: CertificationExtractor,
    model: EquationModel,
    dt_record: dict[str, Any],
) -> dict[str, Expr]:
    values: dict[str, Expr] = {}
    for parameter in extractor.parser.parameters:
        target = extractor.canonical_name(parameter.qualified_name.split("::"))
        if target not in model.constants or "ScenarioInput" in parameter.metadata:
            continue
        values[target] = Const(parameter.value)
    for target in model.constants:
        if target == "dt" or target.endswith("_dt"):
            values[target] = Const(dt_record["canonical"])
    return values


def timing_record(
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
