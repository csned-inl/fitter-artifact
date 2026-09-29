"""Eliminate checked constant fields from per-location relation signatures.

At location n, its checked invariant fixes each omitted field to a literal.
The remaining fields plus those literals reconstruct the complete state. Every
incoming edge has already been checked to preserve the destination invariant.
No unknown field, message payload or stored reading is omitted.
"""
from .constant_facts import free_constants
from .lazy_graph import fields


class ConstantStateLayout:
    def __init__(self, program, facts):
        self.paths = {}
        self.sorts = {}
        self.omitted = {}
        for name, row in program['nodes'].items():
            signature = fields(row['compiler'], row['input'])
            kept = [(path, value) for path, value in signature
                    if facts[name].get(path) is None and free_constants(value)]
            self.paths[name] = tuple(path for path, _ in kept)
            self.sorts[name] = tuple(value.sort() for _, value in kept)
            self.omitted[name] = len(signature)-len(kept)

    def pack(self, destination, compiler, state):
        values = dict(fields(compiler, state))
        return tuple(values[path] for path in self.paths[destination])
