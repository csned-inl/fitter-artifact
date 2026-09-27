"""Constant facts must be inductive source facts, never frozen sample values."""
import unittest

import z3

from clarity.certification.constant_facts import (
    derive_constant_facts, check_constant_facts, constant_premises, ConstantTransfer)
from clarity.certification.lazy_graph import compile_graph, fields
from clarity.certification.lazy_solver import LazyTransitionQuery
from source_equation_fixture import small_source_model


class Constants(unittest.TestCase):
    def setUp(self):
        self.model = small_source_model()
        self.program = compile_graph(self.model.execution, .1, specialize=True)
        self.facts = derive_constant_facts(self.program)

    def test_packed_accessor_ast_lifetimes_and_sorts(self):
        import gc
        for row in self.program['nodes'].values():
            transfer = ConstantTransfer(row, self.facts[next(name for name, candidate in
                                      self.program['nodes'].items() if candidate is row)])
            gc.collect()
            for path, expression in transfer.input_fields:
                result = transfer.evaluate(expression)
                if result is not None:
                    self.assertEqual(result.sort(), expression.sort(), path)
            for path, expression in transfer.output_fields:
                result = transfer.evaluate(expression)
                if result is not None:
                    self.assertEqual(result.sort(), expression.sort(), path)

    def test_changing_physical_value_not_frozen(self):
        self.assertTrue(check_constant_facts(self.program, self.facts))
        changed = {key: value for key, value in self.facts['response'].items()
                   if key.startswith('store/system::x/') and key.endswith('/payload')}
        self.assertTrue(changed)
        self.assertTrue(all(value is None for value in changed.values()), changed)
        self.assertTrue(self.facts['response']['engine_time'].eq(z3.FPVal(0, z3.Float64())))

    def test_advancing_clock_not_frozen(self):
        graph = self.model.execution['decision_transition']
        graph['nodes']['cycle/entry']['operation'] = 'advance_engine_time'
        program = compile_graph(self.model.execution, .1, specialize=True)
        facts = derive_constant_facts(program)
        self.assertTrue(check_constant_facts(program, facts))
        self.assertIsNone(facts['response']['engine_time'])

    def test_each_inferred_equality_follows_from_source_equations(self):
        for name, row in self.program['nodes'].items():
            incoming = self.facts[name]
            if name == self.program['graph']['initial_entry']:
                incoming = dict.fromkeys(incoming)
            outgoing = ConstantTransfer(row, incoming).result()
            failures = [term != outgoing[path] for path, term in
                        fields(row['compiler'], row['relation'].state) if outgoing[path] is not None]
            solver = z3.Solver()
            solver.set(timeout=30000)
            solver.add(*constant_premises(row, incoming), *row['compiler'].eq.equations,
                       z3.Or(*failures))
            self.assertEqual(solver.check(), z3.unsat, name)

    def test_forged_boundary_constant_rejected(self):
        corrupt = {name: row.copy() for name, row in self.facts.items()}
        corrupt['response']['engine_time'] = z3.FPVal(1, z3.Float64())
        with self.assertRaisesRegex(ValueError, 'not preserved'):
            check_constant_facts(self.program, corrupt)

    def test_missing_source_node_rejected(self):
        corrupt = self.facts.copy()
        del corrupt['execution_error']
        with self.assertRaisesRegex(ValueError, 'omits'):
            check_constant_facts(self.program, corrupt)

    def test_incremental_transfer_matches_fresh_after_facts_change(self):
        for name, row in self.program['nodes'].items():
            original = self.facts[name]
            unknown = dict.fromkeys(original)
            partial = {key: value if index % 2 else None
                       for index, (key, value) in enumerate(original.items())}
            transfer = ConstantTransfer(row, unknown)
            for incoming in (unknown, original, partial, unknown, original):
                transfer.set_incoming(incoming)
                actual = transfer.result()
                expected = ConstantTransfer(row, incoming).result()
                for key in expected:
                    if expected[key] is None:
                        self.assertIsNone(actual[key], (name, key))
                    else:
                        self.assertIsNotNone(actual[key], (name, key))
                        self.assertTrue(actual[key].eq(expected[key]), (name, key))

    def test_worklist_result_matches_full_fifo_recomputation(self):
        from collections import deque
        from clarity.certification.lazy_control import machine_return_targets
        from clarity.certification.type_facts import edges
        from clarity.certification.constant_facts import _same
        actual_facts = derive_constant_facts(self.program, conditional=False)
        rows, graph = self.program['nodes'], self.program['graph']
        root = graph['initial_entry']
        keys = list(self.facts[root])
        unknown = dict.fromkeys(keys)
        facts = {root: unknown.copy()}
        pending, queued = deque([root]), {root}
        returns = machine_return_targets(graph)
        while pending:
            name = pending.popleft()
            queued.remove(name)
            outgoing = ConstantTransfer(rows[name], facts[name]).result()
            for target in edges(graph, name, returns):
                previous = facts.get(target)
                merged = outgoing.copy() if previous is None else {
                    key: previous[key] if _same(previous[key], outgoing[key]) else None
                    for key in keys}
                if previous is None or any(not _same(merged[key], previous[key]) for key in keys):
                    facts[target] = merged
                    if target not in queued:
                        pending.append(target)
                        queued.add(target)
        for name in rows:
            for key in keys:
                self.assertTrue(_same(actual_facts[name][key], facts.get(name, unknown)[key]), (name, key))

    def test_typed_literal_payload_definition_is_propagated(self):
        from types import SimpleNamespace
        from clarity.certification.lazy_expressions import Value
        from unittest.mock import patch
        payload = z3.FP('typed_constant_payload',z3.Float64())
        row = dict(compiler=SimpleNamespace(eq=SimpleNamespace(
                       equations=[Value.Float(payload)==Value.Float(z3.FPVal(3.,z3.Float64()))])),
                   input=object(),relation=SimpleNamespace(state=object()))
        with patch('clarity.certification.constant_facts.fields',return_value=[]):
            transfer=ConstantTransfer(row,{})
        self.assertTrue(transfer.evaluate(payload).eq(z3.FPVal(3.,z3.Float64())))

    def test_infeasible_edges_do_not_destroy_constants(self):
        graph = self.model.execution['decision_transition']
        graph['nodes']['cycle/entry'] = dict(operation='branch',
            successors=dict(true='update', false='dead_clock'), on_exception='execution_error',
            data=dict(condition=dict(kind='literal', value=True)))
        graph['nodes']['dead_clock'] = dict(operation='advance_engine_time',
            successors=dict(next='update'), on_exception='execution_error', data={})
        program = compile_graph(self.model.execution, .1, specialize=True)
        facts = derive_constant_facts(program)
        self.assertTrue(check_constant_facts(program, facts))
        self.assertNotIn('dead_clock', facts.reachable)
        self.assertTrue(facts['response']['engine_time'].eq(z3.FPVal(0,z3.Float64())))
        old = derive_constant_facts(program, conditional=False)
        self.assertIsNone(old['response']['engine_time'])
        from clarity.certification.constant_facts import edge_condition, impossible_edge
        from clarity.certification.type_facts import edges
        for name in facts.reachable:
            row = program['nodes'][name]
            transfer = ConstantTransfer(row, facts[name])
            for target in edges(graph, name):
                if impossible_edge(program,name,target,transfer):
                    solver=z3.Solver();solver.set(timeout=30000)
                    solver.add(*row['compiler'].eq.equations,
                               *constant_premises(row,facts[name]),edge_condition(program,name,target))
                    self.assertEqual(solver.check(),z3.unsat,(name,target))

    def test_reachable_source_edge_cannot_be_removed_from_evidence(self):
        from clarity.certification.constant_facts import ConstantFacts
        forged = ConstantFacts(self.facts, self.facts.reachable - {'response'})
        with self.assertRaisesRegex(ValueError, 'feasible source edge'):
            check_constant_facts(self.program, forged)

    def test_root_constant_cannot_contradict_initialization(self):
        from clarity.certification.constant_facts import ConstantFacts
        forged = ConstantFacts({n:r.copy() for n,r in self.facts.items()}, self.facts.reachable)
        forged['initial/entry']['engine_time'] = z3.FPVal(1,z3.Float64())
        with self.assertRaisesRegex(ValueError, 'initialization'):
            check_constant_facts(self.program, forged)

    def test_newly_feasible_edge_contributes_all_fields(self):
        graph = self.model.execution['decision_transition']
        nodes = graph['nodes']
        nodes['initial/entry']['data']['parameters'][0]['value'] = 2.0
        nodes['initial/entry']['data']['parameters'].append(
            dict(qualified_name='system::flag',cli_name='flag',value=False))
        graph['part_attribute_types']['system']['flag']='Boolean'
        graph['storage']['system::flag']={'writers':['initial/entry','set_flag']}
        nodes['cycle/entry']=dict(operation='branch',successors=dict(true='join',false='set_x'),
            on_exception='execution_error',data=dict(context='system',condition=dict(kind='reference',path=['system','flag'])))
        def assign(name,key,value,target):
            nodes[name]=dict(operation='assign',successors=dict(next=target),on_exception='execution_error',
                data=dict(context='system',target='system::'+key,source_storage_target='system::'+key,
                          source_target=[key],expression=dict(kind='literal',value=value)))
        assign('set_x','x',1.,'join')
        assign('set_flag','flag',True,'reset_x')
        assign('reset_x','x',2.,'cycle/entry')
        del nodes['update']
        del nodes['check']
        nodes['join']=dict(operation='no_op',successors=dict(next='set_flag'),on_exception='execution_error',data={})
        program=compile_graph(self.model.execution,.1,specialize=True)
        facts=derive_constant_facts(program)
        self.assertTrue(check_constant_facts(program,facts))
        self.assertIsNone(facts['join']['store/system::x/payload'])

    def test_simple_source_closure(self):
        query = LazyTransitionQuery(self.model, {'x'}, self.program)
        result = query.acyclic_projection(30000)
        self.assertEqual(result['status'], 'unsat', result)
        self.assertGreater(sum(result['checked_constant_fields'].values()), 0)


if __name__ == '__main__':
    unittest.main()
