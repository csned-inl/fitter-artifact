"""Functional execution equations for the source-owned decision program.

The input of a decision transition includes physical values, stored readings,
mailboxes and the continuation at the suspended source call. These are distinct
objects. ``advance`` applies the executed action and computes the first next
decision, terminal or error without a prescribed number of simulator cycles.

This evaluator is independent of SimulationEngine. It is used to validate the
operation equations against that implementation; it is not a substitute for the
symbolic closure/reconstruction obligations or a different runtime controller.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import operator


@dataclass(frozen=True)
class Alias:
    target: str


@dataclass(frozen=True)
class Binding:
    expression: dict
    context: str


@dataclass
class Configuration:
    node: str
    state: dict = field(default_factory=dict)
    machines: dict = field(default_factory=dict)
    mailboxes: dict = field(default_factory=dict)
    local_items: dict = field(default_factory=dict)
    stack: list = field(default_factory=list)
    machine_frame: dict = field(default_factory=dict)
    engine_time: float = 0.0
    pending_completion: bool = False
    inputs: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    sequence: int = 0
    error: str | None = None
    outcome: str | None = None


class MissingReference(ValueError):
    pass


def value_kind(value):
    """The symbolic backend preserves Python's distinct Boolean/int/float kinds."""
    return getattr(value, 'python_kind', type(value))


class ExecutionEquations:
    """Evaluate all source operations as ordered configuration updates."""
    def __init__(self, execution, dt):
        self.execution = execution
        self.graph = execution['decision_transition']
        self.dt = dt
        self.system = self.graph['system_part']
        self.references = self.graph['reference_bindings']
        self.properties = {p['name']: p for p in execution['property_inventory']}

    def canonical(self, state, key):
        seen = set()
        while isinstance(state.get(key), Alias):
            if key in seen:
                raise ValueError('cyclic source binding: ' + key)
            seen.add(key); key = state[key].target
        return key

    def read(self, state, key, active=()):
        if key in active:
            raise ValueError('cyclic source binding: ' + key)
        value = state.get(key)
        if isinstance(value, Alias):
            return self.read(state, value.target, (*active, key))
        if isinstance(value, Binding):
            return self.evaluate(value.expression, state, value.context, strict=True, active=(*active, key))
        return value

    def lookup_order(self, path, context):
        original = path
        absolute = path
        prefix = self.references.get(context + '::' + path[0]) if path and context else None
        if prefix:
            absolute = prefix.split('::') + path[1:]
        candidates = ['::'.join(absolute)] if prefix else []
        if context:
            candidates.append(context + '::' + '.'.join(original))
        candidates += ['.'.join(original), '::'.join(absolute)]
        if context:
            candidates.append(context + '::' + '::'.join(original))
        if self.system:
            candidates.append(self.system + '::' + '::'.join(original))
        return candidates

    def total_kind(self, expr, state, context='', active=()):
        """Prove a pure expression total before merging Boolean branches.

        Missing references, mixed numeric conversions and division remain lazy.
        Thus an inactive clause can never introduce an evaluation error.
        """
        kind = expr['kind']
        if kind == 'literal': return type(expr['value'])
        if kind == 'reference':
            candidates = expr.get('lookup_order') or self.lookup_order(expr['path'], context)
            key = next((k for k in candidates if k in state), None)
            if key is None or key in active: return None
            while isinstance(state.get(key), Alias):
                active = (*active, key); key = state[key].target
                if key in active: return None
            value = state.get(key)
            if isinstance(value, Binding):
                return self.total_kind(value.expression, state, value.context, (*active, key))
            return value_kind(value) if value is not None else None
        if kind == 'unary':
            operand = self.total_kind(expr['operand'], state, context, active)
            if expr['operator'] == 'not' and operand is bool: return bool
            if expr['operator'] == '-' and operand in (int, float): return operand
            return None
        if kind != 'binary': return None
        left = self.total_kind(expr['left'], state, context, active)
        right = self.total_kind(expr['right'], state, context, active)
        op = expr['operator']
        if op in ('and', 'or', 'implies'):
            return bool if left is bool and right is bool else None
        if op == '==' and left is bool and right is bool: return bool
        if left not in (int, float) or right not in (int, float): return None
        if op in ('==', '>', '>=', '<', '<='): return bool
        if op in ('+', '-', '*') and left is right: return left
        return None

    def evaluate(self, expr, state, context='', *, strict=False, active=()):
        kind = expr['kind']
        if kind == 'literal':
            return expr['value']
        if kind == 'reference':
            candidates = expr.get('lookup_order') or self.lookup_order(expr['path'], context)
            key = next((key for key in candidates if key in state), None)
            value = None if key is None else self.read(state, key, active)
            if strict and value is None:
                raise MissingReference('unresolved requirement reference ' + '.'.join(expr['path']))
            return value
        if kind == 'conditional':
            cond = self.evaluate(expr['condition'], state, context, strict=strict, active=active)
            if value_kind(cond) is not bool:
                raise ValueError('non-Boolean conditional guard')
            return self.evaluate(expr['true' if cond else 'false'], state, context, strict=strict, active=active)
        if kind == 'unary':
            value = self.evaluate(expr['operand'], state, context, strict=strict, active=active)
            if expr['operator'] == 'not' and value_kind(value) is bool:
                return value.logical_not() if hasattr(value, 'logical_not') else not value
            if expr['operator'] == '-' and value_kind(value) in (int, float):
                return -value
            raise ValueError('invalid operand for ' + expr['operator'])
        if kind != 'binary':
            raise ValueError('unsupported expression ' + kind)
        op = expr['operator']
        left = self.evaluate(expr['left'], state, context, strict=strict, active=active)
        if op in ('and', 'or', 'implies'):
            if value_kind(left) is not bool:
                raise ValueError('non-Boolean left operand for ' + op)
            if (hasattr(left, 'logical_combine') and
                    self.total_kind(expr['right'], state, context, active) is bool):
                right = self.evaluate(expr['right'], state, context, strict=strict, active=active)
                return left.logical_combine(right, op)
            if op == 'and' and not left: return False
            if op == 'or' and left: return True
            if op == 'implies' and not left: return True
            right = self.evaluate(expr['right'], state, context, strict=strict, active=active)
            if value_kind(right) is not bool:
                raise ValueError('non-Boolean right operand for ' + op)
            return right
        right = self.evaluate(expr['right'], state, context, strict=strict, active=active)
        if left is None or right is None:
            raise ValueError('undefined operand for ' + op)
        if op != '==' and (value_kind(left) not in (int, float) or value_kind(right) not in (int, float)):
            raise ValueError('non-numeric operand for ' + op)
        if op == '==' and (value_kind(left) is bool) != (value_kind(right) is bool):
            raise ValueError('incompatible Boolean/numeric equality')
        if op == '/' and right == 0:
            raise ValueError('division by zero in source expression')
        functions = {'+': operator.add, '-': operator.sub, '*': operator.mul, '/': operator.truediv,
                     '==': operator.eq, '>': operator.gt, '>=': operator.ge, '<': operator.lt, '<=': operator.le}
        return functions[op](left, right)

    def subtype(self, actual, expected):
        seen = set()
        while actual is not None:
            if actual == expected: return True
            if actual in seen: raise ValueError('cyclic item inheritance')
            seen.add(actual); actual = self.graph['item_type_parents'].get(actual)
        return False

    def constraint_key(self, path, context):
        prefix = self.references.get(context + '::' + path[0]) if path and context else None
        return (prefix + '::' + '::'.join(path[1:])).rstrip(':') if prefix else (
            (context + '::') if context else '') + '::'.join(path)

    def _constraint_targets(self, expr, context, state, result):
        if expr['kind'] != 'binary': return
        if expr['operator'] == 'and':
            self._constraint_targets(expr['left'], context, state, result)
            self._constraint_targets(expr['right'], context, state, result)
        elif expr['operator'] == 'implies':
            self._constraint_targets(expr['right'], context, state, result)
        elif expr['operator'] == '==' and expr['left']['kind'] == 'reference':
            result.add(self.canonical(state, self.constraint_key(expr['left']['path'], context)))

    def _assign_constraints(self, expr, context, state):
        if expr['kind'] != 'binary': return
        if expr['operator'] == 'and':
            self._assign_constraints(expr['left'], context, state)
            self._assign_constraints(expr['right'], context, state)
        elif expr['operator'] == '==' and expr['left']['kind'] == 'reference':
            value = self.evaluate(expr['right'], state, context)
            state[self.canonical(state, self.constraint_key(expr['left']['path'], context))] = value

    def _implies_constraints(self, expr, context, state):
        if expr['kind'] != 'binary': return
        if expr['operator'] == 'and':
            self._implies_constraints(expr['left'], context, state)
            self._implies_constraints(expr['right'], context, state)
        elif expr['operator'] == 'implies' and self.evaluate(expr['left'], state, context):
            self._assign_constraints(expr['right'], context, state)

    def solve_constraints(self, config, data):
        state = config.state
        for key in state:
            if 'behavior.' in key: state[key] = False
        for context, current in config.machines.items():
            for name in self.graph['machine_states'].get(context, []):
                state[context + '::behavior.' + name] = name == current
            state['behavior.' + current] = True
        for binding in data['bindings']:
            state[binding['target']] = Binding(binding['expression'], binding['target'].rsplit('::', 1)[0])
        def contains_implies(expr):
            return expr['kind'] == 'binary' and (expr['operator'] == 'implies' or
                contains_implies(expr['left']) or contains_implies(expr['right']))
        constraints = data['constraints']
        ordered = [c for c in constraints if contains_implies(c['expression'])]
        ordered += [c for c in constraints if not contains_implies(c['expression'])]
        for _ in range(data['algorithm']['iteration_limit']):
            before, unresolved = dict(state), {}
            for constraint in ordered:
                expr, context = constraint['expression'], constraint['context']
                try:
                    if contains_implies(expr):
                        self._implies_constraints(expr, context, state)
                    elif expr['kind'] == 'binary' and expr['operator'] == '==':
                        self._assign_constraints(expr, context, state)
                except ValueError as exc:
                    unresolved[constraint['name']] = str(exc)
            defined = set()
            for constraint in constraints:
                self._constraint_targets(constraint['expression'], constraint['context'], state, defined)
            incoming = {}
            for flow in data['flows']:
                start = self.system + '::' + flow['from_port'].replace('.', '::') + '::'
                end = self.system + '::' + flow['to_port'].replace('.', '::') + '::'
                for key in list(state):
                    if key.startswith(start):
                        target = self.canonical(state, end + key[len(start):])
                        if target not in defined: incoming.setdefault(target, []).append(key)
            for target, sources in incoming.items():
                if len(set(sources)) != 1:
                    raise ValueError('multiple flow inputs lack a source aggregation equation: ' + target)
                for source in sources:
                    value = self.read(state, source)
                    if value is not None: state[target] = value
            if state == before:
                if unresolved: raise ValueError(f'unresolved source constraints: {unresolved}')
                failed = []
                for constraint in constraints:
                    value = self.evaluate(constraint['expression'], state, constraint['context'], strict=True)
                    if value_kind(value) is not bool or not value: failed.append(constraint['name'])
                if failed: raise ValueError(f'source constraints false after propagation: {failed}')
                return
        raise ValueError(f'source constraint propagation did not converge: {unresolved}')

    def initialize(self, config, data, overrides):
        state = config.state
        for parameter in data['parameters']:
            key = parameter['qualified_name']
            state[key] = overrides.get(parameter['cli_name'], overrides.get(key, parameter['value']))
        config.machines.update(data['machine_initial_states'])
        for context, attributes in self.graph['part_attribute_types'].items():
            for name, kind in attributes.items():
                key = context + '::' + name
                if key not in state and kind in ('Boolean', 'Real'):
                    state[key] = False if kind == 'Boolean' else 0.0
        for context, states in self.graph['machine_states'].items():
            for name in states: state[context + '::behavior.' + name] = name == config.machines[context]
        for lhs, rhs in data['stored_aliases'].items(): state[lhs] = Alias(rhs)
        for target in self.graph['initial_transition_targets']: state.setdefault(target, 0.0)
        for binding in data['constraints']['bindings']:
            state[binding['target']] = Binding(binding['expression'], binding['target'].rsplit('::', 1)[0])
        pending = [row for row in data['values'] if row['kind'] == 'initial' and 'expression' in row]
        for row in pending: state.pop(row['target'], None)
        while pending:
            remaining = []
            for row in pending:
                try:
                    value = self.evaluate(row['expression'], state, row['target'].rsplit('::', 1)[0], strict=True)
                except MissingReference:
                    remaining.append(row); continue
                state[row['target']] = value
            if len(remaining) == len(pending):
                raise ValueError('unresolved or cyclic initial values: ' + ', '.join(r['target'] for r in remaining))
            pending = remaining
        self.solve_constraints(config, data['constraints'])

    def step(self, config, *, action=None, overrides=None):
        """One equation, including its selected continuation and all effects."""
        node = self.graph['nodes'][config.node]
        op, data, edges = node['operation'], node['data'], node['successors']
        selected = 'next'
        state = config.state
        if op == 'initialize_existing_runtime':
            self.initialize(config, data, overrides or {})
        elif op == 'enter_block':
            if data['fresh_locals']: config.local_items = {}
            for name, kind in data['declarations'].items():
                config.local_items[name] = {'type': kind, 'attrs': {}}
        elif op in ('no_op', 'perform', 'begin_cycle'):
            pass
        elif op in ('assign', 'declare_attribute'):
            if data['expression'] is not None:
                value = self.evaluate(data['expression'], state, data['context'])
                if value is not None:
                    target = data.get('source_storage_target', data['target'])
                    if op == 'declare_attribute':
                        state[target] = value
                    else:
                        path = data['source_target']
                        if path[0] in config.local_items:
                            config.local_items[path[0]]['attrs']['.'.join(path[1:])] = value
                        else:
                            target = self.canonical(state, target)
                            capacity = target.rsplit('::', 1)[0] + '::capacity'
                            parameters = self.graph['nodes']['initial/entry']['data']['parameters']
                            if any(p['qualified_name'] == capacity and p['value'] is not None for p in parameters):
                                raise ValueError('implicit capacity clamp is not a source assignment')
                        target = self.canonical(state, target)
                        if isinstance(state.get(target), Binding):
                            raise ValueError('assignment to bound feature ' + target)
                        state[target] = value
        elif op in ('branch', 'machine_guard'):
            value = self.evaluate(data['condition'], state, data.get('context', ''))
            if op == 'branch' and value_kind(value) is not bool: raise ValueError('source action guard is not Boolean')
            selected = 'true' if value else 'false'
        elif op == 'send_copy':
            item = config.local_items.get(data['payload']); destination = data['destination']
            selected = 'absent'
            if item is not None and destination:
                box = config.mailboxes.setdefault(destination, [])
                message = {'type': item['type'], 'attrs': dict(item['attrs'])}
                index = next((i for i, old in enumerate(box) if old['type'] == item['type']), None)
                if index is None: box.append(message)
                else: box[index] = message
                selected = 'sent'
        elif op == 'accept_copy':
            port = data['port']; box = config.mailboxes.get(port, [])
            if not box: box = config.mailboxes.get(port.rsplit('::', 1)[0], [])
            index = next((i for i, item in enumerate(box) if self.subtype(item['type'], data['expected_type'])), None)
            if index is None:
                raise ValueError(f"blocked source accept: {port} expects {data['expected_type']}")
            item = box[index]
            if data['destination']:
                config.local_items[data['destination']] = item
                for attr, value in item['attrs'].items():
                    state[data['context'] + '::' + data['destination'] + '::' + attr.replace('.', '::')] = value
            box.pop(index); selected = 'accepted'
        elif op == 'call_machine':
            config.stack.append((edges['return'], config.local_items, config.machine_frame))
            selected = 'call'
        elif op == 'enter_machine':
            config.machine_frame = {'current': config.machines.get(data['instance']), 'matched': None, 'port': None}
        elif op == 'machine_from_state':
            selected = 'true' if config.machine_frame['current'] == data['expected'] else 'false'
        elif op == 'match_trigger':
            selected = 'matched'; config.machine_frame['matched'] = None
            if data['type']:
                if data['port']:
                    port = data['instance'] + '::' + data['port'].replace('.', '::')
                    item = next((m for m in config.mailboxes.get(port, []) if self.subtype(m['type'], data['type'])), None)
                    config.machine_frame.update(matched=item, port=port)
                    if item is None: selected = 'absent'
                    elif data['destination']:
                        for attr, value in item['attrs'].items():
                            state[data['instance'] + '::' + data['destination'] + '::' + attr.replace('.', '::')] = value
                else:
                    key = data['instance'] + '::' + data['type']
                    if not state.get(key, False): selected = 'absent'
                    else: state[key] = False
        elif op == 'finish_machine_transition':
            item = config.machine_frame['matched']
            if item is not None:
                box = config.mailboxes.get(config.machine_frame['port'], [])
                if item in box: box.remove(item)
            config.machines[data['instance']] = data['to']
        elif op == 'return':
            config.node, config.local_items, config.machine_frame = config.stack.pop()
            return
        elif op == 'solve_source_constraints':
            self.solve_constraints(config, data)
        elif op == 'set_dt':
            state['dt'] = self.dt
        elif op == 'advance_engine_time':
            config.engine_time += self.dt
        elif op == 'check_all_requirements':
            statuses = {}
            for name in data['properties']:
                tags = self.properties[name]['tags']
                entry = {'kind': sorted(set(tags) & {'Prohibition', 'Obligation'})[0],
                         'metadata': tags, 'status': None, 'error': None}
                try:
                    value = self.evaluate(data['expressions'][name], state, strict=True)
                    if value_kind(value) is not bool: raise ValueError('source requirement result is not Boolean')
                    entry['status'] = value
                except (ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
                    entry['error'] = str(exc)
                statuses[name] = entry
            config.events.append({'sequence': config.sequence, 'boundary': data['boundary'],
                                  'engine_time': config.engine_time, 'statuses': statuses})
            config.sequence += 1
        elif op == 'decision':
            inputs = {key: self.evaluate(expr, state, data['context']) for key, expr in data['inputs'].items()}
            if set(inputs) != set(data['expected_inputs']) or any(value is None for value in inputs.values()):
                raise ValueError('missing, extra or undefined source Neural input')
            if value_kind(inputs[data['completion_input']]) is not bool:
                raise ValueError('source Completion input is not Boolean')
            config.inputs = inputs; config.pending_completion = inputs[data['completion_input']]
            config.outcome = 'decision'; return
        elif op == 'apply_executed_action':
            if action is None: raise ValueError('missing executed controller action')
            for name, target in data['outputs'].items():
                if name in action: state[target] = action[name]
        elif op == 'completion_test':
            selected = 'true' if config.pending_completion else 'false'
        elif op == 'outcome':
            config.outcome = 'error' if data['outcome'] == 'execution_error' else data['outcome']; return
        else:
            raise ValueError('missing operation equation: ' + op)
        config.node = edges[selected]

    def advance(self, current=None, *, action=None, overrides=None, observe=None):
        """Exact decision-boundary evaluation; no fixed scan count or path sample."""
        config = deepcopy(current) if current is not None else Configuration(self.graph['initial_entry'])
        config.events = []; config.error = None; config.outcome = None
        if current is not None:
            if current.outcome != 'decision': raise ValueError('action requires a pending decision')
            config.node = self.graph['nodes'][current.node]['successors']['resume']
        while config.outcome is None:
            identity = config.node
            try:
                self.step(config, action=action, overrides=overrides)
            except Exception as exc:
                config.error = f'{type(exc).__name__}: {exc}'
                config.node = self.graph['nodes'][identity]['on_exception']
            if observe is not None: observe(identity, config)
        return config

    def region(self, current, *, action=None, overrides=None):
        """Execute to the next source cut point, retaining cyclic continuations.

        Splitting at machine calls and cycle entries bounds the source region,
        not the number of cycles in a decision transition. An edge returning to
        a cut point remains an ordinary edge in the compiled equation program.
        """
        config = deepcopy(current)
        config.outcome = None; config.error = None
        cutpoints = {self.graph['cycle_entry']}
        cutpoints.update(node for node, record in self.graph['nodes'].items()
                         if record['operation'] in ('enter_machine', 'branch', 'machine_guard',
                             'solve_source_constraints', 'check_all_requirements', 'completion_test'))
        visited = []
        while config.outcome is None:
            if visited and (config.node in cutpoints or config.node in visited):
                break
            identity = config.node; visited.append(identity)
            try:
                self.step(config, action=action, overrides=overrides)
            except Exception as exc:
                config.error = f'{type(exc).__name__}: {exc}'
                config.node = self.graph['nodes'][identity]['on_exception']
        return {'configuration': config, 'source_nodes': visited}
