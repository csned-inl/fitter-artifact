"""Finite source-derived message storage with conditional, shared equations.

Queues retain source order and exact-type replacement. References retain sharing
through accept/trigger operations; send allocates a copy. Allocation and capacity
conditions are proof obligations, never assumptions used to discard executions.
"""
from dataclasses import dataclass, replace

import z3

from .lazy_expressions import Value, Term, text_id, boolean_value, float_value


@dataclass(frozen=True)
class StoredValue:
    present: object
    value: object
    identity: object


@dataclass(frozen=True)
class Queue:
    length: object
    references: object


@dataclass(frozen=True)
class Messages:
    types: object
    present: object
    values: object
    identities: object
    queues: dict
    orders: object
    lengths: object


class MessageEquations:
    def __init__(self, equations, schema, parents):
        self.equations = equations
        self.capacity = schema.payload_capacity
        self.fields = tuple(schema.payload_fields)
        self.box_types = dict(schema.mailbox_types)
        self.parents = dict(parents)
        self.obligations = []
        self.all_types = set(schema.item_types) | set(t for types in self.box_types.values() for t in types)
        # Subtyping must be finite and source-defined.
        for kind in self.all_types:
            self.subtype(kind, kind, check_cycle=True)

    def subtype(self, actual, expected, check_cycle=False):
        seen, matched = set(), False
        while actual is not None:
            if actual in seen:
                raise ValueError('cyclic item inheritance')
            seen.add(actual)
            matched |= actual == expected
            if matched and not check_cycle:
                return True
            actual = self.parents.get(actual)
        return matched

    def empty(self):
        zero = z3.K(z3.IntSort(), z3.IntVal(0))
        return Messages(zero, z3.K(z3.IntSort(), z3.BoolVal(False)),
                        z3.K(z3.IntSort(), Value.Absent), zero,
                        {port: Queue(z3.IntVal(0), zero) for port in self.box_types}, zero, zero)

    def symbolic(self, prefix):
        def array(name, sort):
            return z3.Array(prefix + '_' + name, z3.IntSort(), sort)
        return Messages(array('types', z3.IntSort()), array('present', z3.BoolSort()),
                        array('values', Value), array('identities', z3.IntSort()),
                        {port: Queue(z3.Int(f'{prefix}_length_{i}'),
                                     array('queue_' + str(i), z3.IntSort()))
                         for i, port in enumerate(self.box_types)},
                        array('orders', z3.IntSort()), array('lengths', z3.IntSort()))

    def field_index(self, reference, field):
        if field not in self.fields:
            raise ValueError('payload field missing from source schema: ' + field)
        return reference * len(self.fields) + self.fields.index(field)

    def field(self, state, reference, field):
        index = self.field_index(reference, field)
        return StoredValue(z3.Select(state.present, index), z3.Select(state.values, index),
                           z3.Select(state.identities, index))

    def queue(self, state, port):
        return state.queues.get(port, Queue(z3.IntVal(0), z3.K(z3.IntSort(), z3.IntVal(0))))

    def roots(self, state, external=()):
        result = list(external)
        for port, types in self.box_types.items():
            box = state.queues[port]
            result.extend((i < box.length, z3.Select(box.references, i))
                          for i in range(len(types)))
        return result

    def well_formed(self, state, external=()):
        conditions = []
        for live, reference in self.roots(state, external):
            conditions.append(z3.Implies(live, z3.And(reference > 0, reference <= self.capacity,
                z3.Or(*[z3.Select(state.types, reference) == text_id(t) for t in self.all_types]))))
            length = z3.Select(state.lengths, reference)
            present = [self.field(state, reference, field).present for field in self.fields]
            order = [z3.Select(state.orders, self.field_index(reference, field)) for field in self.fields]
            conditions.append(z3.Implies(live, z3.And(
                length == z3.Sum([z3.If(p, 1, 0) for p in present]),
                *[z3.Implies(p, z3.And(rank >= 0, rank < length)) for p, rank in zip(present, order)],
                *[z3.Implies(z3.And(present[i], present[j]), order[i] != order[j])
                  for i in range(len(order)) for j in range(i)])))
        for port, types in self.box_types.items():
            box = state.queues[port]
            conditions += [box.length >= 0, box.length <= len(types)]
            for i in range(len(types)):
                reference = z3.Select(box.references, i)
                kind = z3.Select(state.types, reference)
                conditions.append(z3.Implies(i < box.length,
                    z3.Or(*[kind == text_id(t) for t in types])))
                for j in range(i):
                    other = z3.Select(state.types, z3.Select(box.references, j))
                    conditions.append(z3.Implies(i < box.length, kind != other))
        return z3.And(*conditions)

    def free_reference(self, state, external, active):
        roots = self.roots(state, external)
        chosen = z3.IntVal(0)
        for slot in reversed(range(1, self.capacity + 1)):
            unused = z3.And(*[z3.Or(z3.Not(live), ref != slot) for live, ref in roots])
            chosen = z3.If(unused, slot, chosen)
        chosen = self.equations.native(chosen)
        self.obligations.append(z3.Implies(active, chosen != 0))
        return chosen

    def _write_array(self, array, index, value, active):
        return self.equations.native(z3.If(active, z3.Store(array, index, value), array))

    def write_field(self, state, reference, field, value, identity, active=True):
        index = self.field_index(reference, field)
        added = z3.And(active, z3.Not(z3.Select(state.present, index)))
        return replace(state,
            present=self._write_array(state.present, index, z3.BoolVal(True), active),
            values=self._write_array(state.values, index, value, active),
            identities=self._write_array(state.identities, index, identity, active),
            orders=self._write_array(state.orders, index, z3.Select(state.lengths, reference), added),
            lengths=self._write_array(state.lengths, reference, z3.Select(state.lengths, reference)+1, added))

    def declare(self, state, kind, external=(), active=True):
        if kind not in self.all_types:
            raise ValueError('undeclared sendable payload type: ' + kind)
        reference = self.free_reference(state, external, active)
        result = replace(state, types=self._write_array(state.types, reference, z3.IntVal(text_id(kind)), active),
                         lengths=self._write_array(state.lengths, reference, z3.IntVal(0), active))
        for field in self.fields:
            index = self.field_index(reference, field)
            # Absent attributes have no observable value or identity.
            result = replace(result,
                present=self._write_array(result.present, index, z3.BoolVal(False), active))
        return result, reference

    def _first(self, state, port, predicate):
        box = self.queue(state, port)
        selected = z3.IntVal(-1)
        for index in reversed(range(len(self.box_types.get(port, ())))):
            reference = z3.Select(box.references, index)
            selected = z3.If(z3.And(index < box.length, predicate(reference)), index, selected)
        return self.equations.native(selected)

    def send(self, state, item, port, external=(), active=True):
        if port not in self.box_types:
            raise ValueError('send destination missing from source storage schema: ' + str(port))
        # Source send is absent when the local payload is absent.
        active = z3.And(active, item != 0)
        reference = self.free_reference(state, [*external, (active, item)], active)
        kind = z3.Select(state.types, item)
        result = replace(state, types=self._write_array(state.types, reference, kind, active),
                         lengths=self._write_array(state.lengths, reference, z3.Select(state.lengths, item), active))
        for field in self.fields:
            source = self.field(state, item, field)
            index = self.field_index(reference, field)
            result = replace(result,
                present=self._write_array(result.present, index, source.present, active),
                values=self._write_array(result.values, index, source.value, active),
                identities=self._write_array(result.identities, index, source.identity, active),
                orders=self._write_array(result.orders, index,
                    z3.Select(state.orders, self.field_index(item, field)), active))
        old = state.queues[port]
        index = self._first(state, port, lambda ref: z3.Select(state.types, ref) == kind)
        position = z3.If(index >= 0, index, old.length)
        self.obligations.append(z3.Implies(active, z3.And(position >= 0, position < len(self.box_types[port]))))
        box = Queue(self.equations.native(z3.If(z3.And(active, index < 0), old.length + 1, old.length)),
                    self._write_array(old.references, position, reference, active))
        return replace(result, queues={**state.queues, port: box}), reference

    def match(self, state, port, expected):
        compatible = [text_id(t) for t in self.all_types if self.subtype(t, expected)]
        index = self._first(state, port,
                            lambda ref: z3.Or(*[z3.Select(state.types, ref) == t for t in compatible]))
        reference = self.equations.native(z3.If(index >= 0,
            z3.Select(self.queue(state, port).references, index), 0))
        return index, reference

    def pop(self, state, port, index, active=True):
        if port not in state.queues:
            return state
        old = state.queues[port]
        enabled = z3.And(active, index >= 0, index < old.length)
        refs = old.references
        for i in range(len(self.box_types[port])):
            new = z3.If(i + 1 < old.length, z3.Select(old.references, i + 1), 0)
            refs = self._write_array(refs, z3.IntVal(i), new,
                                     z3.And(enabled, i >= index, i < old.length))
        return replace(state, queues={**state.queues, port: Queue(
            self.equations.native(z3.If(enabled, old.length - 1, old.length)), refs)})

    def accept(self, state, port, expected, active=True):
        parent = port.rsplit('::', 1)[0]
        fallback = self.queue(state, port).length == 0
        i, ref = self.match(state, port, expected)
        j, parent_ref = self.match(state, parent, expected)
        chosen = self.equations.native(z3.If(fallback, parent_ref, ref))
        result = self.pop(state, port, i, z3.And(active, z3.Not(fallback)))
        result = self.pop(result, parent, j, z3.And(active, fallback))
        return result, chosen  # chosen == 0 is the source's blocked-accept error.

    def scalar_equal(self, a, b):
        # Container equality uses Python equality, where True == 1. Source
        # expression equality deliberately has stricter Boolean typing.
        def numeric(value):
            return z3.If(Value.is_Boolean(value),
                         Value.Integer(z3.If(boolean_value(value), 1, 0)), value)
        x, y = numeric(a.value), numeric(b.value)
        equal = self.equations.binary('==', Term(x), Term(y))
        nan_shared = z3.And(Value.is_Float(a.value), Value.is_Float(b.value),
                            z3.fpIsNaN(float_value(a.value)), z3.fpIsNaN(float_value(b.value)),
                            a.identity != 0, a.identity == b.identity)
        # The value equation for == also covers None and strings; expression
        # error checks do not apply to Python dictionary equality.
        return z3.Or(nan_shared, boolean_value(equal.value))

    def equal(self, state, left, right):
        clauses = [z3.Select(state.types, left) == z3.Select(state.types, right)]
        for field in self.fields:
            a, b = self.field(state, left, field), self.field(state, right, field)
            clauses += [a.present == b.present,
                        z3.Implies(a.present, self.scalar_equal(a, b))]
        return self.equations.native(z3.Or(left == right, z3.And(*clauses)))

    def remove_equal(self, state, port, matched, active=True):
        index = self._first(state, port, lambda ref: self.equal(state, matched, ref))
        return self.pop(state, port, index, z3.And(active, matched != 0))
