"""Constructor analysis and optimization soundness regressions (workstation)."""
from copy import deepcopy
import unittest

import z3

from clarity.certification.lazy_expressions import Equations, Value, Status, Term
from clarity.certification.lazy_graph import compile_graph
from clarity.certification.type_facts import (AbstractEquations, KINDS, derive_type_facts,
                                             check_type_facts, symbolic_value, tracked)
from source_equation_fixture import small_source_model


class TypeFacts(unittest.TestCase):
    def test_specialized_nodes_preserve_complete_source_results(self):
        from copy import copy
        from clarity.certification.lazy_graph import fields
        from clarity.certification.lazy_solver import variables
        execution = small_source_model().execution
        graph = execution['decision_transition']
        graph['part_attribute_types']['system']['held'] = 'Real'
        graph['nodes']['initial/entry']['data']['parameters'].append(
            {'qualified_name': 'system::held', 'cli_name': 'held', 'value': 0.0})
        graph['storage']['system::held'] = {'writers': ['initial/entry', 'sample']}
        graph['nodes']['cycle/entry']['successors']['next'] = 'sample'
        graph['nodes']['sample'] = {
            'operation': 'assign', 'successors': {'next': 'update'},
            'on_exception': 'execution_error',
            'data': {'context': 'system', 'target': 'system::held',
                     'source_storage_target': 'system::held', 'source_target': ['held'],
                     'expression': {'kind': 'conditional',
                         'condition': {'kind': 'reference', 'path': ['system', 'go']},
                         'true': {'kind': 'reference', 'path': ['system', 'x']},
                         'false': {'kind': 'reference', 'path': ['system', 'held']}}}}
        generic = compile_graph(execution, .1)
        specialized = compile_graph(execution, .1, specialize=True)
        for name, left in generic['nodes'].items():
            right = specialized['nodes'][name]
            compiler = copy(right['compiler'])
            del compiler.scalar_layout
            def snapshot(row, owner, state):
                return [v for _, v in fields(owner, state)]
            a_inputs = snapshot(left, left['compiler'], left['input'])
            b_inputs = snapshot(right, compiler, right['input'])
            a_inputs += [term.value for term in generic['actions'].values()]
            b_inputs += [term.value for term in specialized['actions'].values()]
            def outputs(row, owner):
                relation = row['relation']
                result = snapshot(row, owner, relation.state)
                result += [relation.status, relation.successor]
                result += list(owner.representation_obligations)
                for boundary, enabled, entries in relation.events:
                    result.append(enabled)
                    for _, term in sorted(entries):
                        result.extend((term.value, term.status, term.identity))
                return result
            a_outputs, b_outputs = outputs(left, left['compiler']), outputs(right, compiler)
            self.assertEqual(len(a_outputs), len(b_outputs), name)
            expressions = [*right['compiler'].eq.equations, *b_inputs, *b_outputs]
            renaming = [(v, z3.Const('independent_' + str(v.get_id()), v.sort()))
                        for v in variables(*expressions)]
            def other(term):
                return z3.substitute(term, *renaming)
            solver = z3.Solver()
            solver.set(timeout=30000)
            solver.add(*left['compiler'].eq.equations,
                       *[other(e) for e in right['compiler'].eq.equations],
                       *[a == other(b) for a, b in zip(a_inputs, b_inputs)],
                       z3.Or(*[a != other(b) for a, b in zip(a_outputs, b_outputs)]))
            self.assertEqual(solver.check(), z3.unsat, name)

    def test_named_optional_values_preserve_their_value(self):
        source = symbolic_value('optional_source', frozenset(('Absent', 'Float')))
        equations = Equations('optional_bind')
        first = equations.bind(source)
        second = equations.bind(first.value)
        solver = z3.Solver()
        solver.add(*equations.equations, source != second.value)
        self.assertEqual(solver.check(), z3.unsat)

    def test_declared_real_does_not_hide_integer_or_boolean_writes(self):
        model = small_source_model()
        graph = model.execution['decision_transition']
        graph['nodes']['initial/entry']['data']['parameters'][0]['value'] = 0
        graph['nodes']['update']['data']['expression'] = {'kind': 'reference', 'path': ['system', 'go']}
        program = compile_graph(model.execution, .1)
        facts = derive_type_facts(program)
        self.assertTrue(check_type_facts(program, facts))
        self.assertEqual(facts['decision']['system::x', 'value'], frozenset(('Integer', 'Boolean')))
        specialized = compile_graph(model.execution, .1, specialize=True)
        self.assertIn('Boolean', specialized['type_evidence']['scalar_layout']['system::x'])
        self.assertIn('Integer', specialized['type_evidence']['scalar_layout']['system::x'])

    def test_error_edges_preserve_writes_before_failure(self):
        model = small_source_model()
        graph = model.execution['decision_transition']
        graph['nodes']['update']['data']['expression'] = {'kind': 'literal', 'value': True}
        graph['nodes']['check']['operation'] = 'branch'
        graph['nodes']['check']['data'] = {'context': 'system', 'condition': {'kind': 'literal', 'value': 1}}
        graph['nodes']['check']['successors'] = {'true': 'decision', 'false': 'decision'}
        program = compile_graph(model.execution, .1)
        facts = derive_type_facts(program)
        self.assertTrue(check_type_facts(program, facts))
        self.assertIn('Boolean', facts['execution_error']['system::x', 'value'])
        corrupted = deepcopy(facts)
        corrupted['execution_error']['system::x', 'value'] = frozenset(('Float',))
        with self.assertRaisesRegex(ValueError, 'not preserved'):
            check_type_facts(program, corrupted)

    def test_integer_operations_preserve_unbounded_values_and_errors(self):
        a, b = z3.Ints('typed_int_left typed_int_right')
        ls, rs = z3.Consts('typed_int_left_status typed_int_right_status', Status)
        for index, op in enumerate(('+', '-', '*', '==', '<', '<=', '>', '>=')):
            generic = Equations('generic_int_' + str(index), specialize_operations=False)
            native = Equations('native_int_' + str(index))
            operands = (Term(Value.Integer(a), ls), Term(Value.Integer(b), rs))
            old, new = generic.binary(op, *operands), native.binary(op, *operands)
            solver = z3.Solver()
            solver.set(timeout=30000)
            solver.add(*generic.equations, *native.equations,
                       z3.Or(old.value != new.value, old.status != new.status,
                             old.identity != new.identity))
            self.assertEqual(solver.check(), z3.unsat, op)

    def test_float_operations_include_errors_and_special_values(self):
        a, b = z3.FPs('typed_left typed_right', z3.Float64())
        ls, rs = z3.Consts('typed_left_status typed_right_status', Status)
        for op in ('+', '-', '*', '/', '==', '<', '<=', '>', '>='):
            generic = Equations('generic_' + str(ord(op[0])), specialize_operations=False)
            native = Equations('native_' + str(ord(op[0])))
            operands = (Term(Value.Float(a), ls), Term(Value.Float(b), rs))
            old, new = generic.binary(op, *operands), native.binary(op, *operands)
            solver = z3.Solver()
            solver.set(timeout=30000)
            solver.add(*generic.equations, *native.equations,
                       z3.Or(old.value != new.value, old.status != new.status,
                             old.identity != new.identity))
            self.assertEqual(solver.check(), z3.unsat, op)

    def test_type_evidence_replay_and_mutation(self):
        program = compile_graph(small_source_model().execution, .1)
        facts = derive_type_facts(program)
        self.assertTrue(check_type_facts(program, facts))
        corrupted = deepcopy(facts)
        del corrupted[next(iter(corrupted))]
        with self.assertRaisesRegex(ValueError, 'omits source nodes'):
            check_type_facts(program, corrupted)
        root = program['graph']['initial_entry']
        from clarity.certification.type_facts import edges, transfer
        target = edges(program['graph'], root)[0]
        outgoing = transfer(program['nodes'][root], facts[root])
        key = next(k for k, v in outgoing.items() if k[1] == 'value' and v != KINDS)
        corrupted = deepcopy(facts)
        corrupted[target][key] = KINDS - outgoing[key]
        with self.assertRaisesRegex(ValueError, 'not preserved'):
            check_type_facts(program, corrupted)

    def test_constructor_equality_does_not_equate_payloads(self):
        a, b = z3.Ints('type_a type_b')
        term = Value.Integer(a) == Value.Integer(b)
        self.assertEqual(AbstractEquations.operation(term, [frozenset(('Integer',))] * 2),
                         frozenset((False, True)))

    def test_optional_representation_retains_absent_and_signed_zero(self):
        value = symbolic_value('optional_float', frozenset(('Absent', 'Float')))
        for target in (Value.Absent, Value.Float(z3.FPVal(0.0, z3.Float64())),
                       Value.Float(z3.FPVal(-0.0, z3.Float64())),
                       Value.Float(z3.fpNaN(z3.Float64()))):
            solver = z3.Solver()
            solver.add(value == target)
            self.assertEqual(solver.check(), z3.sat)
        solver = z3.Solver()
        solver.add(Value.is_Integer(value))
        self.assertEqual(solver.check(), z3.unsat)

    def test_native_layout_preserves_raw_values_including_absence(self):
        from clarity.certification.type_facts import packed_value
        for kind in sorted(KINDS - {'Absent'}):
            kinds = frozenset(('Absent', kind))
            left, right = symbolic_value('pack_left_' + kind, kinds), symbolic_value('pack_right_' + kind, kinds)
            a, b = packed_value(left, kinds), packed_value(right, kinds)
            solver = z3.Solver()
            solver.set(timeout=30000)
            solver.add(*[x == y for (_, x), (_, y) in zip(a, b)], left != right)
            self.assertEqual(solver.check(), z3.unsat, kind)

    def test_specialized_graph_preserves_coverage_and_field_sorts(self):
        execution = small_source_model().execution
        program = compile_graph(execution, .1, specialize=True)
        self.assertEqual(set(program['nodes']), set(execution['decision_transition']['nodes']))
        self.assertIn('type_evidence', program)
        import json
        self.assertEqual(json.loads(json.dumps(program['type_evidence'])), program['type_evidence'])
        for row in program['nodes'].values():
            self.assertEqual({key: value.sort() for key, value in tracked(row['input']).items()},
                             {key: value.sort() for key, value in tracked(row['relation'].state).items()})
        from clarity.certification.lazy_graph import fields
        signatures = [{key: value.sort() for key, value in fields(row['compiler'], row['input'])}
                      for row in program['nodes'].values()]
        self.assertTrue(all(signature == signatures[0] for signature in signatures))


if __name__ == '__main__':
    unittest.main()
