"""Compact source lowering, independent mutation checks and numerical equations."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import z3

from clarity.certification.compact_transition import (
    compact_decision_transition, validate_compact_transition,
    check_block_equations, expand_expressions,
)
from clarity.certification.ordered_execution import build_execution_description, fingerprint
from clarity.certification.equations import Const, Equation, EquationModel, Ite, Op, Var
from clarity.certification.solver import Encoder, infer_sorts, one_step_transition_closure
from clarity.certification.certificate import build_certificate_for_path, check_certificate
from clarity.models import models_root
from clarity.sysml.parser import SysMLParser


def load(path):
    parser = SysMLParser(str(path)); parser.parse()
    return build_execution_description(parser)


def resign(record):
    record['sha256'] = fingerprint({k: v for k, v in record.items() if k != 'sha256'})
    return record


class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventories = [load(models_root() / name / 'model.sysml') for name in
                           ('thermostat', 'cruise-controller-model', 'mixing-sysml-model')]

    def test_all_source_operations_have_checked_intermediate_equations(self):
        for inventory in self.inventories:
            graph, compact = inventory.decision_transition, inventory.compact_transition
            self.assertEqual(validate_compact_transition(compact, graph), [])
            self.assertLess(len(compact['blocks']), len(graph['nodes']))
            report = check_block_equations(compact, graph, include_artifacts=True)
            self.assertEqual(report['status'], 'discharged')
            self.assertEqual(report['intermediate_equations_checked'], len(graph['nodes']))
            # Replay the emitted query independently of the producer's verdict.
            solver = z3.Solver(); solver.from_string(report['artifacts']['smt2'])
            self.assertEqual(solver.check(), z3.unsat)

    def test_preserves_physical_sample_copy_and_requirement_expressions(self):
        for inventory in self.inventories:
            compact, graph = inventory.compact_transition, inventory.decision_transition
            for block in compact['blocks'].values():
                for eq in block['equations']:
                    source = graph['nodes'][eq['source_node']]
                    self.assertEqual(expand_expressions(eq['data'], compact['expressions']),
                                     json.loads(json.dumps(source['data'])))
            boundaries = [eq for b in compact['blocks'].values() for eq in b['equations']
                          if eq['operation'] == 'check_all_requirements']
            self.assertEqual({eq['data']['boundary'] for eq in boundaries}, {'initialization', 'cycle_end'})
            self.assertTrue(all(len(eq['data']['properties']) == len(inventory.property_inventory)
                                for eq in boundaries))

    def test_renaming_frequency_phase_and_nonperiodic_guard(self):
        source = (models_root() / 'mixing-sysml-model/model.sysml').read_text()
        variants = [
            source.replace('volumeSensor', 'arbitraryProbe').replace('controller', 'decisionMaker'),
            source.replace('scanCycleFrequencyHz : Integer = 10', 'scanCycleFrequencyHz : Integer = 7')
                  .replace('lastScanTimeSeconds : Real = 0', 'lastScanTimeSeconds : Real = 0.037'),
            source.replace('currentTimeSeconds - lastScanTimeSeconds >= 1.0 / scanCycleFrequencyHz',
                           '(observedLevel1 < observedLevel2) or (currentTimeSeconds > 0.237)'),
        ]
        with tempfile.TemporaryDirectory() as folder:
            for i, source_text in enumerate(variants):
                path = Path(folder) / f'variant{i}.sysml'; path.write_text(source_text)
                inventory = load(path)
                self.assertEqual(validate_compact_transition(inventory.compact_transition,
                                                            inventory.decision_transition), [])
                self.assertEqual(check_block_equations(inventory.compact_transition,
                                                      inventory.decision_transition)['status'], 'discharged')
                self.assertNotEqual(inventory.decision_transition['sha256'],
                                    self.inventories[2].decision_transition['sha256'])

    def test_mutated_equations_and_edges_rejected_with_fresh_hashes(self):
        inventory = self.inventories[2]
        graph, original = inventory.decision_transition, inventory.compact_transition
        def first(compact, operation=None):
            return next(eq for b in compact['blocks'].values() for eq in b['equations']
                        if operation is None or eq['operation'] == operation)
        mutations = [
            lambda c: first(c).__setitem__('input', 'c999'),
            lambda c: first(c).__setitem__('result', 'c0'),
            lambda c: first(c, 'assign')['data'].__setitem__('target', 'physical_instead_of_sample'),
            lambda c: first(c, 'send_copy')['data'].__setitem__('copy', 'live physical alias'),
            lambda c: first(c, 'accept_copy')['data'].__setitem__('selection', 'last item'),
            lambda c: first(c, 'check_all_requirements')['data'].__setitem__('properties', []),
            lambda c: first(c).__setitem__('writes', []),
            lambda c: next(iter(c['blocks'].values()))['equations'].pop(),
            lambda c: c['blocks']['cycle/terminal']['successors'].__setitem__('false', 'terminal'),
            lambda c: first(c)['state_access'].__setitem__('read_version', 'node_exit'),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(mutation=i):
                record = deepcopy(original); mutate(record); resign(record)
                self.assertTrue(validate_compact_transition(record, graph))
                self.assertEqual(check_block_equations(record, graph)['status'], 'rejected')

    def test_missing_cyclic_and_altered_expression_rejected(self):
        inventory = self.inventories[0]
        for kind in ('missing', 'cycle', 'changed'):
            compact = deepcopy(inventory.compact_transition)
            key = next(iter(compact['expressions']))
            if kind == 'missing':
                del compact['expressions'][key]
            elif kind == 'cycle':
                compact['expressions'][key] = {'$expression': key}
            else:
                compact['expressions'][key] = {'kind': 'literal', 'type': 'Boolean', 'value': False}
            self.assertTrue(validate_compact_transition(resign(compact), inventory.decision_transition))

    def test_checker_does_not_call_builder(self):
        inventory = self.inventories[0]
        with patch('clarity.certification.compact_transition.compact_decision_transition',
                   side_effect=AssertionError('checker invoked producer')):
            self.assertEqual(validate_compact_transition(inventory.compact_transition,
                                                        inventory.decision_transition), [])

    def test_generation_extracts_source_once_and_does_not_overclaim(self):
        from clarity.certification import certificate_generation as generation
        real = generation.extract_equation_model
        with patch.object(generation, 'extract_equation_model', wraps=real) as extract:
            certificate = build_certificate_for_path(str(models_root() / 'thermostat/model.sysml'), dt=.1)
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(certificate['solver_advisory']['compact_representation_preservation']['status'], 'discharged')
        self.assertNotEqual(certificate['result'], 'PASS')
        self.assertTrue(check_certificate(certificate))


def synthetic_graph(count):
    """Many independently named sample copies with a data-dependent repeat edge."""
    nodes = {}
    def node(identity, operation, successors, data=None, reads=(), writes=()):
        nodes[identity] = dict(operation=operation, successors=successors, data=data or {},
                               reads=list(reads), writes=list(writes), on_exception='error',
                               read_version='node_entry', write_version='node_exit', frame='retain unmodified')
    for i in range(count):
        physical, sample = f'plant_{i}::quantity', f'probe_{i}::held'
        node(f'copy{i}', 'assign', {'next': f'copy{i+1}' if i+1 < count else 'guard'},
             {'target': sample, 'expression': {'kind': 'reference', 'path': [physical],
                                              'storage': physical, 'lookup_order': [physical]}},
             reads=(physical,), writes=(sample,))
    node('guard', 'branch', {'true': 'decision', 'false': 'copy0'},
         {'condition': {'kind': 'reference', 'path': ['stateDependentGuard']}})
    node('decision', 'decision', {'resume': 'response'})
    node('response', 'apply_executed_action', {'next': 'copy0'})
    node('error', 'outcome', {})
    graph = dict(nodes=nodes, initial_entry='copy0', cycle_entry='copy0',
                 decisions=[{'request': 'decision', 'resume': 'response'}],
                 composition={'configuration': ['node', 'state']})
    return resign(graph)


class GeneralityTests(unittest.TestCase):
    def test_variable_component_count_holds_loops_and_no_schedule_assumption(self):
        for count in (1, 3, 7, 17):
            graph = synthetic_graph(count)
            compact = compact_decision_transition(graph)
            self.assertEqual(validate_compact_transition(compact, graph), [])
            self.assertEqual(check_block_equations(compact, graph)['status'], 'discharged')
            self.assertEqual(compact['blocks']['copy0']['successors'],
                             {'true': 'decision', 'false': 'copy0'})
            copies = compact['blocks']['copy0']['equations'][:-1]
            self.assertEqual(len(copies), count)
            for index, eq in enumerate(copies):
                self.assertEqual(eq['input'], f'c{index}')
                self.assertNotEqual(eq['reads'], eq['writes'])
                self.assertIn('retain unmodified', eq['state_access']['frame'])


class NumericTests(unittest.TestCase):
    def model(self, expression, hidden=False):
        transitions = {'x': Equation('x', expression, 'transition')}
        if hidden:
            transitions['h'] = Equation('h', Var('h'), 'transition')
        return EquationModel('<general numerical fixture>', {'x', 'h'} if hidden else {'x'},
                             {'action'}, transitions=transitions)

    def test_repeated_nonlinear_updates_retain_every_equation(self):
        expression = Var('x')
        for _ in range(3):
            expression = Op('*', (expression, expression))
        model = self.model(expression)
        report = one_step_transition_closure(model, {'x'}, include_artifacts=True)
        self.assertEqual(report['status'], 'discharged')
        self.assertEqual(report['max_polynomial_degree'], 2)
        self.assertEqual(report['intermediate_constraints'], 6)
        solver = z3.Solver(); solver.from_string(report['artifacts']['smt2'])
        self.assertEqual(solver.check(), z3.unsat)

    def test_compact_and_expanded_values_identical_for_all_inputs(self):
        x, a = Var('x'), Var('action')
        expressions = [Op('+', (x, a)), Op('-', (x,)),
                       Op('/', (Op('*', (x, x)), Const(2.0))),
                       Ite(Op('>', (x, Const(0))), Op('*', (x, x)), Op('-', (a, x)))]
        for expression in expressions:
            model = self.model(expression)
            sorts, _ = infer_sorts(model)
            compact, direct = Encoder(model, sorts, compact=True), Encoder(model, sorts)
            result = compact.encode_expr(expression, 1, 'current')
            reference = direct.encode_expr(expression, 1, 'current')
            solver = z3.Solver(); solver.add(*compact.constraints, result != reference)
            self.assertEqual(solver.check(), z3.unsat)
            # Removing an actual defining constraint must permit disagreement.
            solver = z3.Solver(); solver.add(*compact.constraints[:-1], result != reference)
            self.assertEqual(solver.check(), z3.sat)

    def test_hidden_dependency_still_produces_counterexample(self):
        expression = Op('*', (Op('*', (Var('x'), Var('x'))), Var('h')))
        report = one_step_transition_closure(self.model(expression, hidden=True), {'x'})
        self.assertEqual(report['status'], 'counterexample')
        self.assertEqual(report['disagreement']['term'], 'next_q.x')

    def test_numeric_and_boolean_constants_do_not_share_terms(self):
        model = self.model(Var('x'))
        encoder = Encoder(model, {'x': 'Real', 'action': 'Real'}, compact=True)
        number = encoder.encode_expr(Ite(Const(True), Const(1), Const(0)), 1, 'current')
        boolean = encoder.encode_expr(Ite(Const(True), Const(True), Const(False)), 1, 'current')
        self.assertTrue(z3.is_real(number)); self.assertTrue(z3.is_bool(boolean))
        self.assertEqual(len(encoder.constraints), 2)

    def test_cycle_and_dynamic_division_are_not_silently_admitted(self):
        model = self.model(Op('/', (Var('x'), Var('action'))))
        self.assertEqual(one_step_transition_closure(model, {'x'})['status'], 'unknown')
        model = self.model(Var('d'))
        model.definitions['d'] = Equation('d', Var('d'), 'definition')
        self.assertEqual(one_step_transition_closure(model, {'x'})['status'], 'unknown')

    def test_dt_is_an_input_to_equations_not_a_fixed_scan_schedule(self):
        for dt in (0.1, 0.07, 0.333):
            expression = Ite(Op('and', ()), Op('+', (Var('x'), Const(dt))), Var('x'))
            self.assertEqual(one_step_transition_closure(self.model(expression), {'x'})['status'], 'discharged')

    def test_default_trajectory_encoder_keeps_original_expression_interface(self):
        expression = Op('*', (Var('x'), Var('x')))
        model = self.model(expression)
        encoder = Encoder(model, {'x': 'Real'})
        result = encoder.encode_expr(expression, 1, 'current')
        self.assertEqual(encoder.constraints, [])
        self.assertEqual(result.decl().kind(), z3.Z3_OP_MUL)


if __name__ == '__main__':
    unittest.main(verbosity=2)
