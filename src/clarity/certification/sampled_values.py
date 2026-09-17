"""Source inventory for distinct physical, sampled, and delivered values.

An edge records where a value came from. It NEVER authorizes replacing a held
value with the current value of its source. Event equations are deliberately
separate from cycle transition equations: composing events needs timing evidence.
"""

from __future__ import annotations

from clarity.sysml.parser import AcceptStmt, AssignStmt, IfStmt, ItemDeclStmt, PerformStmt
from .equations import Equation, Var
from clarity.sysml.parser_values import assigned_features, initialization_values


def walk_actions(parser, context, statements, active=()):
    for statement in statements:
        yield statement
        if isinstance(statement, IfStmt):
            yield from walk_actions(parser, context, statement.body, active)
            yield from walk_actions(parser, context, statement.else_body, active)
        elif isinstance(statement, PerformStmt):
            if statement.action_name in active:
                raise ValueError("recursive performed action cannot be extracted")
            instance = parser.part_instances.get(context)
            part = parser.part_defs[instance.part_type if instance else parser.system_type]
            action = next((a for a in part.actions if a.name == statement.action_name), None)
            if action is None:
                raise ValueError(f"unknown performed action {statement.action_name}")
            yield from walk_actions(parser, context, action.body, active + (action.name,))


def register_message_values(extractor):
    """Register storage before resolving expressions, so legacy aliases cannot win."""
    parser, model = extractor.parser, extractor.model
    bodies = [(ctx, body, False) for ctx, body in parser.step_action_bodies]
    bodies += [(ctx, transition.do_action or [], True)
               for ctx, machine in parser.instance_state_machines.items()
               for transition in machine.transitions]
    assignments, accepts = {}, {}
    runtime_keys = {}
    for context, body, is_event in bodies:
        statements = list(walk_actions(parser, context, body))
        local_items = {s.name for s in statements if isinstance(s, ItemDeclStmt)}
        for stmt in statements:
            if isinstance(stmt, AssignStmt):
                key = extractor.canonical_name(context.split("::") + stmt.target)
                runtime_keys[key] = context + "::" + "::".join(stmt.target)
                if is_event and len(stmt.target) > 1 and stmt.target[0] in local_items:
                    assignments[key] = (stmt.expr, context)
                    model.state.add(key)
            elif isinstance(stmt, AcceptStmt) and stmt.var_name:
                for field in parser.item_def_attrs.get(stmt.type_name, {}):
                    path = [stmt.var_name, field]
                    key = extractor.canonical_name(context.split("::") + path)
                    runtime_keys[key] = context + "::" + "::".join(path)
                    accepts[key] = context
                    model.state.add(key)
    extractor._message_assignments = assignments
    extractor._message_accepts = accepts
    extractor._value_runtime_keys = runtime_keys


def finish_value_semantics(extractor):
    parser, model = extractor.parser, extractor.model
    for target, (expression, context) in extractor._message_assignments.items():
        model.sample_events[target] = Equation(
            target, extractor.expression(expression, context.split("::"),
                                         allow_legacy_fallback=False),
            "sample_event", "state-machine action assignment on message acceptance",
        )
    for target in extractor._message_accepts:
        # Follow only the connection path, stopping at the stored sent payload.
        # Do not follow that payload's assignment back to current physical state.
        current, seen = target, set()
        while current not in seen:
            seen.add(current)
            prefix = next((p for p in sorted(extractor.legacy.alias, key=len, reverse=True)
                           if current == p or current.startswith(p + "_")), None)
            if prefix is None:
                break
            current = extractor.legacy.alias[prefix] + current[len(prefix):]
            if current in model.state:
                break
        if current == target or current not in model.state:
            model.add_diagnostic("error", "unresolved_sample_delivery",
                                 "accepted value has no distinct stored source", target)
            continue
        model.sample_events[target] = Equation(
            target, Var(current), "delivery_event", "accept copies the delivered payload",
        )

    physical = {extractor.canonical_name(a.target_key.split("::"))
                for a in parser.step_actions if "ContinuousRate" in a.metadata}
    equations = {**model.definitions, **model.transitions, **model.sample_events}

    def sources(name, seen):
        if name in physical:
            return {name}
        if name in seen or name not in equations:
            return set()
        eq = equations[name]
        # Conditional sampling's guard determines WHEN, not the measured value.
        from .equations import Ite
        expr = eq.expr
        refs = (expr.then_expr.refs() | expr.else_expr.refs()
                if isinstance(expr, Ite) else expr.refs())
        result = set()
        for ref in refs - {name}:
            result |= sources(ref, seen | {name})
        return result

    keys = dict(extractor._value_runtime_keys)
    for action in parser.step_actions:
        keys[extractor.canonical_name(action.target_key.split("::"))] = action.target_key
    for target in sorted(model.state - physical):
        for source in sorted(sources(target, set())):
            model.sampled_state.add(target)
            equation = equations.get(target)
            model.state_value_pairs.append({
                "physical_value": source,
                "sampled_value": target,
                "physical_runtime_key": keys.get(source),
                "sampled_runtime_key": keys.get(target),
                "sample_update": equation.pretty() if equation else None,
                "relationship": "distinct_storage_no_current_equality_assumed",
                "between_sample_events": "hold_sampled_value",
            })

    declarations = []
    for kind, values in (("binding", parser.derived_attributes),
                         ("initial", initialization_values(parser))):
        for attr in values:
            declarations.append({"name": attr.qualified_name, "kind": kind,
                                 "expression": repr(attr.expression)})
    assigned = assigned_features(parser)
    for parameter in parser.parameters:
        declarations.append({"name": parameter.qualified_name,
                             "kind": "initial" if parameter.qualified_name in assigned else "parameter",
                             "expression": repr(parameter.value)})
    model.value_semantics = {
        "version": 1,
        "feature_values": sorted(declarations, key=lambda r: (r["name"], r["kind"])),
        "state_value_pairs": model.state_value_pairs,
        "sample_events": [eq.pretty() for _, eq in sorted(model.sample_events.items())],
        "sampled_values": sorted(model.sampled_state),
        "physical_values": sorted(physical),
        "implicit_sample_to_physical_equality": False,
    }
    if model.sample_events:
        model.add_diagnostic(
            "error", "sample_event_composition_required",
            "sample/delivery storage is explicit; these event equations require an "
            "ordered decision-to-decision composition before Markov certification",
        )
