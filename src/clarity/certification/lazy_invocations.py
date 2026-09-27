"""Finite static call-site expansion for acyclic decision intervals.

Only source call sites introduce copies. Numeric branches still share joins.
The polynomial size guard abandons this optimization, never source coverage.
Programs with a decision inside an unresolved call retain the recursive backend.
"""
from collections import deque


def invocation_order(graph, entry):
    nodes = graph['nodes']
    limit = max(1, len(nodes) ** 2)
    root = (entry, ())
    pending, adjacent = [root], {}
    while pending:
        current = pending.pop()
        if current in adjacent:
            continue
        if len(adjacent) >= limit:
            return None
        name, stack = current
        node = nodes[name]
        op, source_edges = node['operation'], node['successors']
        edges = []
        if op == 'outcome':
            adjacent[current] = ()
            continue
        if op == 'return':
            if not stack:
                return None
            edges.append((stack[-1], (stack[-1], stack[:-1])))
        elif op == 'call_machine':
            # Recursive source calls are not expanded by this optimization.
            if len(stack) >= len(nodes):
                return None
            target = source_edges['call']
            edges.append((target, (target, (*stack, source_edges['return']))))
        elif op != 'decision':
            edges.extend((target, (target, stack)) for target in set(source_edges.values()))
        error = node['on_exception']
        edges.append((error, (error, stack)))
        adjacent[current] = tuple(dict.fromkeys(edges))
        pending.extend(target for _, target in adjacent[current])
    indegree = dict.fromkeys(adjacent, 0)
    for targets in adjacent.values():
        for _, target in targets:
            indegree[target] += 1
    ready = deque(key for key, degree in indegree.items() if degree == 0)
    order = []
    while ready:
        key = ready.popleft()
        order.append(key)
        for _, target in adjacent[key]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    return (order, adjacent) if len(order) == len(adjacent) else None
