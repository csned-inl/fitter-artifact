"""Ordered bounded source-constraint propagation as shared conditional equations."""
from dataclasses import dataclass

import z3

from .lazy_expressions import Value, Status, Term, scalar, failure, text_id, boolean_value
from .lazy_message_operations import truth
from .lazy_messages import StoredValue


def contains_implies(expr):
    return expr['kind'] == 'binary' and (expr['operator'] == 'implies' or
        contains_implies(expr['left']) or contains_implies(expr['right']))


@dataclass(frozen=True)
class ErrorList:
    count: object
    names: object
    messages: object

    @classmethod
    def empty(cls):
        zero = z3.K(z3.IntSort(), z3.IntVal(0))
        return cls(z3.IntVal(0), zero, zero)


class ConstraintEquations:
    def __init__(self, equations, store, message_equality):
        self.eq, self.store, self.message_equality = equations, store, message_equality
        self.obligations = []

    def error_add(self, errors, name, message, active, capacity, dictionary):
        index = errors.count
        if dictionary:
            for i in reversed(range(capacity)):
                index = z3.If(z3.And(i < errors.count, z3.Select(errors.names, i) == text_id(name)), i, index)
        index = self.eq.native(index)
        return ErrorList(self.eq.native(z3.If(z3.And(active, index == errors.count), errors.count + 1, errors.count)),
            self.eq.native(z3.If(active, z3.Store(errors.names, index, text_id(name)), errors.names)),
            self.eq.native(z3.If(active, z3.Store(errors.messages, index, message), errors.messages)))

    def error_status(self, errors, mode, capacity):
        details = z3.Store(z3.K(z3.IntSort(), z3.IntVal(0)), 0, errors.count)
        for i in range(capacity):
            details = z3.Store(details, 2*i+1, z3.If(i < errors.count, z3.Select(errors.names, i), 0))
            details = z3.Store(details, 2*i+2, z3.If(i < errors.count, z3.Select(errors.messages, i), 0))
        return Status.ConstraintFailure(mode, self.eq.native(details))

    def assign(self, state, expr, context, active):
        if expr['kind'] != 'binary': return state, Status.Success
        op = expr['operator']
        if op == 'and':
            state, status = self.assign(state, expr['left'], context, active)
            state, later = self.assign(state, expr['right'], context, z3.And(active, status == Status.Success))
            return state, z3.If(status == Status.Success, later, status)
        if op != '==' or expr['left']['kind'] != 'reference': return state, Status.Success
        value = self.store.evaluate(state, expr['right'], context)
        targets, status = self.store.canonical_targets(state, self.constraint_key(expr['left']['path'], context),
            z3.And(active, value.status == Status.Success))
        for enabled, key in targets:
            state = self.store.write(state, key, value, enabled)
        return state, self.eq.native(z3.If(active, z3.If(value.status == Status.Success, status, value.status), Status.Success))

    def imply(self, state, expr, context, active):
        if expr['kind'] != 'binary': return state, Status.Success
        if expr['operator'] == 'and':
            state, status = self.imply(state, expr['left'], context, active)
            state, later = self.imply(state, expr['right'], context, z3.And(active, status == Status.Success))
            return state, z3.If(status == Status.Success, later, status)
        if expr['operator'] != 'implies': return state, Status.Success
        condition = self.store.evaluate(state, expr['left'], context)
        state, status = self.assign(state, expr['right'], context,
                                   z3.And(active, condition.status == Status.Success, truth(condition.value)))
        return state, self.eq.native(z3.If(active,
            z3.If(condition.status == Status.Success, status, condition.status), Status.Success))

    def constraint_key(self, path, context):
        prefix = self.store.graph.get('reference_bindings', {}).get(context + '::' + path[0]) if path and context else None
        return ((prefix + '::' + '::'.join(path[1:])).rstrip(':') if prefix else
                ((context + '::') if context else '') + '::'.join(path))

    def defined_targets(self, state, expr, context, result):
        if expr['kind'] != 'binary': return Status.Success
        if expr['operator'] == 'and':
            a = self.defined_targets(state, expr['left'], context, result)
            b = self.defined_targets(state, expr['right'], context, result)
            return z3.If(a == Status.Success, b, a)
        if expr['operator'] == 'implies':
            return self.defined_targets(state, expr['right'], context, result)
        if expr['operator'] == '==' and expr['left']['kind'] == 'reference':
            targets, status = self.store.canonical_targets(state, self.constraint_key(expr['left']['path'], context))
            for guard, key in targets:
                result[key] = z3.Or(result.get(key, z3.BoolVal(False)), guard)
            return status
        return Status.Success

    def flow(self, state, data, defined, active):
        incoming = {}
        status = Status.Success
        for i, flow in enumerate(data['flows']):
            prefix = self.store.graph['system_part'] + '::'
            start = prefix + flow['from_port'].replace('.', '::') + '::'
            end = prefix + flow['to_port'].replace('.', '::') + '::'
            for key in self.store.keys:
                if not key.startswith(start): continue
                cell = self.store.cell(state, key)
                targets, error = self.store.canonical_targets(state, end + key[len(start):], cell.present)
                status = z3.If(status == Status.Success, error, status)
                for guard, target in targets:
                    enabled = z3.And(guard, z3.Not(defined.get(target, z3.BoolVal(False))))
                    if z3.is_false(z3.simplify(enabled)): continue
                    if target not in state.cells:
                        raise ValueError('flow destination absent from source scalar schema: ' + target)
                    # The outer flow loop precedes the insertion-ordered key loop.
                    order = i * (state.next_order + 1) + cell.order
                    incoming.setdefault(target, []).append((key, enabled, order))
        # Each target is processed once, in first-insertion order. Conditional
        # selection avoids constructing every permutation of incoming targets.
        priorities, present = {}, {}
        sentinel = (len(data['flows']) + 1) * (state.next_order + 1)
        for target, rows in incoming.items():
            priority = sentinel
            for _, guard, order in rows:
                priority = z3.If(z3.And(guard, order < priority), order, priority)
            priorities[target] = self.eq.native(priority)
            present[target] = self.eq.native(z3.Or(*[guard for _, guard, _ in rows]))
        consumed = {key: z3.BoolVal(False) for key in incoming}
        for _ in incoming:
            selections = {}
            for key in incoming:
                selected = z3.And(present[key], z3.Not(consumed[key]), *[
                    z3.Or(z3.Not(present[other]), consumed[other], priorities[key] < priorities[other])
                    for other in incoming if other != key])
                selections[key] = self.eq.native(selected)
            for target, rows in incoming.items():
                enabled = z3.And(active, status == Status.Success, selections[target])
                distinct = {}
                for key, guard, _ in rows:
                    distinct[key] = z3.Or(distinct.get(key, z3.BoolVal(False)), guard)
                ambiguous = z3.Sum(*[z3.If(g, 1, 0) for g in distinct.values()]) > 1
                status = self.eq.native(z3.If(z3.And(enabled, ambiguous),
                    failure('multiple flow inputs lack a source aggregation equation: ' + target), status))
                # With one distinct source, duplicate entries still execute
                # in order: a live expression may create a fresh NaN object.
                for key, guard, _ in rows:
                    value = self.store.read(state, key)
                    take = z3.And(enabled, status == Status.Success, guard)
                    state = self.store.write(state, target, value,
                        z3.And(take, value.status == Status.Success, z3.Not(Value.is_Absent(value.value))))
                    status = self.eq.native(z3.If(take, value.status, status))
            consumed = {key: self.eq.native(z3.Or(consumed[key], selections[key])) for key in consumed}
        return state, z3.If(active, status, Status.Success)

    def same_store(self, left, right):
        equal = []
        for key, a in left.cells.items():
            b = right.cells[key]
            if a is b: continue
            av, bv = StoredValue(a.present, a.value, a.identity), StoredValue(b.present, b.value, b.identity)
            equal.append(z3.And(a.present == b.present, z3.Implies(a.present,
                z3.And(a.kind == b.kind, z3.Or(a.kind != 0, self.message_equality.scalar_equal(av, bv))))))
        return self.eq.native(z3.And(*equal))

    def solve(self, state, machines, data, active=True):
        for key in self.store.keys:
            if 'behavior.' in key:
                state = self.store.write(state, key, Term(scalar(False)),
                                         z3.And(active, self.store.cell(state, key).present))
        for owner, current in machines.items():
            labels = self.store.graph['machine_states'].get(owner, [])
            self.obligations.append(z3.Implies(active, z3.Or(*[current == text_id(s) for s in labels])))
            for label in labels:
                state = self.store.write(state, owner + '::behavior.' + label,
                                         Term(Value.Boolean(current == text_id(label))), active)
            for label in labels:
                state = self.store.write(state, 'behavior.' + label, Term(scalar(True)),
                                         z3.And(active, current == text_id(label)))
        for binding in data['bindings']:
            state = self.store.write(state, binding['target'], Term(Value.Absent), active, kind=2)
        constraints = data['constraints']; capacity = len(constraints)
        ordered = [c for c in constraints if contains_implies(c['expression'])]
        ordered += [c for c in constraints if not contains_implies(c['expression'])]
        running, status = z3.BoolVal(active) if type(active) is bool else active, Status.Success
        unresolved = ErrorList.empty()
        for _ in range(data['algorithm']['iteration_limit']):
            before = state; unresolved = ErrorList.empty()
            for row in ordered:
                expr, context = row['expression'], row['context']
                enabled = z3.And(running, status == Status.Success)
                if contains_implies(expr): state, error = self.imply(state, expr, context, enabled)
                elif expr['kind'] == 'binary' and expr['operator'] == '==':
                    state, error = self.assign(state, expr, context, enabled)
                else: continue
                caught = z3.And(Status.is_Failure(error), z3.Or(
                    Status.exception_type(error) == text_id('ValueError'),
                    Status.exception_type(error) == text_id('MissingReference')))
                unresolved = self.error_add(unresolved, row['name'],
                    z3.If(Status.is_Failure(error), Status.message(error), 0),
                    z3.And(enabled, caught), capacity, True)
                status = self.eq.native(z3.If(z3.And(enabled, error != Status.Success, z3.Not(caught)), error, status))
            defined = {}
            for row in constraints:
                error = self.defined_targets(state, row['expression'], row['context'], defined)
                status = self.eq.native(z3.If(z3.And(running, status == Status.Success), error, status))
            state, error = self.flow(state, data, defined, z3.And(running, status == Status.Success))
            status = self.eq.native(z3.If(z3.And(running, status == Status.Success), error, status))
            converged = self.same_store(before, state)
            check = z3.And(running, status == Status.Success, converged)
            status = self.eq.native(z3.If(z3.And(check, unresolved.count > 0),
                                         self.error_status(unresolved, 0, capacity), status))
            failed = ErrorList.empty()
            for row in constraints:
                enabled = z3.And(check, status == Status.Success)
                value = self.store.evaluate(state, row['expression'], row['context'], strict=True)
                status = self.eq.native(z3.If(enabled, value.status, status))
                failed = self.error_add(failed, row['name'], z3.IntVal(0),
                    z3.And(enabled, value.status == Status.Success, z3.Not(boolean_value(value.value))), capacity, False)
            status = self.eq.native(z3.If(z3.And(check, status == Status.Success, failed.count > 0),
                                         self.error_status(failed, 1, capacity), status))
            running = self.eq.native(z3.And(running, status == Status.Success, z3.Not(converged)))
        status = self.eq.native(z3.If(running, self.error_status(unresolved, 2, capacity), status))
        return state, status
