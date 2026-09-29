"""Guarded, typed equations for finite regions of the source execution graph.

A region ends at a source cut point, not after a chosen number of scans. Its
successor configuration is an explicit argument of the next region. Numeric
fields use native Python kinds (Boolean, Integer and IEEE binary64); object
sharing is retained because Python container equality uses identity for NaNs.
"""
from __future__ import annotations

from dataclasses import fields
import hashlib
import json

import z3

from .execution_equations import Alias, Binding, Configuration, ExecutionEquations
from .symbolic_execution import SymbolicValue, guarded_evaluations


class ConfigurationShape:
    """Separate configuration layout/object sharing from all scalar values."""
    def __init__(self, configuration):
        self.values = []
        self.locations = []
        memo = {}
        def encode(value, path):
            kind = value.python_kind if isinstance(value, SymbolicValue) else type(value)
            if kind in (bool, int, float):
                # Only float identity can affect equality of scalar containers.
                # A symbolic scalar is immutable and has the source copy identity.
                identity = id(value) if kind is float else None
                if identity is not None and identity in memo:
                    return {'scalar_ref': memo[identity]}
                index = len(self.values)
                if identity is not None: memo[identity] = index
                self.values.append(value); self.locations.append(path)
                return {'scalar': index, 'kind': kind.__name__}
            if value is None or isinstance(value, str): return {'literal': value}
            if isinstance(value, Alias): return {'alias': value.target}
            if isinstance(value, Binding): return {'binding': [value.expression, value.context]}
            identity = id(value)
            key = ('container', identity)
            if key in memo: return {'object_ref': memo[key]}
            index = sum(isinstance(k, tuple) for k in memo)
            memo[key] = index
            if isinstance(value, dict):
                return {'object': index, 'dict': [[k, encode(v, (*path, k))] for k, v in value.items()]}
            if isinstance(value, (tuple, list)):
                return {'object': index, 'tuple' if isinstance(value, tuple) else 'list':
                        [encode(v, (*path, i)) for i, v in enumerate(value)]}
            if isinstance(value, Configuration):
                return {'object': index, 'configuration': [[f.name, encode(getattr(value, f.name), (*path, f.name))]
                                                          for f in fields(value)]}
            raise TypeError('unsupported configuration value ' + type(value).__name__)
        self.layout = encode(configuration, ())
        self.sha256 = hashlib.sha256(json.dumps(self.layout, sort_keys=True).encode()).hexdigest()

    def instantiate(self, context, prefix='input'):
        scalars, objects = {}, {}
        def decode(value):
            if 'scalar_ref' in value: return scalars[value['scalar_ref']]
            if 'scalar' in value:
                i = value['scalar']; result = context.variable(f'{prefix}_{i}', {'bool': bool, 'int': int, 'float': float}[value['kind']])
                scalars[i] = result; return result
            if 'literal' in value: return value['literal']
            if 'alias' in value: return Alias(value['alias'])
            if 'binding' in value: return Binding(*value['binding'])
            if 'object_ref' in value: return objects[value['object_ref']]
            identity = value['object']
            if 'configuration' in value:
                result = Configuration(''); objects[identity] = result
                for k, v in value['configuration']: setattr(result, k, decode(v))
            elif 'dict' in value:
                result = {}; objects[identity] = result
                for k, v in value['dict']: result[k] = decode(v)
            else:
                result = []; objects[identity] = result
                result.extend(decode(v) for v in value.get('list', value.get('tuple', [])))
                if 'tuple' in value: result = tuple(result); objects[identity] = result
            return result
        return decode(self.layout), scalars


def scalar_expression(value):
    if isinstance(value, SymbolicValue): return value.expression
    if type(value) is bool: return z3.BoolVal(value)
    if type(value) is int: return z3.IntVal(value)
    if type(value) is float: return z3.FPVal(value, z3.Float64())
    raise TypeError('not a source scalar')


def value_equality(left, right):
    """SMT value identity, including float sign and NaN category.

    This is deliberately stronger than a source ``==`` predicate. In particular,
    +0.0 and -0.0 must not be equated before subsequent arithmetic.
    """
    a, b = scalar_expression(left), scalar_expression(right)
    return z3.BoolVal(False) if a.sort() != b.sort() else a == b


def compile_region(execution, current, *, dt, timeout_ms=1000, prefix='input', constraints=()):
    """Generate every feasible guarded update of the supplied input layout.

    Scalar values in ``current`` select kinds and sharing only, never guards.
    Caller-provided constraints must be established separately. UNKNOWN paths
    are returned, not discarded. No source cycles are unrolled here.
    """
    shape = ConfigurationShape(current)
    evaluator = ExecutionEquations(execution, dt)
    def run(context):
        config, inputs = shape.instantiate(context, prefix)
        actions = {name: context.variable('action_' + name, {'Boolean': bool, 'Integer': int, 'Real': float}[node['data']['output_types'][name]])
                   for node in evaluator.graph['nodes'].values()
                   if node['operation'] == 'apply_executed_action'
                   for name in node['data']['outputs']}
        overrides = {}
        scenario_constraints = []
        if config.node == evaluator.graph['initial_entry']:
            profile = evaluator.graph.get('scenario_profile', {})
            if profile.get('error'):
                raise ValueError('unsupported source scenario profile: ' + profile['error'])
            if profile.get('bounds') and context.choose(z3.Bool(prefix + '_sampled_scenario')):
                for key, bounds in profile['bounds'].items():
                    lo, hi = bounds['lower'], bounds['upper']
                    if lo == hi:
                        overrides[key] = lo
                    else:
                        kind = int if type(lo) is int and type(hi) is int else float
                        value = context.variable(prefix + '_scenario_' + key, kind)
                        overrides[key] = value
                        scenario_constraints.extend([(value >= lo).expression, (value <= hi).expression])
                for target, source in profile.get('state_bindings', []):
                    overrides[target] = overrides[source]
        value = evaluator.region(config, action=actions, overrides=overrides)
        value['scenario_constraints'] = scenario_constraints
        value['input_values'] = inputs; value['actions'] = actions
        return value
    # Infeasible arithmetic branches may stay in the relation with their exact
    # guards. Solving them separately is unnecessary and duplicates the final
    # proof query. Syntactically false guards are still discarded.
    for branch in guarded_evaluations(run, constraints=constraints, timeout_ms=timeout_ms,
                                      check_feasibility=False):
        branch['input_shape'] = shape
        if branch['status'] == 'equation':
            branch['guards'] = (*branch['guards'], *branch['value']['scenario_constraints'])
            branch['output_shape'] = ConfigurationShape(branch['value']['configuration'])
        yield branch


def branch_record(branch):
    """Serializable equation evidence, including exceptional/UNKNOWN exits."""
    result = {'status': branch['status'], 'guards': [g.sexpr() for g in branch['guards']],
              'input_shape': branch['input_shape'].layout}
    if branch['status'] != 'equation':
        return dict(result, **{k: branch[k] for k in ('reason', 'error') if k in branch})
    value, shape = branch['value'], branch['output_shape']
    result.update(source_nodes=value['source_nodes'], output_shape=shape.layout,
                  output_values=[scalar_expression(v).sexpr() for v in shape.values],
                  input_locations=[list(p) for p in branch['input_shape'].locations],
                  input_symbols={str(i): v.expression.sexpr() for i, v in value['input_values'].items()},
                  input_sorts={str(i): v.expression.sort().sexpr() for i, v in value['input_values'].items()},
                  action_symbols={k: v.expression.sexpr() for k, v in value['actions'].items()})
    return result


def compile_program(execution, *, dt, timeout_ms=1000, initial=None, progress=None):
    """Close the set of configuration layouts under source region equations.

    Values are generalized at every region, so the returned program is an
    overapproximation of reachable configurations, not a collection of sampled
    executions. Repeated layouts become edges, including arbitrarily many scans.
    An unresolved branch makes the entire program incomplete.
    """
    from collections import deque
    from copy import deepcopy
    evaluator = ExecutionEquations(execution, dt)
    # An acyclic source call graph has a source-derived finite stack bound.
    # Refuse recursive dispatch rather than guessing an unfolding depth.
    calls = {}
    for entry, node in evaluator.graph['nodes'].items():
        if node['operation'] != 'enter_machine': continue
        todo, seen, targets = [entry], set(), set()
        while todo:
            identity = todo.pop()
            if identity in seen: continue
            seen.add(identity); record = evaluator.graph['nodes'][identity]
            if record['operation'] in ('return', 'outcome'): continue
            if record['operation'] == 'call_machine':
                targets.add(record['successors']['call'])
                todo.append(record['successors']['return'])
            else: todo.extend(record['successors'].values())
        calls[entry] = targets
    def acyclic(entry, active=(), done=None):
        if entry in active:
            raise ValueError('recursive source machine dispatch cannot use a finite configuration layout: ' + entry)
        done = set() if done is None else done
        if entry in done: return
        for child in calls.get(entry, ()):
            acyclic(child, (*active, entry), done)
        done.add(entry)
    for entry in calls: acyclic(entry)
    pending = deque([initial or Configuration(evaluator.graph['initial_entry'])])
    shapes, edges, unresolved = {}, [], []
    while pending:
        current = pending.popleft()
        shape = ConfigurationShape(current)
        if shape.sha256 in shapes: continue
        shapes[shape.sha256] = shape
        for branch in compile_region(execution, current, dt=dt, timeout_ms=timeout_ms,
                                     prefix='s' + shape.sha256):
            if branch['status'] != 'equation':
                unresolved.append(branch_record(branch)); continue
            destination = deepcopy(branch['value']['configuration'])
            emitted = destination.events
            destination.events = []
            branch['emitted_events'] = emitted
            if destination.outcome == 'decision':
                # The pending decision is a boundary; its outgoing edge reads a
                # new executed action, and completion remains latched.
                destination.node = evaluator.graph['nodes'][destination.node]['successors']['resume']
                destination.outcome = None
            if destination.outcome not in ('terminal', 'error'):
                next_shape = ConfigurationShape(destination)
                branch['next_shape_sha256'] = next_shape.sha256
                pending.append(destination)
            else:
                branch['next_shape_sha256'] = None
            branch['entry_shape_sha256'] = shape.sha256
            branch['continuation'] = destination
            edges.append(branch)
        if progress is not None: progress(len(shapes), len(edges), len(pending), len(unresolved))
    return {'status': 'complete' if not unresolved else 'unknown',
            'graph_sha256': evaluator.graph['sha256'], 'shapes': shapes,
            'edges': edges, 'unresolved': unresolved, 'dt': dt,
            'scope': 'all numeric configurations of the initialized source layouts'}
