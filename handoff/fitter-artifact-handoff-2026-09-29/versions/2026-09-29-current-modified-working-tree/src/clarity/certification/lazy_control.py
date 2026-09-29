"""Source-bounded call frames and shared local dictionaries for compact equations."""
from dataclasses import dataclass, replace

import z3
from collections import deque


def structural_source_progress(graph):
    """Prove finite decision intervals with shared procedure summaries.

    The source schema independently rejects recursive calls. Each machine is
    summarized once by whether it can return without crossing a decision. A
    call contributes its continuation only in that case. An acyclic resulting
    graph proves finite progress from every source location, including resumes
    inside a machine. No scan bound or call-stack enumeration is introduced.
    """
    nodes, summaries = graph['nodes'], {}
    def can_return(entry, active=()):
        if entry in active:
            raise ValueError('recursive call graph requires a separate progress proof')
        if entry in summaries:
            return summaries[entry]
        pending, seen, result = [entry], set(), False
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            node = nodes[name]
            op, edges = node['operation'], node['successors']
            if op == 'return':
                result = True
                continue
            if op == 'outcome':
                continue
            if op == 'call_machine':
                if can_return(edges['call'], (*active, entry)):
                    pending.append(edges['return'])
            elif op != 'decision':
                pending.extend(edges.values())
            pending.append(node['on_exception'])
        summaries[entry] = result
        return result
    try:
        for node in nodes.values():
            if node['operation'] == 'call_machine':
                can_return(node['successors']['call'])
    except ValueError as error:
        return {'discharged': False, 'reason': str(error)}
    adjacent = {}
    for name, node in nodes.items():
        op, edges = node['operation'], node['successors']
        if op in ('return', 'outcome'):
            adjacent[name] = ()
            continue
        targets = {node['on_exception']}
        if op == 'call_machine':
            if summaries[edges['call']]:
                targets.add(edges['return'])
        elif op != 'decision':
            targets.update(edges.values())
        adjacent[name] = tuple(sorted(targets))
    indegree = dict.fromkeys(nodes, 0)
    for targets in adjacent.values():
        for target in targets:
            indegree[target] += 1
    pending = deque(sorted(name for name, degree in indegree.items() if not degree))
    order = []
    while pending:
        name = pending.popleft()
        order.append(name)
        for target in adjacent[name]:
            indegree[target] -= 1
            if not indegree[target]:
                pending.append(target)
    return {'discharged': len(order) == len(nodes),
            'method': 'acyclic_source_procedure_summaries',
            'can_return_without_decision': dict(sorted(summaries.items())),
            'topological_order': order,
            'cyclic_nodes': sorted(set(nodes) - set(order))}


def machine_return_targets(graph):
    """Match return sites to their source-compatible callers.

    Calls are traversed through their continuation, not their callee. Thus a
    nested callee's return cannot become a return of its caller. Shared bodies
    retain the union of all owners. Unknown ownership conservatively keeps all
    continuations, so this analysis never excludes an unclassified return.
    """
    nodes = graph['nodes']
    callers = {}
    for node in nodes.values():
        if node['operation'] == 'call_machine':
            callers.setdefault(node['successors']['call'], set()).add(node['successors']['return'])
    targets, owned = {}, set()
    for entry, node in nodes.items():
        if node['operation'] != 'enter_machine':
            continue
        pending, seen = [entry], set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            current = nodes[name]
            if current['operation'] == 'return':
                owned.add(name)
                targets.setdefault(name, set()).update(callers.get(entry, ()))
                continue
            if current['operation'] == 'outcome':
                continue
            if current['operation'] == 'call_machine':
                pending.append(current['successors']['return'])
            else:
                pending.extend(current['successors'].values())
            pending.append(current['on_exception'])
    all_targets = set().union(*callers.values()) if callers else set()
    return {name: tuple(sorted(targets.get(name, ()) if name in owned else all_targets))
            for name, node in nodes.items() if node['operation'] == 'return'}


@dataclass(frozen=True)
class Control:
    depth: object
    locals_id: object
    local_values: object
    saved_locals: object
    returns: object
    current: object
    matched: object
    ports: object


class ControlEquations:
    def __init__(self, equations, schema):
        self.equations = equations
        self.names = tuple(schema.local_names)
        self.depth = schema.call_depth
        self.local_capacity = self.depth + 2  # Live frames plus a fresh dictionary.
        self.obligations = []

    def empty(self):
        zero = z3.K(z3.IntSort(), z3.IntVal(0))
        return Control(z3.IntVal(0), z3.IntVal(1), zero, zero, zero, zero, zero, zero)

    def symbolic(self, prefix):
        def array(name): return z3.Array(prefix + '_' + name, z3.IntSort(), z3.IntSort())
        return Control(z3.Int(prefix + '_depth'), z3.Int(prefix + '_locals_id'),
                       *[array(name) for name in ('local_values', 'saved_locals', 'returns',
                                                 'current', 'matched', 'ports')])

    def _store(self, array, key, value, active):
        return self.equations.native(z3.If(active, z3.Store(array, key, value), array))

    def local_index(self, frame, name):
        if name not in self.names:
            raise ValueError('local name absent from source storage inventory: ' + name)
        return frame * len(self.names) + self.names.index(name)

    def local(self, state, name):
        if name not in self.names:
            return z3.IntVal(0)
        return z3.Select(state.local_values, self.local_index(state.locals_id, name))

    def set_local(self, state, name, reference, active=True):
        return replace(state, local_values=self._store(state.local_values,
            self.local_index(state.locals_id, name), reference, active))

    def live_locals(self, state):
        return [(z3.BoolVal(True), state.locals_id)] + [
            (i < state.depth, z3.Select(state.saved_locals, i)) for i in range(self.depth)]

    def roots(self, state):
        roots = []
        for live, frame in self.live_locals(state):
            for name in self.names:
                ref = z3.Select(state.local_values, self.local_index(frame, name))
                roots.append((z3.And(live, ref != 0), ref))
        for i in range(self.depth + 1):
            ref = z3.Select(state.matched, i)
            roots.append((z3.And(i <= state.depth, ref != 0), ref))
        return roots

    def well_formed(self, state):
        return z3.And(state.depth >= 0, state.depth <= self.depth,
            *[z3.Implies(live, z3.And(frame >= 1, frame <= self.local_capacity))
              for live, frame in self.live_locals(state)])

    def fresh_locals(self, state, active=True):
        frame = z3.IntVal(0)
        for i in reversed(range(1, self.local_capacity + 1)):
            unused = z3.And(*[z3.Or(z3.Not(live), old != i)
                              for live, old in self.live_locals(state)])
            frame = z3.If(unused, i, frame)
        frame = self.equations.native(frame)
        self.obligations.append(z3.Implies(active, frame != 0))
        values = state.local_values
        for name in self.names:
            values = self._store(values, self.local_index(frame, name), z3.IntVal(0), active)
        return replace(state, local_values=values,
                       locals_id=self.equations.native(z3.If(active, frame, state.locals_id)))

    def push(self, state, continuation, active=True):
        self.obligations.append(z3.Implies(active, state.depth < self.depth))
        new_depth = state.depth + 1
        return replace(state,
            depth=self.equations.native(z3.If(active, new_depth, state.depth)),
            saved_locals=self._store(state.saved_locals, state.depth, state.locals_id, active),
            returns=self._store(state.returns, state.depth, continuation, active),
            # Until enter_machine executes, the current frame has the saved
            # frame's contents, exactly as in the source evaluator.
            current=self._store(state.current, new_depth, z3.Select(state.current, state.depth), active),
            matched=self._store(state.matched, new_depth, z3.Select(state.matched, state.depth), active),
            ports=self._store(state.ports, new_depth, z3.Select(state.ports, state.depth), active))

    def pop(self, state, active=True):
        self.obligations.append(z3.Implies(active, state.depth > 0))
        new_depth = state.depth - 1
        continuation = z3.Select(state.returns, new_depth)
        return replace(state,
            depth=self.equations.native(z3.If(active, new_depth, state.depth)),
            locals_id=self.equations.native(z3.If(active,
                z3.Select(state.saved_locals, new_depth), state.locals_id))), continuation

    def enter_machine(self, state, current, active=True):
        return replace(state,
            current=self._store(state.current, state.depth, current, active),
            matched=self._store(state.matched, state.depth, z3.IntVal(0), active),
            ports=self._store(state.ports, state.depth, z3.IntVal(0), active))

    def match(self, state, reference, port=None, active=True):
        result = replace(state, matched=self._store(state.matched, state.depth, reference, active))
        if port is not None:
            result = replace(result, ports=self._store(state.ports, state.depth, port, active))
        return result
