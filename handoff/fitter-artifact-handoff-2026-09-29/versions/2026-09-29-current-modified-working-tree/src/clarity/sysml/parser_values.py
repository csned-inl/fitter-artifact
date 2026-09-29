"""Read the restored CLARITY parser's existing declaration/action records.

The parser and SysML inputs stay unchanged. Mutable declarations omitted from
its derived-attribute list supply starting values, as in the original parser.
"""
from .parser import DerivedAttribute, ExpressionParser


def owners(parser):
    return [(parser.system_part, parser.system_type)] + [
        (context, instance.part_type)
        for context, instance in parser.part_instances.items()]


def assigned_features(parser):
    result = set()
    for context, part_type in owners(parser):
        part = parser.part_defs.get(part_type)
        if part is None:
            continue
        bodies = [action.body for action in part.actions]
        machine = parser.instance_state_machines.get(context)
        if machine is not None:
            bodies += [transition.do_action or [] for transition in machine.transitions]
        for body in bodies:
            for statement, _ in parser._walk_assigns(body):
                path = '::'.join(statement.target)
                result.add(path if path.startswith(parser.system_part + '::')
                           else context + '::' + path)
    return result


def initialization_values(parser):
    """Recover declared starting expressions excluded from live derivations."""
    installed = {p.qualified_name for p in parser.parameters}
    installed.update(a.qualified_name for a in parser.derived_attributes)
    result = []
    for context, part_type in owners(parser):
        part = parser.part_defs.get(part_type)
        if part is None:
            continue
        for name, text in part.derived_attributes.items():
            key = context + '::' + name
            if key not in installed:
                result.append(DerivedAttribute(
                    name, key, ExpressionParser(text).parse(), context))
    return result
