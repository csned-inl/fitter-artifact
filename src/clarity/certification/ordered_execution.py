"""Source-owned execution inventory retaining action order.

Storage copies remain separate. A recorded program is not, by itself, a proof
of a cross-part scheduler, numerical flow, or encoded-observation MDP theorem.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
import re
from pathlib import Path

from clarity.sysml import parser as ast
from clarity.sysml.expression_types import expression_type, parse_checked_expression
from clarity.sysml.parser_values import assigned_features, initialization_values

VERSION = 2
SAFETY_TAGS = {'Prohibition', 'Obligation'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def expression_record(expr):
    if isinstance(expr, ast.LiteralExpr):
        return {'kind': 'literal', 'value': expr.value,
                'type': expression_type(expr)}
    if isinstance(expr, ast.RefExpr):
        return {'kind': 'reference', 'path': list(expr.path)}
    if isinstance(expr, ast.UnaryExpr):
        return {'kind': 'unary', 'operator': expr.op, 'operand': expression_record(expr.operand)}
    if isinstance(expr, ast.BinaryExpr):
        return {'kind': 'binary', 'operator': expr.op, 'left': expression_record(expr.left),
                'right': expression_record(expr.right)}
    if isinstance(expr, ast.TernaryExpr):
        return {'kind': 'conditional', 'condition': expression_record(expr.condition),
                'true': expression_record(expr.true_expr), 'false': expression_record(expr.false_expr)}
    raise ValueError(f'unsupported source expression {type(expr).__name__}')


def source_text_without_comments(text):
    # Preserve offsets, and quoted requirement names. Comments are not declarations.
    pattern = r"'[^']*'|\"[^\"]*\"|/\*[\s\S]*?\*/|//[^\n]*"
    return re.sub(pattern, lambda m: (' ' * len(m[0]) if m[0].startswith('/') else m[0]), text)


def source_requirement_inventory(parser):
    source = source_text_without_comments(Path(parser.file_path).read_text())
    records = []
    for match in re.finditer(r"((?:#\w+\s+)*)requirement\s+def\s+(?:'([^']+)'|(\w+))\s*\{", source):
        tags = re.findall(r'#(\w+)', match[1])
        if not SAFETY_TAGS.intersection(tags):
            continue
        body, end = ast.SysMLParser._extract_block_from_string(source, match.end())
        subject = re.search(r'\bsubject\s+(\w+)\s*:\s*(\w+)\s*;', body)
        constraint = re.search(r'\brequire\s+constraint\s*\{', body)
        if not subject or not constraint:
            raise ValueError('unrepresented source safety declaration')
        expr, _ = ast.SysMLParser._extract_block_from_string(body, constraint.end())
        records.append({'name': match[2] or match[3], 'tags': tags,
                        'source_start': match.start(), 'source_end': end,
                        'subject': subject[1], 'subject_type': subject[2],
                        'expression_text': expr.strip(),
                        'expression': expression_record(parse_checked_expression(expr))})
    expected = {r['name']: r for r in records}
    if len(expected) != len(records):
        raise ValueError('duplicate source safety identity')
    parsed = {r.name: r for r in parser.parsed_requirements if SAFETY_TAGS.intersection(r.metadata)}
    if set(expected) != set(parsed):
        raise ValueError('source safety declaration inventory differs from parsed inventory')
    for name, req in parsed.items():
        if (expected[name]['expression'] != expression_record(req.expression)
                or set(expected[name]['tags']) != set(req.metadata)):
            raise ValueError(f'source safety expression/tags changed: {name}')
    return records


def declared_types(parser):
    types = {'dt': 'Real'}
    owners = [(parser.system_part, parser.system_type)] + [
        (ctx, inst.part_type) for ctx, inst in parser.part_instances.items()]
    for context, part_type in owners:
        part = parser.part_defs[part_type]
        for name, type_name in part.attributes.items():
            types[f'{context}::{name}'] = type_name
        for port, port_type in parser.part_def_ports.get(part_type, {}).items():
            for field, type_name in parser.port_def_attrs.get(port_type, {}).items():
                types[f'{context}::{port}::{field}'] = type_name
            for item, item_type in parser.port_def_items.get(port_type, {}).items():
                for field, type_name in parser.item_def_attrs.get(item_type, {}).items():
                    types[f'{context}::{port}::{item}::{field}'] = type_name
        for state in parser.instance_state_machines.get(context, ast.StateMachine('')).states:
            types[f'{context}::behavior.{state.name}'] = 'Boolean'
        def walk(body):
            for stmt in body:
                if isinstance(stmt, (ast.ItemDeclStmt, ast.AcceptStmt)):
                    name = stmt.name if isinstance(stmt, ast.ItemDeclStmt) else stmt.var_name
                    if name:
                        for field, type_name in parser.item_def_attrs.get(stmt.type_name, {}).items():
                            types[f'{context}::{name}::{field}'] = type_name
                elif isinstance(stmt, ast.AttributeDeclStmt):
                    types[f'{context}::{stmt.name}'] = stmt.type_name
                elif isinstance(stmt, ast.IfStmt):
                    walk(stmt.body); walk(stmt.else_body)
                elif isinstance(stmt, ast.SubactionCallStmt):
                    definition = next((a for a in part.action_defs if a.name == stmt.type_name), None)
                    if definition:
                        for param in definition.out_params:
                            types[f'{context}::{stmt.name}::{param.name}'] = param.type_name
        for action in part.actions:
            walk(action.body)
        for transition in parser.instance_state_machines.get(context, ast.StateMachine('')).transitions:
            if transition.trigger_var:
                for field, type_name in parser.item_def_attrs.get(transition.trigger, {}).items():
                    types[f'{context}::{transition.trigger_var}::{field}'] = type_name
            walk(transition.do_action or [])
    return types


def resolve_source_key(parser, context, path, subject=None):
    path = list(path)
    if subject and path and path[0] == subject:
        path = path[1:]
    key = '::'.join(path)
    if key == 'dt':
        return key
    if not key.startswith(parser.system_part + '::'):
        key = f'{context}::{key}'
    seen = set()
    while key not in seen:
        seen.add(key)
        matches = [p for p in parser.ref_bindings if key == p or key.startswith(p + '::')]
        if not matches:
            return key
        prefix = max(matches, key=len)
        key = parser.ref_bindings[prefix] + key[len(prefix):]
    raise ValueError(f'cyclic source reference {key}')


@dataclass(frozen=True)
class OrderedEvent:
    event_id: str
    context: str
    kind: str
    data: dict
    children: tuple = ()
    alternatives: tuple = ()


@dataclass(frozen=True)
class ExecutionInventory:
    """The control-flow program retained until decision composition is discharged."""
    source_sha256: str
    programs: tuple
    initial_values: tuple
    property_inventory: tuple
    value_types: dict
    diagnostics: tuple
    version: int = VERSION

    def to_dict(self):
        record = json.loads(json.dumps(asdict(self), allow_nan=False))
        record['sha256'] = fingerprint(record)
        return record


def build_execution_description(parser):
    types = declared_types(parser)
    diagnostics = []
    properties = source_requirement_inventory(parser)
    for req in parser.parsed_requirements:
        if not SAFETY_TAGS.intersection(req.metadata):
            continue
        resolver = lambda path: types.get(resolve_source_key(parser, req.context, path, req.subject_var))
        try:
            if expression_type(req.expression, resolver) != 'Boolean':
                raise ValueError('non-Boolean safety property')
        except ValueError as exc:
            diagnostics.append({'code': 'source_property_type', 'source': req.name, 'message': str(exc)})

    def lower(context, body, prefix, active=()):
        events = []
        part = parser.part_defs[parser.part_instances[context].part_type if context in parser.part_instances
                                else parser.system_type]
        for index, stmt in enumerate(body):
            identity = f'{prefix}/{index}'
            data, children, alternatives = {}, (), ()
            kind = type(stmt).__name__
            if isinstance(stmt, ast.AssignStmt):
                target = resolve_source_key(parser, context, stmt.target)
                data = {'target': target, 'expression': expression_record(stmt.expr),
                        'metadata': list(stmt.metadata), 'read_state': 'preceding_event',
                        'write_state': identity, 'unaffected_storage': 'unchanged'}
                try:
                    rhs_type = expression_type(stmt.expr, lambda path: types.get(resolve_source_key(parser, context, path)))
                    lhs_type = types.get(target)
                    if lhs_type is None or rhs_type != lhs_type and not (lhs_type == 'Real' and rhs_type == 'Integer'):
                        raise ValueError(f'{target}: {rhs_type} assignment to {lhs_type}')
                except ValueError as exc:
                    diagnostics.append({'code': 'source_assignment_type', 'source': identity, 'message': str(exc)})
                if 'ContinuousRate' in stmt.metadata and types.get(target) == 'Integer':
                    diagnostics.append({'code': 'integer_continuous_state', 'source': identity, 'message': target})
            elif isinstance(stmt, ast.IfStmt):
                data = {'condition': expression_record(stmt.condition), 'guard_state': 'preceding_event'}
                children = lower(context, stmt.body, identity + '/true', active)
                alternatives = lower(context, stmt.else_body, identity + '/false', active)
            elif isinstance(stmt, ast.PerformStmt):
                action = next((a for a in part.actions if a.name == stmt.action_name), None)
                if action is None or stmt.action_name in active:
                    raise ValueError(f'undefined/recursive performed action {context}::{stmt.action_name}')
                data = {'action': stmt.action_name}
                children = lower(context, action.body, identity + '/' + action.name, active + (action.name,))
            elif isinstance(stmt, ast.SendStmt):
                data = {'payload': stmt.item_name, 'port': stmt.port, 'copy_at': identity}
            elif isinstance(stmt, ast.AcceptStmt):
                data = {'destination': stmt.var_name, 'type': stmt.type_name, 'port': stmt.port,
                        'copy_at': identity, 'no_message': 'blocked_not_identity_update'}
            elif isinstance(stmt, ast.SubactionCallStmt):
                definition = next((a for a in part.action_defs if a.name == stmt.type_name), None)
                if definition is None or 'Neural' not in definition.metadata:
                    raise ValueError(f'unsupported action call {stmt.type_name}')
                kind = 'Decision'
                data = {'name': stmt.name, 'type': stmt.type_name,
                        'inputs': {b.name: expression_record(b.expr) for b in stmt.bindings
                                   if isinstance(b, ast.InputBindingStmt)},
                        'outputs': {p.name: p.type_name for p in definition.out_params},
                        'action_convention': 'executed'}
            elif isinstance(stmt, ast.AttributeDeclStmt):
                data = {'name': stmt.name, 'type': stmt.type_name, 'evaluation': 'at_declaration',
                        'expression': None if stmt.init_expr is None else expression_record(stmt.init_expr)}
            elif isinstance(stmt, (ast.ItemDeclStmt, ast.InParamStmt, ast.OutParamStmt)):
                data = {'name': stmt.name, 'type': stmt.type_name}
            else:
                raise ValueError(f'unsupported source statement {kind}')
            events.append(OrderedEvent(identity, context, kind, data, tuple(children), tuple(alternatives)))
        return tuple(events)

    programs = []
    for context, body in parser.step_action_bodies:
        programs.append({'context': context, 'kind': 'step',
                         'events': tuple(asdict(e) for e in lower(context, body, context + '/step'))})
    for context, machine in parser.instance_state_machines.items():
        for transition in machine.transitions:
            programs.append({'context': context, 'kind': 'state_machine_transition',
                'name': transition.name, 'from': transition.from_state, 'to': transition.to_state,
                'trigger': transition.trigger, 'port': transition.trigger_port,
                'guard': None if transition.guard is None else expression_record(transition.guard),
                'events': tuple(asdict(e) for e in lower(context, transition.do_action or [],
                                                        context + '/state_machine/' + transition.name))})
    initial = [{'target': a.qualified_name, 'kind': 'initial', 'expression': expression_record(a.expression)}
               for a in initialization_values(parser)]
    initial += [{'target': a.qualified_name, 'kind': 'binding', 'expression': expression_record(a.expression)}
                for a in parser.derived_attributes]
    assigned = assigned_features(parser)
    initial += [{'target': p.qualified_name,
                 'kind': 'initial' if p.qualified_name in assigned else 'parameter', 'value': p.value,
                 'metadata': list(p.metadata)} for p in parser.parameters]
    return ExecutionInventory(hashlib.sha256(Path(parser.file_path).read_bytes()).hexdigest(),
                              tuple(programs), tuple(initial), tuple(properties), types, tuple(diagnostics))


def validate_execution_description(record, model_path):
    if not isinstance(record, dict):
        return ['missing ordered source execution description']
    parser = ast.SysMLParser(str(model_path)); parser.parse()
    expected = build_execution_description(parser).to_dict()
    if record != expected:
        return ['ordered execution/property/type inventory does not match source']
    return [f"{d['code']}: {d['source']}: {d['message']}" for d in expected['diagnostics']]
