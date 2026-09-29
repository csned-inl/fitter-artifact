"""Independent scalar semantics, storage preservation and growth regressions."""
import itertools
import contextlib
import io
import math
import unittest

import z3

from clarity.certification.lazy_expressions import Equations, Term, Value, Status, scalar, failure
from clarity.certification.execution_equations import ExecutionEquations
from clarity.certification.equations import EquationModel, Equation, Var, Const, Op
from clarity.discretization.model.proof_rules import expand_definitions, ProofDeferred
from clarity.discretization.certificates.verification.source_reconstruction import _expand_definitions
from clarity.discretization.model.case_reduction import factored_obligation
from source_equation_fixture import small_source_model


def literal(x): return {'kind': 'literal', 'value': x}
def binary(op, a, b): return {'kind': 'binary', 'operator': op, 'left': a, 'right': b}


class LazyExpressions(unittest.TestCase):
    def prove(self, equations, claim):
        solver = z3.Solver(); solver.set(timeout=3000)
        solver.add(*equations.equations, z3.Not(claim))
        self.assertEqual(solver.check(), z3.unsat, solver.reason_unknown())

    def test_native_values_and_errors(self):
        reference = ExecutionEquations(small_source_model().execution, .1)
        values = [0, -2, 2**53+1, 0.0, -0.0, .1, 1e308, float('inf'), float('nan'), True, None]
        for a, b, op in itertools.product(values, values, ['+', '-', '*', '/', '==', '<']):
            with self.subTest(a=a, b=b, op=op):
                expression = binary(op, literal(a), literal(b)); eq = Equations()
                result = eq.expression(expression, lambda _: Term(Value.Absent))
                try: expected = reference.evaluate(expression, {})
                except Exception as exc:
                    self.prove(eq, result.status == failure(str(exc), type(exc).__name__))
                else:
                    self.prove(eq, z3.And(result.status == Status.Success, result.value == scalar(expected)))

    def test_short_circuit_missing_and_arithmetic_errors(self):
        bad = binary('/', literal(1), literal(0))
        for op, left, expected in [('and', False, False), ('or', True, True), ('implies', False, True)]:
            eq = Equations(); result = eq.expression(binary(op, literal(left), bad), lambda _: Term(Value.Absent))
            self.prove(eq, z3.And(result.status == Status.Success, result.value == scalar(expected)))
        eq = Equations(); result = eq.expression({'kind':'conditional','condition':literal(True),
            'true':literal(2),'false':{'kind':'reference','path':['missing']}}, lambda _: Term(Value.Absent), strict=True)
        self.prove(eq, z3.And(result.status == Status.Success, result.value == scalar(2)))

    def test_separate_physical_and_sampled_values(self):
        eq = Equations(); physical = Term(Value.Float(z3.FP('physical', z3.Float64())))
        held = Term(Value.Float(z3.FP('held', z3.Float64())))
        guard = Term(Value.Boolean(z3.Bool('sample_now')))
        sample = eq.conditional(guard, physical, held)
        self.prove(eq, z3.Implies(z3.Not(Value.boolean(guard.value)), sample.value == held.value))
        solver = z3.Solver(); solver.add(*eq.equations, z3.Not(Value.boolean(guard.value)), sample.value != physical.value)
        self.assertEqual(solver.check(), z3.sat)

    def test_linear_equation_and_serialization_growth(self):
        sizes = []
        for n in (8, 16, 32, 64):
            eq = Equations(); value = Term(scalar(0.0))
            for i in range(n):
                value = eq.conditional(Term(Value.Boolean(z3.Bool('g'+str(i)))), Term(scalar(float(i))), value)
            self.assertEqual(len(eq.equations), 2*n)
            solver = z3.Solver(); solver.add(*eq.equations)
            sizes.append(len(solver.to_smt2()))
        self.assertTrue(all(b < 3*a for a,b in zip(sizes,sizes[1:])), sizes)

    def test_shared_source_expression_is_not_reexpanded(self):
        expression = literal(True)
        for _ in range(20): expression = binary('and', expression, expression)
        eq = Equations(); eq.expression(expression, lambda _: Term(Value.Absent))
        self.assertEqual(len(eq.terms), 21)

    def test_mutated_conditional_fails_equivalence(self):
        eq = Equations(); g=z3.Bool('g'); x=z3.FP('x',z3.Float64()); y=z3.FP('y',z3.Float64())
        result=eq.conditional(Term(Value.Boolean(g)),Term(Value.Float(x)),Term(Value.Float(y)))
        solver=z3.Solver();solver.add(*eq.equations,result.value != z3.If(g,Value.Float(y),Value.Float(x)))
        self.assertEqual(solver.check(),z3.sat)


class PropertyPreservation(unittest.TestCase):
    def test_storage_bound_rejects_unrecognized_mailbox_writers(self):
        from copy import deepcopy
        from clarity.certification.lazy_storage import source_storage_schema
        from clarity.models import models_root
        from clarity.certification.strict_extract import extract_equation_model
        with contextlib.redirect_stdout(io.StringIO()):
            model = extract_equation_model(str(models_root() / 'thermostat' / 'model.sysml'))
        for key in ('$mailboxes', '$mailbox:example::port'):
            graph = deepcopy(model.execution['decision_transition'])
            graph['nodes']['unknown_writer'] = {
                'operation': 'unknown_append', 'data': {}, 'successors': {}, 'writes': [key]}
            with self.assertRaisesRegex(ValueError, 'unrecognized mailbox writer'):
                source_storage_schema(graph)

    def test_mailbox_bounds_follow_all_local_writers_and_subtypes(self):
        from clarity.certification.lazy_storage import source_storage_schema
        from source_equation_fixture import small_source_model
        graph = small_source_model().execution['decision_transition']
        graph['item_type_parents'] = {'Child': 'Base'}
        def node(op, **data):
            return {'operation': op, 'data': data, 'successors': {},
                    'on_exception': 'execution_error'}
        graph['nodes'].update({
            'declarations': node('enter_block', declarations={'first': 'Child', 'second': 'Other'}),
            'send_first': node('send_copy', destination='port1', payload='first'),
            'send_second': node('send_copy', destination='port2', payload='second'),
            'accept': node('accept_copy', destination='received', expected_type='Base', context='system'),
            'forward': node('send_copy', destination='port3', payload='received'),
        })
        boxes = dict(source_storage_schema(graph).mailbox_types)
        self.assertEqual(boxes, {'port1': ('Child',), 'port2': ('Other',), 'port3': ('Child',)})
        graph['nodes']['redeclaration'] = node('enter_block', declarations={'first': 'Other'})
        self.assertEqual(set(dict(source_storage_schema(graph).mailbox_types)['port1']), {'Child', 'Other'})

    def test_source_models_have_distinct_storage(self):
        from clarity.models import models_root
        from clarity.certification.strict_extract import extract_equation_model
        from clarity.certification.lazy_storage import source_storage_schema
        for name in ('thermostat','cruise-controller-model','mixing-sysml-model'):
            with contextlib.redirect_stdout(io.StringIO()):
                model=extract_equation_model(str(models_root()/name/'model.sysml'))
            self.assertFalse(model.sampled_state & set(model.definitions))
            for property in model.requirements.values():
                self.assertEqual(expand_definitions(model,property.expr),_expand_definitions(model,property.expr))
            schema=source_storage_schema(model.execution['decision_transition'])
            self.assertGreater(schema.payload_capacity,0)
            self.assertEqual(len(schema.scalar_locations),len(set(schema.scalar_locations)))
            print(name, 'storage',len(schema.scalar_locations),'payload slots',schema.payload_capacity,
                  'call depth',schema.call_depth,flush=True)

    def test_both_checkers_reject_timeless_sample_substitution(self):
        for expr in (Var('x'), Op('+',(Var('x'),Const(1)))):
            model=EquationModel('test',{'x','held'},set(),sampled_state={'held'})
            model.definitions['held']=Equation('held',expr,'definition')
            property=Op('<=',(Var('held'),Const(10)))
            with self.assertRaises(ProofDeferred): expand_definitions(model,property)
            with self.assertRaises(ValueError): _expand_definitions(model,property)

    def test_sample_events_are_not_definitions(self):
        model=EquationModel('test',{'x','held'},set(),sampled_state={'held'})
        model.sample_events['held']=Equation('held',Var('x'),'sample_event')
        property=Op('<=',(Var('held'),Const(10)))
        self.assertEqual(expand_definitions(model,property),property)
        self.assertEqual(_expand_definitions(model,property),property)

    def test_discretization_stays_factored(self):
        clauses=[Op('or',(Var('g'+str(i)),Var('h'+str(i)))) for i in range(40)]
        cases,coverage=factored_obligation(Op('and',tuple(clauses)),'test','physical_interval')
        self.assertEqual(len(cases),1);self.assertEqual(coverage['actual_split_count'],0)


if __name__=='__main__': unittest.main()
