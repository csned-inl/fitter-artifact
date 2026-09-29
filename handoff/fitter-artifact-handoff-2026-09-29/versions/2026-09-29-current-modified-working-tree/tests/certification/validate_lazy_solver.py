"""Proof integration, independent SMT replay and non-enumeration regressions."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import z3

from clarity.certification.lazy_graph import compile_graph
from clarity.certification.lazy_solver import LazyTransitionQuery, interval_order
from clarity.certification.source_solver import check_source_transition_closure
from source_equation_fixture import small_source_model


class Solver(unittest.TestCase):
    def test_return_targets_preserve_nested_and_multiple_callers(self):
        from clarity.certification.lazy_control import machine_return_targets
        def node(op, **edges):
            return {'operation': op, 'successors': edges, 'on_exception': 'error'}
        graph = {'nodes': {
            'start': node('call_machine', call='a', **{'return': 'after_a'}),
            'after_a': node('call_machine', call='b', **{'return': 'done'}),
            'a': node('enter_machine', next='nested'),
            'nested': node('call_machine', call='b', **{'return': 'return_a'}),
            'return_a': node('return'),
            'b': node('enter_machine', next='return_b'),
            'return_b': node('return'),
            'done': node('decision'), 'error': node('outcome'),
        }}
        targets = machine_return_targets(graph)
        self.assertEqual(targets['return_a'], ('after_a',))
        self.assertEqual(set(targets['return_b']), {'return_a', 'done'})
        # Compare against actual push/pop, including a nested call followed by
        # another call of the same machine from the outer procedure.
        location, stack, returns = 'start', [], []
        while location != 'done':
            row = graph['nodes'][location]
            if row['operation'] == 'call_machine':
                stack.append(row['successors']['return'])
                location = row['successors']['call']
            elif row['operation'] == 'return':
                target = stack.pop()
                self.assertIn(target, targets[location])
                returns.append((location, target))
                location = target
            else:
                location = row['successors']['next']
        self.assertEqual(len(returns), 3)
        self.assertEqual(stack, [])
        from clarity.certification.lazy_control import structural_source_progress
        self.assertTrue(structural_source_progress(graph)['discharged'])
        from clarity.certification.lazy_invocations import invocation_order
        plan = invocation_order(graph, 'start')
        self.assertIsNotNone(plan)
        order, edges = plan
        self.assertEqual(sum(key[0] == 'b' for key in order), 2)
        for key in order:
            if graph['nodes'][key[0]]['operation'] == 'return':
                self.assertIn((key[1][-1], (key[1][-1], key[1][:-1])), edges[key])
        graph['nodes']['b']['successors']['next'] = 'b'
        self.assertFalse(structural_source_progress(graph)['discharged'])
        self.assertIsNone(invocation_order(graph, 'start'))
        self.assertIsNone(invocation_order(graph, 'return_b'))

    def test_relation_cache_does_not_cache_verdicts_or_cross_projections(self):
        from clarity.certification.source_solver import _compiled_query
        model = small_source_model()
        model.execution['decision_transition']['nodes']['check']['successors']['next'] = 'cycle/entry'
        _compiled_query.cache_clear()
        with patch('clarity.certification.lazy_backend.bounded_query',
                   return_value={'status':'unknown','reason':'test_solver_response'}) as solve:
            for q in ({'x'}, {'x'}, set()):
                result = check_source_transition_closure(model, q, dt=.1)
                self.assertEqual(result['reason'], 'test_solver_response', result)
            self.assertEqual(solve.call_count, 3)
        cache = _compiled_query.cache_info()
        self.assertEqual((cache.hits, cache.misses), (1, 2))

    def test_external_query_timeout_is_not_a_proof(self):
        import time
        from clarity.certification.lazy_backend import bounded_query
        class Unresponsive:
            def set(self, **settings):
                pass
            def query(self, predicate):
                time.sleep(10)
                return z3.unsat
        started = time.monotonic()
        result = bounded_query(Unresponsive(), None, 100)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reason'], 'solver_timeout')
        self.assertLess(time.monotonic()-started, 2)

    def test_isolated_query_returns_actual_solver_result(self):
        from clarity.certification.lazy_backend import bounded_query
        reached = z3.Function('bounded_reached', z3.IntSort(), z3.BoolSort())
        bad = z3.Function('bounded_bad', z3.BoolSort())
        solver = z3.Fixedpoint()
        solver.set(engine='spacer')
        solver.register_relation(reached, bad)
        solver.rule(reached(0))
        n = z3.Int('bounded_n')
        solver.rule(z3.ForAll([n], z3.Implies(z3.And(reached(n), n != 0), bad())))
        self.assertEqual(bounded_query(solver, bad(), 1000)['status'], 'unsat')

    def test_external_acyclic_timeout_is_not_a_proof(self):
        import time
        from clarity.certification.lazy_backend import bounded_check
        class Unresponsive:
            def set(self, **settings):
                pass
            def check(self):
                time.sleep(10)
                return z3.unsat
        started = time.monotonic()
        result = bounded_check(Unresponsive(), 100)
        self.assertEqual((result['status'], result['reason']), ('unknown', 'solver_timeout'))
        self.assertLess(time.monotonic()-started, 2)

    def test_complete_acyclic_interval_and_independent_replay(self):
        model = small_source_model()
        with patch('clarity.certification.source_solver.compile_program', side_effect=AssertionError('old enumerator called')):
            result = check_source_transition_closure(model, {'x'}, dt=.1, include_artifacts=True)
        self.assertEqual(result['status'], 'discharged', result)
        self.assertEqual(result['equations']['source_nodes'], len(model.execution['decision_transition']['nodes']))
        for script in result['acyclic_projection']['artifacts']:
            verifier = z3.Solver()
            verifier.from_string(script)
            self.assertEqual(verifier.check(), z3.unsat)

    def test_dropping_state_is_not_proved(self):
        model = small_source_model()
        query = LazyTransitionQuery(model, set(), compile_graph(model.execution, .1))
        result = query.acyclic_projection(3000)
        # The complete query must reject this missing-state projection. The
        # preprocessing order may time out before or after arithmetic abstraction;
        # an intermediate solver status is not this test's correctness contract.
        self.assertIn(result['status'], ('sat', 'unknown'))

    def test_no_structural_progress_claim_for_internal_loop(self):
        model = small_source_model()
        graph = model.execution['decision_transition']
        graph['nodes']['check']['successors']['next'] = 'cycle/entry'
        self.assertIsNone(interval_order(graph, 'response'))
        query = LazyTransitionQuery(model, {'x'}, compile_graph(model.execution, .1))
        self.assertFalse(query.progress)
        query.construct()
        self.assertGreater(query.rules, len(graph['nodes']))
        # Construct the actual recursive relation, rather than a bounded replay.
        self.assertIn('(rule ', query.fixedpoint.to_string([query.bad()]))

    def test_missing_writer_metadata_does_not_make_mutable_parameter_constant(self):
        model = small_source_model()
        model.execution['decision_transition']['storage']['system::x']['writers'] = []
        query = LazyTransitionQuery(model, {'x'}, compile_graph(model.execution, .1))
        self.assertNotIn('system::x', query.constants)

    def test_numeric_abstraction_keeps_distinct_operators_and_operands(self):
        from clarity.certification.lazy_congruence import abstract_numeric
        x, y = z3.FPs('congruence_x congruence_y', z3.Float64())
        a = z3.fpAdd(z3.RNE(), x, y)
        b = z3.fpSub(z3.RNE(), x, y)
        abstracted, count = abstract_numeric([a != a, a != b, a != z3.fpAdd(z3.RNE(), y, x)], 'test')
        self.assertEqual(count, 2)
        solver = z3.Solver()
        solver.add(abstracted[0])
        self.assertEqual(solver.check(), z3.unsat)
        for claim in abstracted[1:]:
            solver = z3.Solver()
            solver.add(claim)
            self.assertEqual(solver.check(), z3.sat)

    def test_ssa_preprocessing_preserves_delayed_distinction(self):
        from clarity.certification.lazy_backend import bounded_acyclic_check
        physical, held, copied = z3.FPs('ssa_physical ssa_held ssa_copied', z3.Float64())
        # Copy elimination may identify copied with held, never physical.
        constraints = [copied == held, physical == z3.FPVal(1, z3.Float64()),
                       held == z3.FPVal(2, z3.Float64())]
        result = bounded_acyclic_check([*constraints, copied != physical], 3000, prefix='held_distinction')
        self.assertEqual(result['status'], 'sat', result)
        result = bounded_acyclic_check([*constraints, copied != held], 3000, prefix='held_copy')
        self.assertEqual(result['status'], 'unsat', result)

    def test_ssa_preprocessing_folds_numeric_definitions_before_abstraction(self):
        from clarity.certification.lazy_backend import bounded_acyclic_check
        a, b, c = z3.Ints('ssa_a ssa_b ssa_c')
        result = bounded_acyclic_check([a == 3, b == a+2, c == b*4, c != 20],
                                      3000, prefix='ssa_constants', include_artifacts=True)
        self.assertEqual(result['status'], 'unsat', result)
        self.assertEqual(result['operators'], 0)
        checker = z3.Solver()
        checker.from_string(result['smt2'])
        self.assertEqual(checker.check(), z3.unsat)

    def test_sparse_source_relation_proves_complete_boolean_state(self):
        from validate_source_history import delayed_boolean_model
        from clarity.certification.lazy_backend import bounded_query
        model=delayed_boolean_model()
        query=LazyTransitionQuery(model,{'x','held'},compile_graph(model.execution,.1,specialize=True))
        query.construct()
        self.assertGreater(query.constant_layout.omitted['response'],0)
        result=bounded_query(query.fixedpoint,query.bad(),30000)
        self.assertEqual(result['status'],'unsat',result)

    def test_source_relations_reject_omitted_boolean_state(self):
        from validate_source_history import delayed_boolean_model
        from clarity.certification.lazy_backend import bounded_query
        from clarity.certification.execution_equations import ExecutionEquations
        model = delayed_boolean_model()
        source = ExecutionEquations(model.execution, .1)
        initial = source.advance()
        observations = []
        for previous_action in (False, True):
            previous = source.advance(initial, action={'go': previous_action})
            current = source.advance(previous, action={'go': False})
            observations.append(current.inputs['x'])
        self.assertEqual(observations, [False, True])
        # Both reachable states have the same empty q and current action.
        # The old inline_eager=False setting incorrectly returned UNSAT.
        for sparse in (False, True):
            with self.subTest(sparse=sparse):
                query = LazyTransitionQuery(model, set(), compile_graph(model.execution, .1, specialize=True))
                query.construct(sparse=sparse)
                result = bounded_query(query.fixedpoint, query.bad(), 30000)
                self.assertEqual(result['status'], 'sat', result)

    def test_delayed_reading_cannot_replace_physical_state_in_transition_proof(self):
        from validate_source_history import delayed_boolean_model
        from clarity.certification.lazy_backend import bounded_query
        model = delayed_boolean_model()
        # held stores the previous x. Equal held does not determine next held,
        # whereas equal physical x and action determine both the next reading
        # and the next physical x in this source program.
        for sparse in (False, True):
            for q, expected in (({'held'}, 'sat'), ({'x'}, 'unsat')):
                with self.subTest(sparse=sparse, q=q):
                    query = LazyTransitionQuery(model, q, compile_graph(model.execution, .1, specialize=True))
                    query.construct(sparse=sparse)
                    self.assertEqual(bounded_query(query.fixedpoint, query.bad(), 30000)['status'], expected)

    def test_production_closure_gate_rejects_missing_physical_state(self):
        from validate_source_history import delayed_boolean_model
        from clarity.certification.source_solver import check_source_transition_closure
        model = delayed_boolean_model()
        rejected = check_source_transition_closure(model, {'held'}, dt=.1, timeout_ms=30000)
        self.assertEqual(rejected['status'], 'unknown', rejected)
        self.assertEqual(rejected['z3_check_sat'], 'sat', rejected)
        accepted = check_source_transition_closure(model, {'x', 'held'}, dt=.1, timeout_ms=30000)
        self.assertEqual(accepted['status'], 'discharged', accepted)
        self.assertEqual(accepted['claim'], 'one_step_transition_closure')

    def test_construct_cannot_silently_reuse_another_layout(self):
        from validate_source_history import delayed_boolean_model
        model = delayed_boolean_model()
        for first in (False, True):
            query = LazyTransitionQuery(model, {'x', 'held'}, compile_graph(model.execution, .1, specialize=True))
            query.construct(sparse=first)
            self.assertIs(query.construct(sparse=first), query)
            with self.assertRaises(ValueError):
                query.construct(sparse=not first)

    def test_guard_count_grows_with_source_not_combinations(self):
        sizes = []
        for count in (4, 8, 16):
            model = small_source_model()
            graph = model.execution['decision_transition']
            graph['nodes']['cycle/entry']['successors']['next'] = 'update0'
            prototype = graph['nodes'].pop('update')
            for index in range(count):
                row = deepcopy(prototype)
                row['successors']['next'] = 'update' + str(index+1) if index+1 < count else 'check'
                # Distinct guards can be true independently; they are not
                # enumerated into separate configurations by compilation.
                key = 'guard' + str(index)
                row['data']['expression']['condition'] = {'kind':'reference','path':['system',key]}
                graph['storage']['system::' + key] = {}
                graph['nodes']['update' + str(index)] = row
            program = compile_graph(model.execution, .1)
            self.assertEqual(len(program['nodes']), len(graph['nodes']))
            sizes.append(program['equations'])
        self.assertEqual(sizes[2]-sizes[1], 2*(sizes[1]-sizes[0]))


if __name__ == '__main__':
    unittest.main()
