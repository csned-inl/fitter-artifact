"""Transition composition, mutation rejection, and unchanged-runtime comparisons.

The runtime comparisons exercise executions. The separate source walk checks
structural coverage, including branches that those executions do not take.
Neither is substituted for the Stage 3 solver obligation.
"""
from collections import Counter
from copy import deepcopy
import json
import inspect
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from clarity.certification.decision_transition import Composer, compose_path, validate_decision_transition
from clarity.certification.ordered_execution import build_execution_description, expression_record, fingerprint
from clarity.certification.strict_extract import extract_equation_model
from clarity.certification.certificate import build_certificate_for_path, check_certificate
from clarity.models import models_root
from clarity.runtime.env import SysMLEnv
from clarity.runtime.shield import SpecShield
from clarity.sysml import parser as ast
from clarity.sysml.simulator import SimulationEngine, ConstraintSolver, ExpressionEvaluator, resolve_value, canonical_key


NAMES = ('thermostat', 'cruise-controller-model', 'mixing-sysml-model')


def load(name):
    parser = ast.SysMLParser(str(models_root() / name / 'model.sysml'))
    parser.parse()
    inventory = build_execution_description(parser)
    return parser, inventory, inventory.decision_transition


def source_kinds(parser):
    """Walk the parsed source independently of the inventory's lowering."""
    result = Counter()
    def body(context, statements):
        for stmt in statements:
            result['Decision' if isinstance(stmt, ast.SubactionCallStmt) else type(stmt).__name__] += 1
            if isinstance(stmt, ast.IfStmt):
                body(context, stmt.body)
                body(context, stmt.else_body)
            elif isinstance(stmt, ast.PerformStmt):
                part = parser.part_defs[parser.part_instances[context].part_type]
                action = next(a for a in part.actions if a.name == stmt.action_name)
                body(context, action.body)
    for context, statements in parser.step_action_bodies:
        body(context, statements)
    for context, machine in parser.instance_state_machines.items():
        for transition in machine.transitions:
            body(context, transition.do_action or [])
    return result


def signature(kind, context, data):
    fields = {'AssignStmt': ('source_target', 'expression'), 'IfStmt': ('condition',),
              'PerformStmt': ('action',), 'SendStmt': ('payload', 'port'),
              'AcceptStmt': ('destination', 'type', 'port'),
              'Decision': ('name', 'type', 'inputs'),
              'AttributeDeclStmt': ('name', 'type', 'expression'),
              'ItemDeclStmt': ('name', 'type'), 'InParamStmt': ('name', 'type'),
              'OutParamStmt': ('name', 'type')}
    return kind, context, json.dumps({key: data[key] for key in fields[kind]}, sort_keys=True)


def statement_signature(statement, context):
    if isinstance(statement, ast.AssignStmt):
        data = dict(source_target=statement.target, expression=expression_record(statement.expr))
    elif isinstance(statement, ast.IfStmt):
        data = dict(condition=expression_record(statement.condition))
    elif isinstance(statement, ast.PerformStmt):
        data = dict(action=statement.action_name)
    elif isinstance(statement, ast.SendStmt):
        data = dict(payload=statement.item_name, port=statement.port)
    elif isinstance(statement, ast.AcceptStmt):
        data = dict(destination=statement.var_name, type=statement.type_name, port=statement.port)
    elif isinstance(statement, ast.SubactionCallStmt):
        return signature('Decision', context, dict(name=statement.name, type=statement.type_name,
                         inputs={b.name: expression_record(b.expr) for b in statement.bindings
                                 if isinstance(b, ast.InputBindingStmt)}))
    elif isinstance(statement, ast.AttributeDeclStmt):
        data = dict(name=statement.name, type=statement.type_name,
                    expression=expression_record(statement.init_expr) if statement.init_expr is not None else None)
    else:
        data = dict(name=statement.name, type=statement.type_name)
    return signature(type(statement).__name__, context, data)


class TraceMatcher:
    """Accept observed runtime events only along graph paths with proper returns.

    No values are executed and no branch is asserted feasible by this matcher.
    Reference/operation tests independently check the values. This observer does
    not change the simulator's scheduler or action execution.
    """
    def __init__(self, graph, inventory):
        self.graph = graph
        self.labels = {}
        def walk(events):
            for event in events:
                self.labels[event['event_id']] = signature(
                    event['kind'], event['context'], event['data'])
                walk(event.get('children', [])); walk(event.get('alternatives', []))
        for program in inventory.programs:
            walk(program['events'])
        for identity, node in graph['nodes'].items():
            op = node['operation']
            if op == 'begin_cycle': self.labels[identity] = ('cycle',)
            elif op == 'call_machine': self.labels[identity] = ('machine', node['data']['instance'])
            elif op == 'return': self.labels[identity] = ('return',)
            elif op == 'solve_source_constraints': self.labels[identity] = ('solve',)
            elif op == 'apply_executed_action': self.labels[identity] = ('response',)
            elif op == 'check_all_requirements': self.labels[identity] = ('check', node['data']['boundary'])
            elif op == 'outcome': self.labels[identity] = (node['data']['outcome'],)
        self.positions = {(graph['cycle_entry'], ())}
        self.started = False
        self.events = 0
        lines, first = inspect.getsourcelines(SimulationEngine._execute_action_stmts)
        self.statement_line = next(first + i for i, line in enumerate(lines)
                                   if line.strip() == 'if isinstance(stmt, AssignStmt):')

    def advance(self, identity, stack):
        node = self.graph['nodes'][identity]
        if node['operation'] == 'return':
            return {(stack[-1], stack[:-1])} if stack else set()
        if node['operation'] == 'call_machine':
            return {(node['successors']['call'], stack + (node['successors']['return'],))}
        return {(target, stack) for target in node['successors'].values()}

    def observe(self, label, selected=None):
        if label == ('cycle',): self.started = True
        if not self.started: return
        pending, seen, result = list(self.positions), set(), set()
        while pending:
            identity, stack = pending.pop()
            if (identity, stack) in seen: continue
            seen.add((identity, stack))
            expected = self.labels.get(identity)
            if expected is not None:
                if label == expected:
                    if selected is not None:
                        result.add((self.graph['nodes'][identity]['successors'][selected], stack))
                    else:
                        result.update(self.advance(identity, stack))
                    if self.graph['nodes'][identity]['operation'] == 'outcome':
                        result.add((identity, stack))
            else:
                pending.extend(self.advance(identity, stack))
        if not result:
            raise AssertionError(f'runtime event has no composed source path: {label}; positions={self.positions}')
        self.positions = result
        self.events += 1

    def trace(self, frame, event, arg):
        name = frame.f_code.co_name
        if event == 'call':
            if name == 'step' and isinstance(frame.f_locals.get('self'), SimulationEngine): self.observe(('cycle',))
            elif name == '_process_state_machine': self.observe(('machine', frame.f_locals['inst_fqn']))
            elif name == 'solve' and isinstance(frame.f_locals.get('self'), ConstraintSolver): self.observe(('solve',))
            elif name == 'record_requirements': self.observe(('check', frame.f_locals['boundary']))
            elif name == '_publish' and frame.f_locals['outcome'] in {'terminal', 'error'}:
                self.observe(('terminal' if frame.f_locals['outcome'] == 'terminal' else 'execution_error',))
        elif event == 'return':
            if name == '_process_state_machine': self.observe(('return',))
            elif name == '_model_fn': self.observe(('response',))
        elif event == 'line' and name == '_execute_action_stmts' and frame.f_lineno == self.statement_line:
            stmt = frame.f_locals['stmt']
            selected = None
            if isinstance(stmt, ast.IfStmt):
                selected = 'true' if frame.f_locals['evaluator'].evaluate(stmt.condition) else 'false'
            elif isinstance(stmt, ast.SendStmt):
                engine = frame.f_locals['self']
                port = frame.f_locals['context'] + '::' + stmt.port.replace('.', '::')
                selected = 'sent' if stmt.item_name in frame.f_locals['local_items'] and engine._find_connected_port(port) else 'absent'
            self.observe(statement_signature(stmt, frame.f_locals['context']), selected)
        return self.trace if name in {'_execute_action_stmts', '_process_state_machine', '_model_fn'} else None


class DescriptionTests(unittest.TestCase):
    def test_all_source_operations_and_all_states_are_covered(self):
        for name in NAMES:
            with self.subTest(model=name):
                parser, inventory, graph = load(name)
                self.assertEqual(Counter(graph['source_events'].values()), source_kinds(parser))
                self.assertEqual(graph['item_type_parents'], parser.item_type_parents)
                self.assertEqual(graph['initial_configuration']['state'], {})
                self.assertEqual(len(graph['decisions']), 1)
                for node in graph['nodes'].values():
                    self.assertTrue(set(node['successors'].values()) <= set(graph['nodes']))
                model = extract_equation_model(parser.file_path)
                self.assertNotIn('missing_composed_storage', {d.code for d in model.diagnostics})
                for state in model.state:
                    self.assertTrue(model.value_semantics['decision_updates'][state], state)
                for sampled in model.sample_events:
                    self.assertTrue(any(r['writers'] for r in model.value_semantics['decision_updates'][sampled]), sampled)

    def test_loops_errors_and_completed_response_remain_explicit(self):
        for name in NAMES:
            _, _, graph = load(name)
            nodes = graph['nodes']
            self.assertEqual(nodes['cycle/terminal']['successors'],
                             {'true': 'terminal', 'false': 'cycle/entry'})
            self.assertEqual(nodes['cycle/time']['successors']['next'], 'cycle/check')
            self.assertEqual(nodes['cycle/check']['successors']['next'], 'cycle/terminal')
            self.assertEqual(graph['composition']['path_length'], 'arbitrary finite length; no cycle-count bound')
            self.assertIn('nontermination', graph['composition']['infinite_path'])
            for node in nodes.values():
                if node['operation'] == 'accept_copy':
                    self.assertEqual(node['successors']['blocked'], 'execution_error')
                    self.assertIn('$mailbox:' + node['data']['port'], node['reads'])
                if node['operation'] == 'send_copy':
                    self.assertIn('$locals', node['reads'])
                if node['operation'] == 'decision':
                    self.assertTrue(node['data']['stop_before_action'])
                    self.assertNotEqual(node['successors']['resume'], 'terminal')
            self.assertEqual({n['data']['boundary'] for n in nodes.values()
                              if n['operation'] == 'check_all_requirements'}, {'initialization', 'cycle_end'})

    def test_mutated_order_copies_properties_and_runtime_identity_reject(self):
        parser, inventory, graph = load('mixing-sysml-model')
        mutations = []
        bad = deepcopy(graph); bad['nodes']['cycle/check']['data']['boundary'] = 'decision'; mutations.append(bad)
        bad = deepcopy(graph); bad['nodes']['cycle/terminal']['successors']['false'] = 'terminal'; mutations.append(bad)
        bad = deepcopy(graph); bad['nodes']['cycle/check']['data']['properties'].pop(); mutations.append(bad)
        bad = deepcopy(graph); bad['runtime_sha256']['sysml/simulator.py'] = '0' * 64; mutations.append(bad)
        bad = deepcopy(graph)
        send = next(n for n in bad['nodes'].values() if n['operation'] == 'send_copy')
        send['data']['copy'] = 'read current physical value on receipt'; mutations.append(bad)
        bad = deepcopy(graph)
        branch = next(n for n in bad['nodes'].values() if n['operation'] == 'branch')
        branch['successors']['false'] = branch['successors']['true']; mutations.append(bad)
        for bad in mutations:
            bad.pop('sha256'); bad['sha256'] = fingerprint(bad)
            self.assertTrue(validate_decision_transition(bad, parser, inventory))
        self.assertFalse(validate_decision_transition(graph, parser, inventory))

    def test_declared_numeric_types_and_initialization_preserved(self):
        _, inventory, graph = load('mixing-sysml-model')
        key = 'system::feederTank1::currentLevelMl'
        self.assertEqual(inventory.value_types[key], 'Integer')
        self.assertEqual(graph['storage'][key]['declared_type'], 'Integer')
        self.assertEqual(graph['nodes']['initial/entry']['data']['values'], list(inventory.initial_values))
        self.assertIn('runtime operations retained', graph['composition']['numeric_domain'])
        self.assertIn('integer_continuous_state', {d['code'] for d in inventory.diagnostics})

    def test_composition_does_not_remove_solver_rejection(self):
        for name in NAMES:
            model = extract_equation_model(str(models_root() / name / 'model.sysml'))
            self.assertIn('decision_transition_solver_required', {d.code for d in model.diagnostics})
            missing = model.state - set(model.transitions)
            self.assertTrue(missing)
            self.assertEqual({d.subject for d in model.diagnostics if d.code == 'missing_ordered_update'}, missing)

    def test_certificate_checker_rejects_changed_transition_even_after_rehash(self):
        path = str(models_root() / 'thermostat/model.sysml')
        certificate = build_certificate_for_path(path, dt=.1)
        self.assertNotEqual(certificate['result'], 'PASS')
        self.assertTrue(check_certificate(certificate))
        changed = deepcopy(certificate)
        changed['result'] = 'PASS'
        execution = changed['execution']
        graph = execution['decision_transition']
        graph['nodes']['cycle/terminal']['successors']['false'] = 'terminal'
        graph.pop('sha256'); graph['sha256'] = fingerprint(graph)
        execution.pop('sha256'); execution['sha256'] = fingerprint(execution)
        errors = check_certificate(changed)
        self.assertIn('ordered execution/property/type inventory does not match source', errors)

    def fixture(self, statements):
        parser, inventory, _ = load('thermostat')
        composer = Composer(parser, inventory)
        composer.node('end', 'outcome', outcome='terminal')
        events = []
        for i, (target, expr) in enumerate(statements):
            events.append({'event_id': f'e{i}', 'context': 'system', 'kind': 'AssignStmt',
                           'data': {'source_target': [target], 'expression': expression_record(ast.ExpressionParser(expr).parse())}})
        composer.block(events, 'system', 'start', 'end', fresh=True)
        return {'initial_entry': 'start', 'nodes': composer.nodes,
                'storage': {key: {} for key in set(composer.writers) | set(composer.readers) | {'$state', '$machines', '$mailboxes'}}}

    def test_sequential_reads_use_preceding_write_and_sample_stays_separate(self):
        graph = self.fixture([('x', 'x + 1'), ('held', 'x'), ('x', 'x + 1')])
        path = compose_path(graph, ['start', 'e0', 'e1', 'e2', 'end'])
        steps = path['relations']
        self.assertEqual(steps[1]['inputs']['system::x'], 0)
        self.assertEqual(steps[2]['inputs']['system::x'], 1)
        self.assertEqual(steps[3]['inputs']['system::held'], 1)
        self.assertEqual(path['final_versions']['system::x'], 2)
        self.assertEqual(path['final_versions']['system::held'], 1)
        self.assertIn('system::held', steps[3]['unchanged'])
        self.assertFalse(path['covers_all_paths'])

    def test_skipped_operations_reject_and_prefix_is_not_a_transition(self):
        graph = self.fixture([('x', '1'), ('held', 'x')])
        with self.assertRaisesRegex(ValueError, 'skipped source operations'):
            compose_path(graph, ['start', 'e0', 'end'])
        self.assertEqual(compose_path(graph, ['start', 'e0'])['outcome'], 'prefix')

    def test_send_copies_and_accept_removes_current_payload(self):
        parser, _, graph = load('thermostat')
        engine = SimulationEngine(parser)
        node = next(n for n in graph['nodes'].values() if n['operation'] == 'send_copy'
                    and 'thermometer' in n['data']['sender_port'].lower())
        port = node['data']['sender_port']
        context, port_name = port.rsplit('::', 1)
        attrs = {'temperatureCelcius': 17.0}
        dest = engine._send_item('ThermometerReading', attrs, context, port_name)
        self.assertEqual(dest, node['data']['destination'])
        attrs['temperatureCelcius'] = 99.0
        self.assertEqual(engine.port_mailboxes[dest][0]['attrs']['temperatureCelcius'], 17.0)


class RuntimeComparisonTests(unittest.TestCase):
    def compare(self, name):
        parser, inventory, graph = load(name)
        composer = Composer(parser, inventory)
        counts = Counter()
        originals = {name: getattr(SimulationEngine, name) for name in ('_assign_value', '_send_item', 'step')}
        original_resolve = ExpressionEvaluator._resolve_ref
        original_solve = ConstraintSolver.solve
        nodes = list(graph['nodes'].values())
        targets = {n['data']['target'] for n in nodes if n['operation'] == 'assign'}
        send_ports = {n['data']['sender_port']: n['data']['destination'] for n in nodes if n['operation'] == 'send_copy'}
        solve_writes = set(graph['nodes']['cycle/solve']['writes'])
        testcase = self

        def assign(engine, key, value):
            canonical = canonical_key(engine.state, key)
            testcase.assertIn(canonical, targets)
            originals['_assign_value'](engine, key, value)
            testcase.assertEqual(resolve_value(engine.state, canonical), value)
            counts['assignments'] += 1

        def send(engine, type_name, attrs, sender, port):
            frozen = dict(attrs)
            expected = send_ports[sender + '::' + port.replace('.', '::')]
            destination = originals['_send_item'](engine, type_name, attrs, sender, port)
            testcase.assertEqual(destination, expected)
            if destination:
                message = next(m for m in engine.port_mailboxes[destination] if m['type'] == type_name)
                testcase.assertEqual(message['attrs'], frozen)
                testcase.assertIsNot(message['attrs'], attrs)
            counts['sends'] += 1
            return destination

        def lookup(evaluator, path):
            result = original_resolve(evaluator, path)
            record = composer.expression(expression_record(ast.RefExpr(path)), evaluator.context)
            selected = next((key for key in record['lookup_order'] if key in evaluator.state), None)
            expected = resolve_value(evaluator.state, selected) if selected is not None else None
            testcase.assertEqual(result, expected)
            counts['reference_reads'] += 1
            return result

        def solve(solver, machines):
            before = dict(solver.state)
            result = original_solve(solver, machines)
            changed = {key for key, value in solver.state.items() if key not in before or before[key] != value}
            testcase.assertTrue(changed <= solve_writes, sorted(changed - solve_writes))
            counts['constraint_solves'] += 1
            return result

        def step(engine, dt):
            before = engine.time
            result = originals['step'](engine, dt)
            testcase.assertEqual(engine.time, before + dt)
            counts['cycles'] += 1
            return result

        def episode():
            env = SysMLEnv(parser.file_path, dt=.1, max_steps=5000, phase=2)
            shield = SpecShield(parser.file_path)
            rows = []
            try:
                initial = env.reset_with_result(seed=1833613192)
                self.assertEqual(initial.outcome, 'decision')
                for _ in range(5000):
                    inputs = dict(env.model_inputs)
                    action = shield.requirement_action(inputs)
                    _, reward, done, info = env.step(action)
                    state = {key: resolve_value(env._twin.engine.state, key) for key in env._twin.engine.state}
                    self.assertTrue(set(info['statuses']) == {p['name'] for p in inventory.property_inventory})
                    self.assertTrue(all(e.boundary == 'cycle_end' for e in info['requirement_events']))
                    rows.append({'inputs': inputs, 'action': action, 'reward': reward, 'done': done,
                                 'outcome': info['outcome'], 'time': env._twin.engine.time,
                                 'statuses': info['statuses'], 'state': state})
                    if done:
                        self.assertEqual(info['outcome'], 'SUCCESS')
                        break
                else:
                    self.fail('unchanged test episode did not finish')
            finally:
                env.close()
            return rows

        baseline = episode()
        matcher = TraceMatcher(graph, inventory)
        with patch.object(SimulationEngine, '_assign_value', assign), \
             patch.object(SimulationEngine, '_send_item', send), \
             patch.object(SimulationEngine, 'step', step), \
             patch.object(ExpressionEvaluator, '_resolve_ref', lookup), \
             patch.object(ConstraintSolver, 'solve', solve):
            previous_trace = threading.gettrace()
            threading.settrace(matcher.trace)
            try:
                observed = episode()
            finally:
                threading.settrace(previous_trace)
        self.assertEqual(observed, baseline)
        self.assertGreater(counts['assignments'], 0)
        self.assertGreater(counts['sends'], 0)
        self.assertGreater(counts['reference_reads'], 0)
        self.assertGreater(matcher.events, 0)
        if name == 'mixing-sysml-model':
            self.assertGreater(counts['cycles'], len(observed))
        print(json.dumps({'model': name, 'decision_responses': len(observed),
                          'identical_uninstrumented_run': True, 'matched_graph_events': matcher.events, **counts}), flush=True)

    def test_thermostat(self):
        self.compare('thermostat')

    def test_cruise(self):
        self.compare('cruise-controller-model')

    def test_mixing(self):
        self.compare('mixing-sysml-model')


if __name__ == '__main__':
    unittest.main(verbosity=2)
