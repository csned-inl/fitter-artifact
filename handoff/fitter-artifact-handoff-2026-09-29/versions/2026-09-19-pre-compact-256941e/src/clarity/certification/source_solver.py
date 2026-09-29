"""Recursive solver relation for the source-owned execution equations.

Every region edge carries real arithmetic/message updates. Recursive Horn rules
compose those edges through arbitrarily many cycles. No guessed scan calendar
or substituted physical reading supplies a missing sensor update.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from functools import lru_cache
from pathlib import Path

import z3

from .execution_equations import Configuration, ExecutionEquations
from .symbolic_execution import SymbolicContext
from .transition_equations import (ConfigurationShape, compile_program,
                                   scalar_expression, value_equality)


_Value = z3.Datatype('ClaritySourceValue')
_Value.declare('Absent')
_Value.declare('BooleanValue', ('boolean_value', z3.BoolSort()))
_Value.declare('IntegerValue', ('integer_value', z3.IntSort()))
_Value.declare('FloatValue', ('float_value', z3.Float64()))
_Value.declare('TextValue', ('text_value', z3.IntSort()))
_Value = _Value.create()
_Values = z3.Datatype('ClaritySourceValues')
_Values.declare('Empty')
_Values.declare('Cons', ('head_value', _Value), ('tail_values', _Values))
_Values = _Values.create()


def _progress(stage, **values):
    if os.environ.get('CLARITY_SOURCE_SOLVER_PROGRESS'):
        print(json.dumps({'source_solver': stage, **values}), flush=True)


def tagged(value):
    if value is None: return _Value.Absent
    if isinstance(value, str):
        # Injective encoding of finite source labels; no string-theory solver
        # or probabilistic hash is needed for equality of these labels.
        return _Value.TextValue(z3.IntVal(int.from_bytes(b'\x01' + value.encode('utf-8'), 'big')))
    expression = scalar_expression(value)
    constructor = {'Bool': _Value.BooleanValue, 'Int': _Value.IntegerValue,
                   '(_ FloatingPoint 11 53)': _Value.FloatValue}[expression.sort().sexpr()]
    return constructor(expression)


def sequence(items, tail=None):
    result = _Values.Empty if tail is None else tail
    for item in reversed(items): result = _Values.Cons(tagged(item), result)
    return result


def property_outputs(events):
    """Retain each source property's result/error at every original boundary."""
    result = []
    for event in events:
        result.append(event['boundary'])
        for name, row in sorted(event['statuses'].items()):
            result.extend((name, row['status'], row['error']))
    return result


class SourceTransitionQuery:
    def __init__(self, model, q, program):
        self.model, self.q, self.program = model, sorted(q), program
        self.evaluator = ExecutionEquations(model.execution, program['dt'])
        self.graph = self.evaluator.graph
        self.fixedpoint = z3.Fixedpoint()
        self.fixedpoint.set(engine='spacer')
        self.variables = {}
        self.rules = 0
        self.action_types = {name: {'Boolean': bool, 'Integer': int, 'Real': float}[kind]
                             for node in self.graph['nodes'].values()
                             if node['operation'] == 'apply_executed_action'
                             for name, kind in node['data']['output_types'].items()}
        self.action_names = sorted(self.action_types)
        context = SymbolicContext()
        self.actions = [context.variable('executed_' + name, self.action_types[name]).expression
                        for name in self.action_names]
        self.next_actions = [context.variable('next_executed_' + name, self.action_types[name]).expression
                             for name in self.action_names]
        self.input_values, self.input_configs = {}, {}
        self.transition, self.reachable = {}, {}
        self.boundaries = {}
        for identity, shape in program['shapes'].items():
            config, values = shape.instantiate(context, 's' + identity)
            inputs = [values[i].expression for i in range(len(values))]
            self.input_values[identity] = inputs
            self.input_configs[identity] = config
            types = [v.sort() for v in inputs + self.actions]
            self.transition[identity] = z3.Function('transition_' + identity,
                *types, _Values, _Values, z3.BoolSort())
            self.reachable[identity] = z3.Function('reachable_' + identity, *types, z3.BoolSort())
            self.fixedpoint.register_relation(self.transition[identity], self.reachable[identity])
        self.bad = z3.Function('different_source_results', z3.BoolSort())
        self.fixedpoint.register_relation(self.bad)

    def rule(self, head, *body):
        formula = z3.Implies(z3.And(*body), head) if body else head
        variables, pending, seen = [], [formula], set()
        while pending:
            term = pending.pop()
            if term.get_id() in seen: continue
            seen.add(term.get_id())
            if z3.is_const(term) and term.decl().kind() == z3.Z3_OP_UNINTERPRETED:
                # A nullary relation is a predicate, not a quantified input.
                if term.decl() != self.bad: variables.append(term)
            else: pending.extend(term.children())
        # Quantify only variables used by this rule. Fixedpoint.declare_var
        # would quantify every variable accumulated from every other layout.
        self.variables.update((v.get_id(), v) for v in variables)
        self.fixedpoint.add_rule(z3.ForAll(variables, formula) if variables else formula)
        self.rules += 1

    def q_values(self, config):
        result = []
        for name in self.q:
            entries = self.model.value_semantics['decision_updates'].get(name, [])
            if len(entries) != 1:
                raise ValueError('no unique source storage for q variable ' + name)
            key = entries[0]['runtime_key']
            if key.startswith('$machine:'):
                value = config.machines.get(key[len('$machine:'):])
            else:
                value = self.evaluator.read(config.state, key)
            if value is None: raise ValueError('q storage is undefined at decision: ' + key)
            result.append(value)
        return result

    def outputs(self, config):
        if config.outcome == 'error': return sequence(['error', config.error])
        return sequence([config.outcome, *self.q_values(config),
                         *[config.inputs[name] for name in sorted(config.inputs)]])

    def acyclic_projection(self, timeout_ms, include_artifacts=False):
        """Eliminate internal region calls when their graph is acyclic.

        This proves a stronger, unrestricted configuration claim. A SAT result
        is passed to the reachable-state query; it is not a runtime allegation.
        """
        if not structural_progress(self.program): return {'status': 'cyclic'}
        by_entry = {key: [] for key in self.program['shapes']}
        for edge in self.program['edges']:
            by_entry[edge['entry_shape_sha256']].append(edge)
            if edge['value']['configuration'].outcome == 'decision':
                self.boundaries[edge['next_shape_sha256']] = edge['value']['configuration'].node
        if not self.boundaries: return {'status': 'no_decision'}
        actions = [(SymbolicContext().variable('action_' + name, self.action_types[name]).expression, value)
                   for name, value in zip(self.action_names, self.actions)]
        summaries = {}
        def summary(key):
            if key in summaries: return summaries[key]
            output, events = sequence(['uncovered_source_guard']), _Values.Empty
            for edge in reversed(by_entry[key]):
                config = edge['value']['configuration']
                if config.outcome in ('decision', 'terminal', 'error'):
                    value = self.outputs(config)
                    event_value = sequence(property_outputs(edge['emitted_events']))
                else:
                    target = edge['next_shape_sha256']
                    value, tail = summary(target)
                    values = [scalar_expression(v) for v in ConfigurationShape(edge['continuation']).values]
                    substitutes = list(zip(self.input_values[target], values))
                    value = z3.substitute(value, *substitutes)
                    tail = z3.substitute(tail, *substitutes)
                    event_value = sequence(property_outputs(edge['emitted_events']), tail)
                guard = z3.simplify(z3.And(*edge['guards']))
                output = z3.If(guard, value, output)
                events = z3.If(guard, event_value, events)
            summaries[key] = (z3.simplify(z3.substitute(output, *actions)),
                              z3.simplify(z3.substitute(events, *actions)))
            return summaries[key]
        checked, artifacts = 0, []
        parameters = self.graph['nodes'][self.graph['initial_entry']]['data']['parameters']
        constants = [p['qualified_name'] for p in parameters if all(
            s == self.graph['initial_entry'] for s in self.graph['storage'].get(p['qualified_name'], {}).get('writers', []))]
        for left in self.boundaries:
            a = self.input_configs[left]; out1, events1 = summary(left)
            for right in self.boundaries:
                b, values = self.program['shapes'][right].instantiate(SymbolicContext(), 'right_' + right)
                substitutes = [(old, values[i].expression) for i, old in enumerate(self.input_values[right])]
                out2, events2 = [z3.substitute(v, *substitutes) for v in summary(right)]
                equalities = [tagged(x) == tagged(y) for x, y in zip(self.q_values(a), self.q_values(b))]
                equalities += [tagged(self.evaluator.read(a.state,k)) == tagged(self.evaluator.read(b.state,k)) for k in constants]
                solver = z3.Solver(); solver.set(timeout=timeout_ms)
                solver.add(*equalities, z3.Or(out1 != out2, events1 != events2))
                result = solver.check(); checked += 1
                if include_artifacts: artifacts.append(solver.to_smt2())
                if result != z3.unsat:
                    return {'status': str(result), 'queries': checked,
                            'reason': solver.reason_unknown() if result == z3.unknown else 'unrestricted_configuration_counterexample'}
        return {'status': 'unsat', 'queries': checked,
                **({'artifacts': artifacts} if include_artifacts else {})}

    def construct(self):
        root = Configuration(self.graph['initial_entry'])
        root_shape = ConfigurationShape(root)
        root_values = [scalar_expression(v) for v in root_shape.values]
        self.rule(self.reachable[root_shape.sha256](*root_values, *self.actions))
        out = z3.Const('final_source_values', _Values)
        event_tail = z3.Const('following_requirement_results', _Values)
        action_symbols = {name: SymbolicContext().variable('action_' + name, kind).expression
                          for name, kind in self.action_types.items()}
        replace_actions = [(action_symbols[name], value) for name, value in zip(self.action_names, self.actions)]
        for branch in self.program['edges']:
            identity = branch['entry_shape_sha256']
            inputs = self.input_values[identity]
            config = branch['value']['configuration']
            guards = [z3.substitute(g, *replace_actions) for g in branch['guards']]
            effects = ConfigurationShape(branch['continuation'])
            next_values = [z3.substitute(scalar_expression(v), *replace_actions) for v in effects.values]
            emitted = sequence(property_outputs(branch['emitted_events']), event_tail)
            emitted = z3.substitute(emitted, *replace_actions)
            reachable = self.reachable[identity](*inputs, *self.actions)
            target = branch['next_shape_sha256']
            if config.outcome in ('decision', 'terminal', 'error'):
                output = z3.substitute(self.outputs(config), *replace_actions)
                events = z3.substitute(sequence(property_outputs(branch['emitted_events'])), *replace_actions)
                self.rule(self.transition[identity](*inputs, *self.actions, output, events), *guards)
            else:
                self.rule(self.transition[identity](*inputs, *self.actions, out, emitted), *guards,
                          self.transition[target](*next_values, *self.actions, out, event_tail))
            if target is not None:
                actions = self.next_actions if config.outcome == 'decision' else self.actions
                self.rule(self.reachable[target](*next_values, *actions), reachable, *guards)
                if config.outcome == 'decision':
                    self.boundaries[target] = config.node
            if self.rules % 2000 == 0: _progress('rules', count=self.rules)
        # A fresh variable per run permits independently reachable histories.
        # The action being tested is shared; all mutable source values remain
        # separate until the explicit equal-q/constant constraints relate them.
        out1, out2 = z3.Consts('result_left result_right', _Values)
        events1, events2 = z3.Consts('events_left events_right', _Values)
        constant_keys = [p['qualified_name'] for p in self.graph['nodes'][self.graph['initial_entry']]['data']['parameters']
                         if all(site == self.graph['initial_entry'] for site in self.graph['storage'].get(p['qualified_name'], {}).get('writers', []))]
        for left in self.boundaries:
            a = self.input_configs[left]
            for right in self.boundaries:
                b, bvalues = self.program['shapes'][right].instantiate(SymbolicContext(), 'right_' + right)
                bargs = [bvalues[i].expression for i in range(len(bvalues))]
                constraints = [tagged(x) == tagged(y) for x, y in zip(self.q_values(a), self.q_values(b))]
                for key in constant_keys:
                    constraints.append(tagged(self.evaluator.read(a.state, key)) == tagged(self.evaluator.read(b.state, key)))
                self.rule(self.bad(),
                    self.reachable[left](*self.input_values[left], *self.actions),
                    self.reachable[right](*bargs, *self.actions),
                    self.transition[left](*self.input_values[left], *self.actions, out1, events1),
                    self.transition[right](*bargs, *self.actions, out2, events2),
                    *constraints, z3.Or(out1 != out2, events1 != events2))
        return self


def implementation_identity():
    root = Path(__file__).parent
    return {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in
            ('execution_equations.py', 'symbolic_execution.py', 'transition_equations.py', 'source_solver.py')}


@lru_cache(maxsize=1)
def _compiled_source(execution_json, dt, implementation_json):
    # The cache contains equations, never a solver verdict. Source extraction
    # and solver queries are repeated by the certificate checker.
    return compile_program(json.loads(execution_json), dt=dt,
        progress=lambda shapes, edges, pending, unresolved: _progress(
            'compile', shapes=shapes, regions=edges, pending=pending,
            unresolved=unresolved) if shapes % 200 == 0 else None)


def structural_progress(program):
    """Acyclic internal region edges prove reaching a boundary in finite steps."""
    edges = {key: set() for key in program['shapes']}
    for branch in program['edges']:
        if branch['value']['configuration'].outcome is None:
            edges[branch['entry_shape_sha256']].add(branch['next_shape_sha256'])
    indegree = {key: 0 for key in edges}
    for children in edges.values():
        for key in children: indegree[key] += 1
    pending = [key for key, degree in indegree.items() if not degree]
    visited = 0
    while pending:
        key = pending.pop(); visited += 1
        for child in edges[key]:
            indegree[child] -= 1
            if not indegree[child]: pending.append(child)
    return visited == len(edges)


def check_source_transition_closure(model, q, *, dt, timeout_ms=1000, include_artifacts=False):
    evidence = {'solver': 'z3', 'logic': 'HORN+FP64+Int+datatypes', 'timeout_ms': timeout_ms,
                'claim': 'not_claimed_by_this_artifact', 'graph_sha256': model.execution['decision_transition']['sha256'],
                'implementation_sha256': implementation_identity(),
                'visible_terms_checked': ['next_q', 'next_neural_inputs', 'outcome',
                                          'every_completed_boundary_requirement_status_and_error'],
                'initialization_scope': 'source defaults and source-bounded scenario inputs; random-generator support overapproximated',
                'q_size': len(q), 'actions_size': len(model.actions)}
    try:
        started = time.monotonic()
        program = _compiled_source(json.dumps(model.execution), dt,
                                   json.dumps(evidence['implementation_sha256'], sort_keys=True))
        _progress('compiled', seconds=round(time.monotonic()-started,3))
        evidence['equations'] = {'status': program['status'], 'configuration_layouts': len(program['shapes']),
                                 'guarded_regions': len(program['edges']),
                                 'unresolved': program['unresolved']}
        if program['status'] != 'complete':
            return dict(evidence, status='unknown', reason='source_equation_compilation_incomplete')
        query = SourceTransitionQuery(model, q, program)
        direct = query.acyclic_projection(timeout_ms, include_artifacts)
        evidence['acyclic_projection'] = direct
        if direct['status'] == 'unsat':
            return dict(evidence, status='discharged', claim='one_step_transition_closure',
                        finite_transition_uniqueness='discharged', progress='acyclic_internal_region_graph')
        query.construct()
        _progress('query_ready', rules=query.rules, seconds=round(time.monotonic()-started,3))
        evidence['equations']['horn_rules'] = query.rules
        evidence['equations']['decision_layouts'] = len(query.boundaries)
        query.fixedpoint.set(timeout=timeout_ms)
        if include_artifacts:
            evidence['artifacts'] = {'smt2': query.fixedpoint.to_string([query.bad()])}
        result = query.fixedpoint.query(query.bad())
        evidence['z3_check_sat'] = str(result)
        if result == z3.sat:
            # The initial random-generator support is deliberately an
            # overapproximation. A trace must be replayed before alleging an
            # actual runtime counterexample.
            return dict(evidence, status='unknown', reason='source_relation_has_counterexample_requiring_runtime_replay',
                        source_counterexample=str(query.fixedpoint.get_answer()))
        if result == z3.unknown:
            reason = query.fixedpoint.reason_unknown()
            return dict(evidence, status='unknown', reason=reason if reason != 'ok' else 'solver_returned_unknown')
        # UNSAT establishes uniqueness of finite completed transitions. An
        # independent progress proof is still required for cyclic programs;
        # absence of a result must not make a divergent path vacuously safe.
        if structural_progress(program):
            return dict(evidence, status='discharged', claim='one_step_transition_closure',
                        finite_transition_uniqueness='discharged', progress='acyclic_internal_region_graph')
        return dict(evidence, status='unknown', reason='finite_transition_uniqueness_proved_progress_not_discharged',
                    finite_transition_uniqueness='discharged')
    except Exception as exc:
        if isinstance(exc, z3.Z3Exception) and 'canceled' in str(exc):
            return dict(evidence, status='unknown', reason='solver_timeout')
        return dict(evidence, status='unknown', reason='source_solver_error',
                    error=f'{type(exc).__name__}: {exc}')
