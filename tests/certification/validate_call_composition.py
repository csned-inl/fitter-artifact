"""Repeated static machine calls preserve both executions and saved returns."""
import unittest
import z3
from clarity.certification.lazy_graph import compile_graph
from clarity.certification.lazy_solver import LazyTransitionQuery, Output, interval_order
from clarity.certification.lazy_expressions import Value
from clarity.certification.constant_facts import constant_premises
from source_equation_fixture import small_source_model


def repeated_calls():
    model = small_source_model()
    graph = model.execution['decision_transition']
    nodes = graph['nodes']
    nodes['cycle/entry']['successors']['next'] = 'call_first'
    def node(op, successors, data=None):
        return {'operation': op, 'successors': successors, 'data': data or {},
                'on_exception': 'execution_error'}
    nodes['call_first'] = node('call_machine', {'call': 'machine', 'return': 'call_second'})
    nodes['call_second'] = node('call_machine', {'call': 'machine', 'return': 'check'})
    nodes['machine'] = node('enter_machine', {'next': 'update'}, {'instance': 'system::machine'})
    nodes['update']['data']['expression'] = {'kind': 'binary', 'operator': '+',
        'left': {'kind': 'reference', 'path': ['system', 'x']}, 'right': {'kind': 'literal', 'value': 1.0}}
    nodes['update']['successors']['next'] = 'machine_return'
    nodes['machine_return'] = node('return', {})
    graph['machine_states']['system::machine'] = []
    nodes['initial/entry']['data']['machine_initial_states']['system::machine'] = None
    return model


class Composition(unittest.TestCase):
    def test_calls_do_not_create_a_recursive_interval(self):
        model = repeated_calls()
        query = LazyTransitionQuery(model, {'x'}, compile_graph(model.execution, .1, specialize=True))
        self.assertIsNone(interval_order(query.graph, 'response'))
        self.assertIsNotNone(query.invocations['response'])
        result = query.acyclic_projection(30000)
        self.assertEqual(result['status'], 'unsat', result)

    def test_wrong_saved_return_is_explicit_failure(self):
        from dataclasses import replace
        model = repeated_calls()
        program = compile_graph(model.execution, .1, specialize=True)
        row = program['nodes']['machine_return']
        row['relation'] = replace(row['relation'], successor=z3.IntVal(row['compiler'].ids['check']))
        query = LazyTransitionQuery(model, {'x'}, program)
        equations, output, events, failed = query.acyclic_execution('response')
        solver = z3.Solver()
        solver.set(timeout=30000)
        solver.add(*equations, *constant_premises(query.rows['response'], query.checked_constants()['response']),
                   query.projections['response'][0] == Value.Float(z3.FPVal(0, z3.Float64())))
        self.assertEqual(solver.check(), z3.sat)
        solver.add(z3.Not(failed))
        self.assertEqual(solver.check(), z3.unsat)

    def test_two_calls_apply_two_updates(self):
        model = repeated_calls()
        query = LazyTransitionQuery(model, {'x'}, compile_graph(model.execution, .1, specialize=True))
        equations, output, events, failed = query.acyclic_execution('response')
        solver = z3.Solver()
        solver.set(timeout=30000)
        solver.add(*equations, *constant_premises(query.rows['response'], query.checked_constants()['response']),
                   query.projections['response'][0] == Value.Float(z3.FPVal(0, z3.Float64())))
        self.assertEqual(solver.check(), z3.sat)
        solver.add(z3.Or(failed, z3.Select(Output.values(output), 0) != Value.Float(z3.FPVal(2, z3.Float64()))))
        self.assertEqual(solver.check(), z3.unsat)


if __name__ == '__main__':
    unittest.main()
