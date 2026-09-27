"""Message/frame equations: source differential tests and universal local checks."""
from dataclasses import replace
import itertools
import unittest

import z3

from clarity.certification.lazy_expressions import Equations, Value, scalar, text_id
from clarity.certification.lazy_storage import StorageSchema
from clarity.certification.lazy_messages import MessageEquations
from clarity.certification.lazy_control import ControlEquations


def schema(types=('Reading', 'Ack'), ports=('receiver::port',), depth=2):
    return StorageSchema('fixture', (), (), ('item', 'received'), ('reading', 'flag'),
                         tuple((p, types) for p in ports), depth,
                         len(ports)*len(types)+(depth+1)*3+1, types)


class Messages(unittest.TestCase):
    def make(self, spec=None):
        eq = Equations('messages')
        return eq, MessageEquations(eq, spec or schema(), {'Reading': 'Base'})

    def solve(self, eq, *conditions):
        solver = z3.Solver(); solver.set(timeout=3000)
        solver.add(*eq.equations, *conditions)
        self.assertEqual(solver.check(), z3.sat, solver.reason_unknown())
        return solver.model()

    def prove(self, eq, claim, *premises):
        solver = z3.Solver(); solver.set(timeout=3000)
        solver.add(*eq.equations, *premises, z3.Not(claim))
        self.assertEqual(solver.check(), z3.unsat, solver.reason_unknown())

    def test_send_copies_then_accept_retains_old_sensor_value(self):
        eq, compiler = self.make()
        state, item = compiler.declare(compiler.empty(), 'Reading')
        physical = z3.FP('physical', z3.Float64())
        updated = z3.FP('updated_physical', z3.Float64())
        state = compiler.write_field(state, item, 'reading', Value.Float(physical), z3.IntVal(1))
        state, sent = compiler.send(state, item, 'receiver::port', [(True, item)])
        state = compiler.write_field(state, item, 'reading', Value.Float(updated), z3.IntVal(2))
        state, received = compiler.accept(state, 'receiver::port', 'Base')
        field = compiler.field(state, received, 'reading')
        self.prove(eq, z3.And(received == sent, received != item, field.present,
                             field.value == Value.Float(physical), field.identity == 1,
                             state.queues['receiver::port'].length == 0,
                             *compiler.obligations))
        self.solve(eq, physical != updated, field.value != Value.Float(updated))

    def test_type_replacement_and_first_subtype_order(self):
        for ordering in itertools.permutations(('Reading', 'Ack')):
            with self.subTest(ordering=ordering):
                eq, compiler = self.make()
                state = compiler.empty(); expected = []
                for i, kind in enumerate((*ordering, ordering[0])):
                    state, local = compiler.declare(state, kind)
                    state = compiler.write_field(state, local, 'reading', scalar(i), z3.IntVal(0))
                    state, _ = compiler.send(state, local, 'receiver::port', [(True, local)])
                    new = (kind, i)
                    index = next((n for n, old in enumerate(expected) if old[0] == kind), None)
                    if index is None: expected.append(new)
                    else: expected[index] = new
                model = self.solve(eq)
                queue = state.queues['receiver::port']
                self.assertEqual(model.eval(queue.length).as_long(), len(expected))
                for i, (kind, reading) in enumerate(expected):
                    ref = z3.Select(queue.references, i)
                    self.assertEqual(model.eval(z3.Select(state.types, ref)).as_long(), text_id(kind))
                    self.assertTrue(z3.is_true(model.eval(compiler.field(state, ref, 'reading').value == scalar(reading))))
                _, chosen = compiler.match(state, 'receiver::port', 'Base')
                self.prove(eq, z3.Select(state.types, chosen) == text_id('Reading'))
                self.prove(eq, z3.And(*compiler.obligations))

    def test_parent_fallback_only_if_primary_empty(self):
        eq, compiler = self.make(schema(ports=('receiver', 'receiver::port')))
        state, reading = compiler.declare(compiler.empty(), 'Reading')
        state, _ = compiler.send(state, reading, 'receiver')
        state, ack = compiler.declare(state, 'Ack')
        state, _ = compiler.send(state, ack, 'receiver::port')
        after, received = compiler.accept(state, 'receiver::port', 'Base')
        self.prove(eq, z3.And(received == 0, after.queues['receiver'].length == 1,
                             after.queues['receiver::port'].length == 1))
        state, _ = compiler.accept(after, 'receiver::port', 'Ack')
        state, received = compiler.accept(state, 'receiver::port', 'Base')
        self.prove(eq, z3.And(received != 0, state.queues['receiver'].length == 0))

    def test_nan_identity_and_python_boolean_numeric_equality(self):
        for same_identity in (False, True):
            eq, compiler = self.make()
            state, a = compiler.declare(compiler.empty(), 'Reading')
            state = compiler.write_field(state, a, 'reading', scalar(float('nan')), z3.IntVal(10))
            state = compiler.write_field(state, a, 'flag', scalar(True), z3.IntVal(0))
            state, sent = compiler.send(state, a, 'receiver::port', [(True, a)])
            state, b = compiler.declare(state, 'Reading', [(True, a)])
            state = compiler.write_field(state, b, 'reading', scalar(float('nan')),
                                         z3.IntVal(10 if same_identity else 11))
            state = compiler.write_field(state, b, 'flag', scalar(1), z3.IntVal(0))
            eq_before = compiler.equal(state, a, b)
            after = compiler.remove_equal(state, 'receiver::port', b)
            self.prove(eq, z3.And(eq_before == same_identity,
                                 after.queues['receiver::port'].length == (0 if same_identity else 1)))

    def test_symbolic_send_preserves_unique_types_and_capacity(self):
        # Arbitrary queue lengths, order, references and field values, not a
        # collection of sampled executions. The spare slot follows the schema.
        eq, compiler = self.make(schema(depth=0))
        state = compiler.symbolic('before')
        local = z3.Int('local_reference'); roots = [(True, local)]
        before = compiler.well_formed(state, roots)
        after, sent = compiler.send(state, local, 'receiver::port', roots)
        self.prove(eq, z3.And(*compiler.obligations, compiler.well_formed(after, roots)), before)
        for field in compiler.fields:
            a, b = compiler.field(state, local, field), compiler.field(after, sent, field)
            self.prove(eq, z3.And(a.present == b.present, a.value == b.value,
                                 a.identity == b.identity), before)

    def test_conditional_send_preserves_disabled_state(self):
        eq, compiler = self.make(schema(depth=0))
        state = compiler.symbolic('before'); local = z3.Int('local'); enabled = z3.Bool('enabled')
        after, _ = compiler.send(state, local, 'receiver::port', [(True, local)], enabled)
        self.prove(eq, z3.And(after.types == state.types, after.values == state.values,
            after.present == state.present, after.identities == state.identities,
            after.orders == state.orders, after.lengths == state.lengths,
            after.queues['receiver::port'].length == state.queues['receiver::port'].length,
            after.queues['receiver::port'].references == state.queues['receiver::port'].references), z3.Not(enabled))

    def test_payload_attribute_order_survives_copy_and_overwrite(self):
        eq, compiler = self.make()
        state, ref = compiler.declare(compiler.empty(), 'Reading')
        for field, value in (('flag', True), ('reading', 2.0), ('flag', False)):
            state = compiler.write_field(state, ref, field, scalar(value), z3.IntVal(0))
        state, copied = compiler.send(state, ref, 'receiver::port', [(True, ref)])
        self.prove(eq, z3.And(z3.Select(state.lengths, copied) == 2,
            z3.Select(state.orders, compiler.field_index(copied, 'flag')) == 0,
            z3.Select(state.orders, compiler.field_index(copied, 'reading')) == 1))

    def test_calls_preserve_local_dictionary_sharing(self):
        eq = Equations('control'); compiler = ControlEquations(eq, schema())
        state = compiler.empty()
        state = compiler.set_local(state, 'item', z3.IntVal(4))
        state = compiler.push(state, z3.IntVal(17))
        state = compiler.set_local(state, 'item', z3.IntVal(5))
        state, destination = compiler.pop(state)
        self.prove(eq, z3.And(compiler.local(state, 'item') == 5, destination == 17,
                             *compiler.obligations))

    def test_fresh_locals_do_not_overwrite_saved_dictionary(self):
        eq = Equations('control'); compiler = ControlEquations(eq, schema())
        state = compiler.set_local(compiler.empty(), 'item', z3.IntVal(4))
        state = compiler.push(state, z3.IntVal(17))
        state = compiler.fresh_locals(state)
        state = compiler.set_local(state, 'item', z3.IntVal(8))
        state, _ = compiler.pop(state)
        self.prove(eq, z3.And(compiler.local(state, 'item') == 4, *compiler.obligations))


if __name__ == '__main__': unittest.main()
