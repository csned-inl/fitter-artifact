"""Lower source message and call nodes to compact numerical/storage equations."""
from dataclasses import dataclass, replace

import z3

from .lazy_control import ControlEquations
from .lazy_expressions import Value, Status, scalar, text_id, failure, boolean_value, float_value, integer_value
from .lazy_messages import MessageEquations, StoredValue


def truth(value):
    """Python truth of a raw scalar (machine guards are not Boolean-only)."""
    return z3.Or(boolean_value(value),
        z3.And(Value.is_Integer(value), integer_value(value) != 0),
        z3.And(Value.is_Float(value), z3.Not(z3.fpIsZero(float_value(value)))),
        z3.And(Value.is_Text(value), Value.text(value) != text_id('')))


@dataclass(frozen=True)
class OperationResult:
    messages: object
    control: object
    machines: dict
    scalar_writes: tuple
    successor: object
    status: object


class MessageOperationCompiler:
    supported = frozenset(('enter_block', 'send_copy', 'accept_copy', 'call_machine',
                           'enter_machine', 'machine_from_state', 'match_trigger',
                           'finish_machine_transition', 'return'))

    def __init__(self, equations, graph, schema):
        self.equations, self.graph = equations, graph
        self.messages = MessageEquations(equations, schema, graph.get('item_type_parents', {}))
        self.control = ControlEquations(equations, schema)
        self.node_ids = {name: i + 1 for i, name in enumerate(sorted(graph['nodes']))}

    def lower(self, identity, messages, control, machines, raw_value=None):
        node = self.graph['nodes'][identity]
        op, data, edges = node['operation'], node['data'], node['successors']
        if op not in self.supported:
            raise ValueError('not a message/control operation: ' + op)
        m, c = self.messages, self.control
        writes = []
        status = Status.Success

        def dest(edge): return z3.IntVal(self.node_ids[edges[edge]])

        def copy_fields(reference, prefix, enabled):
            # Python iterates payload attributes in insertion order. Store
            # propagation can observe the resulting scalar-dictionary order.
            for rank in range(len(m.fields)):
                for field in m.fields:
                    value = m.field(messages, reference, field)
                    order = z3.Select(messages.orders, m.field_index(reference, field))
                    writes.append((prefix + field.replace('.', '::'), value,
                                   z3.And(enabled, value.present, order == rank)))

        if op == 'enter_block':
            if data['fresh_locals']:
                control = c.fresh_locals(control)
            for name, kind in data['declarations'].items():
                messages, ref = m.declare(messages, kind, c.roots(control))
                control = c.set_local(control, name, ref)
            successor = dest('next')
        elif op == 'send_copy':
            ref = c.local(control, data['payload'])
            if data['destination']:
                messages, _ = m.send(messages, ref, data['destination'], c.roots(control))
                successor = z3.If(ref != 0, dest('sent'), dest('absent'))
            else:
                successor = dest('absent')
        elif op == 'accept_copy':
            messages, ref = m.accept(messages, data['port'], data['expected_type'])
            if data['destination']:
                control = c.set_local(control, data['destination'], ref, ref != 0)
                copy_fields(ref, data['context'] + '::' + data['destination'] + '::', ref != 0)
            status = z3.If(ref != 0, Status.Success,
                          failure(f"blocked source accept: {data['port']} expects {data['expected_type']}"))
            successor = z3.If(ref != 0, dest('accepted'), self.node_ids[node['on_exception']])
        elif op == 'call_machine':
            control = c.push(control, dest('return'))
            successor = dest('call')
        elif op == 'enter_machine':
            control = c.enter_machine(control, machines.get(data['instance'], z3.IntVal(0)))
            successor = dest('next')
        elif op == 'machine_from_state':
            current = z3.Select(control.current, control.depth)
            successor = z3.If(current == text_id(data['expected']), dest('true'), dest('false'))
        elif op == 'match_trigger':
            control = c.match(control, z3.IntVal(0))
            successor = dest('matched')
            if data['type']:
                if data['port']:
                    port = data['instance'] + '::' + data['port'].replace('.', '::')
                    _, ref = m.match(messages, port, data['type'])
                    control = c.match(control, ref, z3.IntVal(text_id(port)))
                    successor = z3.If(ref != 0, dest('matched'), dest('absent'))
                    if data['destination']:
                        copy_fields(ref, data['instance'] + '::' + data['destination'] + '::', ref != 0)
                else:
                    if raw_value is None:
                        raise ValueError('flag trigger requires the raw source-store truth callback')
                    key = data['instance'] + '::' + data['type']
                    enabled = raw_value(key)
                    successor = z3.If(enabled, dest('matched'), dest('absent'))
                    writes.append((key, StoredValue(z3.BoolVal(True), scalar(False), z3.IntVal(0)), enabled))
        elif op == 'finish_machine_transition':
            ref = z3.Select(control.matched, control.depth)
            port = z3.Select(control.ports, control.depth)
            for name in m.box_types:
                messages = m.remove_equal(messages, name, ref, port == text_id(name))
            machines = {**machines, data['instance']: z3.IntVal(text_id(data['to']))}
            successor = dest('next')
        else:  # return
            control, successor = c.pop(control)
        return OperationResult(messages, control, machines, tuple(writes),
                               self.equations.native(successor), status)

    def write_local_attribute(self, messages, control, name, field, value, identity, active):
        """Assignment mutates a shared local payload before its scalar write."""
        ref = self.control.local(control, name)
        enabled = z3.And(active, ref != 0)
        return self.messages.write_field(messages, ref, field, value, identity, enabled), ref != 0
