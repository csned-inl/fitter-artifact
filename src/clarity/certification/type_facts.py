"""Finite, checked constructor propagation over the lowered source graph.

The analysis interprets shared equations, not paths. Unknown operations produce
the full constructor set. It includes exceptional edges and decision resumes.
Facts concern raw cell contents, including stale contents of absent cells.
"""
from collections import deque

import z3

from .lazy_expressions import Value


KINDS = frozenset(('Absent', 'Boolean', 'Integer', 'Float', 'Text'))
BOOLS = frozenset((False, True))
MODES = frozenset((0, 1, 2))
CONSTRUCTORS = {Value.constructor(i).get_id(): str(Value.constructor(i).name())
                for i in range(Value.num_constructors())}
TESTS = {Value.recognizer(i).get_id(): str(Value.constructor(i).name())
         for i in range(Value.num_constructors())}


def tracked(state):
    return {(key, part): getattr(cell, part)
            for key, cell in state.store.cells.items()
            for part in ('value', 'present', 'kind')}


def top(part):
    return {'value': KINDS, 'present': BOOLS, 'kind': MODES}[part]


def edges(graph, name, returns=None):
    node = graph['nodes'][name]
    if node['operation'] == 'outcome':
        return ()
    if node['operation'] == 'return':
        from .lazy_control import machine_return_targets
        targets = set((machine_return_targets(graph) if returns is None else returns)[name])
    else:
        targets = set(node['successors'].values())
        if node['operation'] == 'call_machine':
            targets.discard(node['successors']['return'])
    targets.add(node['on_exception'])
    return tuple(sorted(targets))


class AbstractEquations:
    """Nonrecursive DAG interpreter with conservative finite transfer rules."""
    def __init__(self, row, facts):
        self.inputs = {expr.get_id(): facts[key] for key, expr in tracked(row['input']).items()
                       if z3.is_const(expr) and expr.decl().kind() == z3.Z3_OP_UNINTERPRETED}
        self.definitions = {}
        for equation in row['compiler'].eq.equations:
            if not z3.is_eq(equation):
                continue
            left, right = equation.children()
            if z3.is_const(left) and left.decl().kind() == z3.Z3_OP_UNINTERPRETED:
                if left.get_id() in self.definitions:
                    raise ValueError('duplicate SSA definition')
                self.definitions[left.get_id()] = right
        self.memo = {}

    def evaluate(self, expression):
        pending = [(expression, False)]
        while pending:
            term, ready = pending.pop()
            identity = term.get_id()
            if identity in self.memo:
                continue
            rhs = self.definitions.get(identity)
            children = [rhs] if rhs is not None else list(term.children())
            if not ready:
                pending.append((term, True))
                pending.extend((child, False) for child in children if child.get_id() not in self.memo)
                continue
            if rhs is not None:
                value = self.memo[rhs.get_id()]
            elif identity in self.inputs:
                value = self.inputs[identity]
            elif term.sort() == Value and term.decl().get_id() in CONSTRUCTORS:
                value = frozenset((CONSTRUCTORS[term.decl().get_id()],))
            elif z3.is_true(term) or z3.is_false(term):
                value = frozenset((z3.is_true(term),))
            elif z3.is_int_value(term):
                value = frozenset((term.as_long(),))
            else:
                args = [self.memo[child.get_id()] for child in children]
                value = self.operation(term, args)
            self.memo[identity] = value
        return self.memo[expression.get_id()]

    @staticmethod
    def operation(term, args):
        kind = term.decl().kind()
        if kind == z3.Z3_OP_ITE:
            condition = args[0] or BOOLS
            chosen = [args[1] if truth else args[2] for truth in condition]
            return None if any(value is None for value in chosen) else frozenset().union(*chosen)
        if term.decl().get_id() in TESTS:
            values = args[0] or KINDS
            return frozenset(value == TESTS[term.decl().get_id()] for value in values)
        if kind == z3.Z3_OP_NOT:
            return frozenset(not value for value in (args[0] or BOOLS))
        if kind in (z3.Z3_OP_AND, z3.Z3_OP_OR):
            values = [value or BOOLS for value in args]
            if kind == z3.Z3_OP_AND:
                return frozenset(([True] if all(True in v for v in values) else []) +
                                 ([False] if any(False in v for v in values) else []))
            return frozenset(([False] if all(False in v for v in values) else []) +
                             ([True] if any(True in v for v in values) else []))
        if kind == z3.Z3_OP_EQ:
            if term.arg(0).eq(term.arg(1)):
                return frozenset((True,))
            # Constructor equality does not imply equality of their payloads.
            if term.arg(0).sort() == Value:
                if args[0] is not None and args[1] is not None and not args[0] & args[1]:
                    return frozenset((False,))
            elif term.arg(0).sort() in (z3.BoolSort(), z3.IntSort()) and all(v is not None for v in args):
                return frozenset(a == b for a in args[0] for b in args[1])
        return KINDS if term.sort() == Value else BOOLS if z3.is_bool(term) else None


def transfer(row, facts):
    abstract = AbstractEquations(row, facts)
    result = {}
    for key, expr in tracked(row['relation'].state).items():
        inferred = abstract.evaluate(expr)
        # Storage modes have a finite supported domain. Do not silently narrow
        # an unsupported mode to that domain.
        if key[1] == 'kind' and (inferred is None or not inferred <= MODES):
            raise ValueError('unsupported storage mode in source equations')
        result[key] = top(key[1]) if inferred is None else inferred
    return result


def derive_type_facts(program):
    from .lazy_control import machine_return_targets
    graph, rows = program['graph'], program['nodes']
    returns = machine_return_targets(graph)
    keys = tuple(tracked(next(iter(rows.values()))['input']))
    unknown = {key: top(key[1]) for key in keys}
    root = graph['initial_entry']
    facts = {root: unknown.copy()}
    pending, queued = deque((root,)), {root}
    while pending:
        name = pending.popleft()
        queued.remove(name)
        outgoing = transfer(rows[name], facts[name])
        for target in edges(graph, name, returns):
            previous = facts.get(target)
            merged = outgoing.copy() if previous is None else {
                key: previous[key] | outgoing[key] for key in keys}
            if merged != previous:
                facts[target] = merged
                if target not in queued:
                    pending.append(target)
                    queued.add(target)
    # No unreachability claim is used for optimization.
    return {name: facts.get(name, unknown.copy()) for name in rows}


def check_type_facts(program, facts):
    """Check initiation and every edge, including error and resume edges.

    This replays transfer rules rather than trusting worklist convergence or
    proposed facts. The constructor/Boolean rules are the small trusted base.
    """
    from .lazy_control import machine_return_targets
    rows, graph = program['nodes'], program['graph']
    returns = machine_return_targets(graph)
    if set(facts) != set(rows):
        raise ValueError('type evidence omits source nodes')
    keys = set(tracked(next(iter(rows.values()))['input']))
    for name in rows:
        if set(facts[name]) != keys or any(not values or not values <= top(key[1])
                                         for key, values in facts[name].items()):
            raise ValueError('invalid type evidence field set or domain')
    # Root input is the literal empty state. Analyze it without restrictions.
    root = graph['initial_entry']
    for name in rows:
        incoming = {key: top(key[1]) for key in keys} if name == root else facts[name]
        outgoing = transfer(rows[name], incoming)
        for target in edges(graph, name, returns):
            if any(not values <= facts[target][key] for key, values in outgoing.items()):
                raise ValueError('type evidence not preserved on edge ' + name + ' -> ' + target)
    return True


def symbolic_value(prefix, kinds):
    """Surjective representation of exactly the admitted constructors."""
    constructors = {'Boolean': (Value.Boolean, z3.BoolSort()),
                    'Integer': (Value.Integer, z3.IntSort()),
                    'Float': (Value.Float, z3.Float64()),
                    'Text': (Value.Text, z3.IntSort())}
    if not kinds or not kinds <= KINDS:
        raise ValueError('invalid constructor domain')
    if kinds == KINDS:
        return z3.Const(prefix, Value)
    values = [Value.Absent if kind == 'Absent' else
              constructors[kind][0](z3.Const(prefix + '_' + kind, constructors[kind][1]))
              for kind in sorted(kinds)]
    result = values[-1]
    for index, value in enumerate(values[:-1]):
        result = z3.If(z3.Bool(prefix + '_select_' + str(index)), value, result)
    return result


def choose_scalar_layout(program, facts):
    """One reversible raw-value layout shared by all source locations."""
    root = program['graph']['initial_entry']
    domains = {key: frozenset(('Absent',))
               for key in program['nodes'][root]['input'].store.cells}
    for name, row in program['nodes'].items():
        incoming = facts[name]
        outgoing = transfer(row, incoming)
        for key in domains:
            if name != root:
                domains[key] |= incoming[key, 'value']
            domains[key] |= outgoing[key, 'value']
    return {key: tuple(sorted(kinds)) for key, kinds in domains.items()}


def packed_value(value, kinds):
    """Encode raw tagged values injectively on their checked constructor set.

    Absent has no payload. Canonicalizing that variant does not erase a stale
    Float stored in a cell whose separate Cell.present flag is false.
    """
    nonabsent = set(kinds) - {'Absent'}
    if not nonabsent:
        return ()
    if len(nonabsent) != 1:
        return (('value', value),)
    kind = next(iter(nonabsent))
    accessors = {'Boolean': Value.boolean, 'Integer': Value.integer,
                 'Float': Value.floating, 'Text': Value.text}
    testers = {'Boolean': Value.is_Boolean, 'Integer': Value.is_Integer,
               'Float': Value.is_Float, 'Text': Value.is_Text}
    payload = accessors[kind](value)
    if 'Absent' not in kinds:
        return (('payload', z3.simplify(payload)),)
    present = testers[kind](value)
    zero = {'Boolean': z3.BoolVal(False), 'Integer': z3.IntVal(0),
            'Float': z3.FPVal(0.0, z3.Float64()), 'Text': z3.IntVal(0)}[kind]
    return (('has_payload', z3.simplify(present)),
            ('payload', z3.simplify(z3.If(present, payload, zero))))
