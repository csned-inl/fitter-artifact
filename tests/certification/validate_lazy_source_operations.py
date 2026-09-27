"""Compact source-store, message dispatch and constraint differential checks."""
from copy import deepcopy
from dataclasses import replace
import unittest

import z3

from clarity.certification.execution_equations import ExecutionEquations, Configuration, Alias, Binding
from clarity.certification.lazy_expressions import Equations, Term, Value, Status, scalar, text_id
from clarity.certification.lazy_scalar_store import StoreEquations
from clarity.certification.lazy_constraints import ConstraintEquations
from clarity.certification.lazy_messages import MessageEquations
from clarity.certification.lazy_message_operations import MessageOperationCompiler
from clarity.certification.lazy_storage import StorageSchema
from source_equation_fixture import small_source_model


def ref(name): return {'kind': 'reference', 'path': [name]}
def lit(value): return {'kind': 'literal', 'value': value}
def op(name, a, b): return {'kind': 'binary', 'operator': name, 'left': a, 'right': b}


def decode_text(number):
    value = number.as_long()
    return value.to_bytes((value.bit_length()+7)//8, 'big')[1:].decode('utf-8')


def decode_status(model, status):
    value = model.eval(status, model_completion=True)
    if z3.is_true(model.eval(value == Status.Success)): return None
    if z3.is_true(model.eval(Status.is_Failure(value))):
        return decode_text(model.eval(Status.exception_type(value))), decode_text(model.eval(Status.message(value)))
    mode = model.eval(Status.constraint_mode(value)).as_long()
    details = model.eval(Status.constraint_details(value))
    count = model.eval(z3.Select(details, 0)).as_long()
    names = [decode_text(model.eval(z3.Select(details, 2*i+1))) for i in range(count)]
    if mode == 1:
        message = 'source constraints false after propagation: ' + str(names)
    elif mode == 3:
        message = 'unresolved or cyclic initial values: ' + ', '.join(names)
    else:
        messages = [decode_text(model.eval(z3.Select(details, 2*i+2))) for i in range(count)]
        prefix = 'unresolved source constraints: ' if mode == 0 else 'source constraint propagation did not converge: '
        message = prefix + str(dict(zip(names, messages)))
    return 'ValueError', message


class SourceOperations(unittest.TestCase):
    def fixture(self, keys=('x', 'held', 'alias', 'derived'), constraints=(), flows=(), bindings=()):
        model = small_source_model()
        graph = model.execution['decision_transition']
        graph['storage'].update(('system::'+key, {}) for key in keys)
        data = {'bindings': list(bindings), 'constraints': list(constraints), 'flows': list(flows),
                'algorithm': {'iteration_limit': len(constraints)+len(flows)+2}}
        graph['nodes']['initial/entry']['data']['constraints'] = data
        eq = Equations('source'); store = StoreEquations(eq, graph)
        schema = StorageSchema('fixture', (), (), (), (), (), 0, 1)
        messages = MessageEquations(eq, schema, {})
        return model, eq, store, ConstraintEquations(eq, store, messages), data

    def solution(self, eq):
        solver = z3.Solver(); solver.set(timeout=3000); solver.add(*eq.equations)
        self.assertEqual(solver.check(), z3.sat, solver.reason_unknown())
        return solver.model()

    def compare_constraints(self, initial, constraints=(), flows=(), bindings=()):
        model, eq, store, compiler, data = self.fixture(tuple(k.removeprefix('system::') for k in initial),
                                                        constraints, flows, bindings)
        state = store.empty()
        for key, value in initial.items(): state = store.write(state, key, Term(scalar(value)))
        reference = ExecutionEquations(model.execution, .1)
        concrete = Configuration('initial/entry', state=deepcopy(initial))
        expected_error = None
        try: reference.solve_constraints(concrete, data)
        except Exception as exc: expected_error = type(exc).__name__, str(exc)
        result, status = compiler.solve(state, {}, data)
        checked = self.solution(eq)
        self.assertEqual(decode_status(checked, status), expected_error)
        for key, value in concrete.state.items():
            if isinstance(value, Binding): continue
            cell = store.cell(result, key)
            self.assertTrue(z3.is_true(checked.eval(cell.present)), key)
            self.assertTrue(z3.is_true(checked.eval(cell.value == scalar(value))), (key, value, checked.eval(cell.value)))
        return eq, store, result

    def test_held_reading_is_not_a_live_binding(self):
        binding = {'target': 'system::derived', 'expression': op('+', ref('x'), lit(1.0))}
        model, eq, store, _, _ = self.fixture(bindings=[binding])
        state = store.empty()
        state = store.write(state, 'system::x', Term(scalar(10.0)))
        state = store.write(state, 'system::held', store.read(state, 'system::x'))
        state = store.write(state, 'system::derived', Term(Value.Absent), kind=2)
        state = store.write(state, 'system::x', Term(scalar(20.0)))
        values = [store.read(state, 'system::'+key) for key in ('x', 'held', 'derived')]
        checked = self.solution(eq)
        for term, value in zip(values, (20.0, 10.0, 21.0)):
            self.assertTrue(z3.is_true(checked.eval(term.value == scalar(value))))

    def test_undefined_present_key_prevents_fallback(self):
        _, eq, store, _, _ = self.fixture(('x',))
        state = store.write(store.empty(), 'system::x', Term(Value.Absent))
        expression = {'kind': 'reference', 'path': ['x'], 'lookup_order': ['system::x', 'dt']}
        state = store.write(state, 'dt', Term(scalar(2.0)))
        value = store.evaluate(state, expression, strict=True)
        checked = self.solution(eq)
        self.assertEqual(decode_status(checked, value.status), ('MissingReference', 'unresolved requirement reference x'))

    def test_ordered_implications_and_convergence(self):
        rows = [
            {'name': 'copy', 'context': 'system', 'expression': op('==', ref('held'), ref('x'))},
            {'name': 'set', 'context': 'system', 'expression': op('implies', lit(True), op('==', ref('x'), lit(3.0)))},
        ]
        self.compare_constraints({'system::x': 0.0, 'system::held': 1.0}, rows)

    def test_unresolved_and_nonconvergent_errors_match_source(self):
        for expression in (op('==', ref('x'), op('/', lit(1), lit(0))),
                           op('==', ref('x'), op('+', ref('x'), lit(1.0))),
                           op('<', ref('x'), lit(-1.0))):
            with self.subTest(expression=expression):
                self.compare_constraints({'system::x': 0.0},
                    [{'name': 'constraint', 'context': 'system', 'expression': expression}])

    def test_flow_copies_and_ambiguity_match_source(self):
        initial = {'system::a::value': 2.0, 'system::b::value': 3.0, 'system::c::value': 0.0}
        self.compare_constraints(initial, flows=[{'from_port':'a','to_port':'c'}])
        self.compare_constraints(initial, flows=[{'from_port':'a','to_port':'c'}, {'from_port':'b','to_port':'c'}])

    def test_dictionary_order_survives_replacement_and_reinsertion(self):
        _, eq, store, _, _ = self.fixture(('x','held'))
        state = store.write(store.empty(), 'system::x', Term(scalar(1.0)))
        state = store.write(state, 'system::held', Term(scalar(2.0)))
        state = store.write(state, 'system::x', Term(scalar(3.0)))
        a, b = store.cell(state, 'system::x'), store.cell(state, 'system::held')
        self.assertTrue(z3.is_true(self.solution(eq).eval(a.order < b.order)))
        state = store.write(state, 'system::x', Term(Value.Absent), present=False)
        state = store.write(state, 'system::x', Term(scalar(3.0)))
        self.assertTrue(z3.is_true(self.solution(eq).eval(store.cell(state, 'system::x').order > b.order)))

    def test_source_send_and_accept_nodes_match_concrete_evaluator(self):
        model = small_source_model(); graph = model.execution['decision_transition']
        graph['item_type_parents'] = {'Reading':'Base'}
        graph['nodes'].update({
            'block': {'operation':'enter_block','data':{'fresh_locals':True,'declarations':{'item':'Reading'}},
                      'successors':{'next':'send'},'on_exception':'execution_error'},
            'send': {'operation':'send_copy','data':{'payload':'item','destination':'receiver::port'},
                     'successors':{'sent':'accept','absent':'accept'},'on_exception':'execution_error'},
            'accept': {'operation':'accept_copy','data':{'port':'receiver::port','expected_type':'Base',
                        'destination':'received','context':'system'},
                       'successors':{'accepted':'decision'},'on_exception':'execution_error'},
        })
        schema = StorageSchema('fixture',(),(),('item','received'),('reading',),
                               (('receiver::port',('Reading',)),),0,5,('Reading',))
        eq=Equations('dispatch'); compiler=MessageOperationCompiler(eq,graph,schema)
        m,c=compiler.messages.empty(),compiler.control.empty()
        concrete=Configuration('block'); reference=ExecutionEquations(model.execution,.1)
        for node in ('block','send','accept'):
            if node=='send':
                concrete.local_items['item']['attrs']['reading']=12.0
                item=compiler.control.local(c,'item')
                m=compiler.messages.write_field(m,item,'reading',scalar(12.0),z3.IntVal(1))
            concrete.node=node;reference.step(concrete)
            result=compiler.lower(node,m,c,{})
            m,c=result.messages,result.control
            checked=self.solution(eq)
            self.assertEqual(checked.eval(result.successor).as_long(),compiler.node_ids[concrete.node])
        ref_id=compiler.control.local(c,'received')
        reading=compiler.messages.field(m,ref_id,'reading')
        self.assertTrue(z3.is_true(checked.eval(reading.value==scalar(concrete.state['system::received::reading']))))
        self.assertEqual(checked.eval(m.queues['receiver::port'].length).as_long(),len(concrete.mailboxes['receiver::port']))


if __name__=='__main__': unittest.main()
