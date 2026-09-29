"""Checked constant propagation over shared source equations.

The lattice contains a literal constant or unknown, independently per field.
Every source edge is included, including exceptions and decision resumes.
Changing values become unknown at joins. No time or scan bound is assumed.
"""
import heapq
import json
import os
import time

import z3

from .lazy_graph import fields
from .type_facts import edges
from .lazy_control import machine_return_targets
from .lazy_expressions import Status


def free_constants(term):
    pending, seen = [term], set()
    while pending:
        value = pending.pop()
        if value.get_id() in seen:
            continue
        seen.add(value.get_id())
        if z3.is_const(value) and value.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            return True
        pending.extend(value.children())
    return False


class ConstantTransfer:
    def __init__(self, row, incoming):
        self.row = row
        self.known = {}
        # fields() creates packed accessor expressions. Keep their ASTs alive
        # for as long as their IDs are memoization keys, preventing ID reuse.
        self.input_fields = fields(row['compiler'], row['input'])
        self.output_fields = fields(row['compiler'], row['relation'].state)
        self.definitions = {}
        for equation in row['compiler'].eq.equations:
            if not z3.is_eq(equation):
                continue
            pending = [tuple(equation.children())]
            while pending:
                lhs, rhs = pending.pop()
                if lhs.eq(rhs):
                    continue
                if z3.is_const(lhs) and lhs.decl().kind() == z3.Z3_OP_UNINTERPRETED:
                    if lhs.get_id() in self.definitions:
                        raise ValueError('duplicate source SSA definition')
                    self.definitions[lhs.get_id()] = rhs
                elif lhs.decl().kind() == z3.Z3_OP_DT_CONSTRUCTOR:
                    # Typed SSA writes use Float(payload) = expression, etc.
                    # Applying the corresponding selector to both sides yields
                    # payload = selector(expression). This follows from the
                    # original equality even for an optional/mixed RHS.
                    datatype = lhs.sort()
                    index = next(i for i in range(datatype.num_constructors())
                                 if datatype.constructor(i).eq(lhs.decl()))
                    pending.extend((child, z3.simplify(datatype.accessor(index,j)(rhs)))
                                   for j,child in enumerate(lhs.children()))
        self.memo = {}
        self.unknown_children = {}
        self.terms = {}
        self.dependents = {}
        self.set_incoming(incoming)

    def set_incoming(self, incoming):
        """Invalidate exactly the cached ancestors of changed field facts.

        The evaluator still applies the same transfer rules. Unaffected DAG
        results remain valid because every direct dependency is recorded.
        The independent edge checker constructs fresh evaluators.
        """
        known = {}
        for path, term in self.input_fields:
            value = incoming.get(path)
            # Replacing a composite expression is also justified by its field
            # equality. Only constant equalities established on incoming edges
            # enter this table.
            if value is not None:
                if value.sort() != term.sort():
                    raise ValueError('constant evidence changes a field sort')
                if not free_constants(term):
                    if not z3.is_true(z3.simplify(term == value)):
                        raise ValueError('constant evidence contradicts the source field layout')
                    continue
                previous = known.get(term.get_id())
                if previous is not None and not previous.eq(value):
                    raise ValueError('inconsistent constants for the same source expression')
                known[term.get_id()] = value
        changed = {key for key in self.known.keys() | known.keys()
                   if not _same(self.known.get(key), known.get(key))}
        pending = list(changed)
        while pending:
            identity = pending.pop()
            self.memo.pop(identity, None)
            for parent in self.dependents.get(identity, ()):
                if parent not in changed:
                    changed.add(parent)
                    pending.append(parent)
        self.known = known

    def evaluate(self, expression):
        pending = [(expression, False)]
        while pending:
            term, ready = pending.pop()
            identity = term.get_id()
            self.terms[identity] = term
            if identity in self.memo:
                continue
            if identity in self.known:
                self.memo[identity] = self.known[identity]
                continue
            rhs = self.definitions.get(identity)
            children = [rhs] if rhs is not None else list(term.children())
            for child in children:
                self.dependents.setdefault(child.get_id(), set()).add(identity)
            if not ready:
                pending.append((term, True))
                pending.extend((child, False) for child in children if child.get_id() not in self.memo)
                continue
            if rhs is not None:
                result = self.memo[rhs.get_id()]
            else:
                # Fold one operation at a time. Unknown child expressions
                # become independent symbols, preserving identical-child sharing.
                # A constant under this overapproximation is valid for every
                # concrete child value. This avoids recursively simplifying the
                # same large source expression at every ancestor.
                arguments = []
                for child in children:
                    value = self.memo[child.get_id()]
                    if value is None:
                        if child.get_id() not in self.unknown_children:
                            self.unknown_children[child.get_id()] = (child, z3.FreshConst(child.sort(), 'unknown_constant_child'))
                        value = self.unknown_children[child.get_id()][1]
                    arguments.append(value)
                build = {z3.Z3_OP_AND: z3.And, z3.Z3_OP_OR: z3.Or,
                         z3.Z3_OP_DISTINCT: z3.Distinct}.get(term.decl().kind(), term.decl())
                simplified = z3.simplify(build(*arguments)) if children else term
                result = None if free_constants(simplified) else simplified
            self.memo[identity] = result
        return self.memo[expression.get_id()]

    def result(self):
        return {path: self.evaluate(term) for path, term in self.output_fields}


def _same(a, b):
    return a is None and b is None or a is not None and b is not None and a.eq(b)


class ConstantFacts(dict):
    """Per-node invariants plus an independently checked reachable-node set."""
    def __init__(self, values, reachable):
        super().__init__(values)
        self.reachable = frozenset(reachable)


def edge_condition(program, name, target):
    row = program['nodes'][name]
    relation, compiler = row['relation'], row['compiler']
    ordinary = relation.successor == compiler.ids[target]
    if relation.outcome in ('decision', 'terminal', 'error'):
        ordinary = z3.And(relation.status != Status.Success, ordinary)
    if (relation.outcome == 'decision' and
            target == program['graph']['nodes'][name]['successors']['resume']):
        return z3.Or(ordinary, relation.status == Status.Success)
    return ordinary


def impossible_edge(program, name, target, transfer):
    value = transfer.evaluate(edge_condition(program, name, target))
    return value is not None and z3.is_false(value)


def derive_constant_facts(program, *, conditional=True):
    rows, graph = program['nodes'], program['graph']
    root = graph['initial_entry']
    keys = [path for path, _ in fields(rows[root]['compiler'], rows[root]['input'])]
    unknown = dict.fromkeys(keys)
    facts = {root: unknown.copy()}
    returns = machine_return_targets(graph)
    adjacent = {name: edges(graph, name, returns) for name in rows}
    # Reverse postorder is the standard forward-dataflow worklist ordering.
    # It changes scheduling only. Every changed fact still reaches every edge.
    postorder, seen, stack = [], set(), [(root, False)]
    while stack:
        name, ready = stack.pop()
        if ready:
            postorder.append(name)
        elif name not in seen:
            seen.add(name)
            stack.append((name, True))
            stack.extend((target, False) for target in reversed(adjacent[name])
                         if target not in seen)
    priority = {name: index for index, name in enumerate(reversed(postorder))}
    pending, queued = [(priority[root], root)], {root}
    transfers, previous_outputs = {}, {}
    executable_edges = set()
    iterations, started = 0, time.monotonic()
    while pending:
        _, name = heapq.heappop(pending)
        queued.remove(name)
        if name not in transfers:
            transfers[name] = ConstantTransfer(rows[name], facts[name])
        else:
            transfers[name].set_incoming(facts[name])
        outgoing = transfers[name].result()
        old_output = previous_outputs.get(name)
        changed_keys = keys if old_output is None else [
            key for key in keys if not _same(old_output[key], outgoing[key])]
        previous_outputs[name] = outgoing
        for target in adjacent[name]:
            if conditional and impossible_edge(program, name, target, transfers[name]):
                continue
            # A newly feasible edge must contribute ALL fields, even when only
            # its guard changed and its outgoing field values stayed the same.
            edge = (name, target)
            contribution_keys = changed_keys if edge in executable_edges else keys
            executable_edges.add(edge)
            previous = facts.get(target)
            changed = previous is None
            if previous is None:
                facts[target] = outgoing.copy()
            else:
                # Unchanged contributions from this edge were already joined.
                # Meet can only discard a previous constant, never restore it.
                for key in contribution_keys:
                    if previous[key] is not None and not _same(previous[key], outgoing[key]):
                        previous[key] = None
                        changed = True
            if changed and target not in queued:
                heapq.heappush(pending, (priority[target], target))
                queued.add(target)
        iterations += 1
        if os.environ.get('CLARITY_SOURCE_SOLVER_PROGRESS') and iterations % 100 == 0:
            print(json.dumps({'source_solver': 'constant_dataflow', 'transfers': iterations,
                              'queued': len(queued), 'seconds': round(time.monotonic()-started, 3)}), flush=True)
    return ConstantFacts({name: facts.get(name, unknown.copy()) for name in rows}, facts)


def check_constant_facts(program, facts):
    """Replay initiation and edge preservation without trusting convergence."""
    rows, graph = program['nodes'], program['graph']
    root = graph['initial_entry']
    keys = {path for path, _ in fields(rows[root]['compiler'], rows[root]['input'])}
    if set(facts) != set(rows) or any(set(row) != keys for row in facts.values()):
        raise ValueError('constant evidence omits source fields or nodes')
    for name, row in rows.items():
        sorts = {path: term.sort() for path, term in fields(row['compiler'], row['input'])}
        if any(value is not None and (value.sort() != sorts[key] or free_constants(value))
               for key, value in facts[name].items()):
            raise ValueError('invalid constant evidence')
    reachable = getattr(facts, 'reachable', frozenset(rows))
    if root not in reachable or not reachable <= set(rows):
        raise ValueError('constant evidence has an invalid reachable-node set')
    initial = ConstantTransfer(rows[root], dict.fromkeys(keys))
    for path, term in initial.input_fields:
        if facts[root][path] is not None and not _same(facts[root][path], initial.evaluate(term)):
            raise ValueError('constant evidence not established at initialization: '+path)
    returns = machine_return_targets(graph)
    for name, row in rows.items():
        if name not in reachable:
            continue
        incoming = dict.fromkeys(keys) if name == root else facts[name]
        transfer = ConstantTransfer(row, incoming)
        outgoing = transfer.result()
        for target in edges(graph, name, returns):
            if impossible_edge(program, name, target, transfer):
                continue
            if target not in reachable:
                raise ValueError('constant evidence omits a feasible source edge: '+name+' -> '+target)
            if any(value is not None and not _same(value, outgoing[key])
                   for key, value in facts[target].items()):
                raise ValueError('constant evidence not preserved on edge ' + name + ' -> ' + target)
    return True


def constant_premises(row, facts):
    return [term == facts[path] for path, term in fields(row['compiler'], row['input'])
            if facts.get(path) is not None]
