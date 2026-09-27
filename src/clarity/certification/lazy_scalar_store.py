"""Versioned scalar storage, live bindings and aliases without path enumeration."""
from dataclasses import dataclass, replace

import z3

from .lazy_expressions import Value, Status, Term, scalar, failure


@dataclass(frozen=True)
class Cell:
    present: object
    value: object
    identity: object
    kind: object  # 0: scalar; 1: stored alias; 2: live binding.
    order: object = z3.IntVal(0)


@dataclass(frozen=True)
class Store:
    cells: dict
    version: int
    next_order: object = z3.IntVal(0)


class StoreEquations:
    def __init__(self, equations, graph):
        self.equations, self.graph = equations, graph
        self.aliases = dict(graph.get('stored_aliases', {}))
        self.bindings = {}
        keys = {k for k in graph['storage'] if not k.startswith('$')}
        from .lazy_storage import source_storage_schema
        keys.update(source_storage_schema(graph).scalar_locations)
        initial = graph['nodes'][graph['initial_entry']]['data']
        self.aliases.update(initial.get('stored_aliases', {}))
        keys.update(self.aliases); keys.update(self.aliases.values())
        keys.update(p['qualified_name'] for p in initial.get('parameters', []))
        keys.update(row['target'] for row in initial.get('values', []))
        keys.update(graph.get('initial_transition_targets', []))
        keys.add('dt')
        for owner, attrs in graph.get('part_attribute_types', {}).items():
            keys.update(owner + '::' + name for name in attrs)
        for owner, states in graph.get('machine_states', {}).items():
            keys.update(owner + '::behavior.' + name for name in states)
            keys.update('behavior.' + name for name in states)
        for node in graph['nodes'].values():
            data = node['data']
            if node['operation'] == 'initialize_existing_runtime': data = data['constraints']
            elif node['operation'] != 'solve_source_constraints': continue
            for binding in data['bindings']:
                target = binding['target']
                record = (binding['expression'], target.rsplit('::', 1)[0])
                if target in self.bindings and self.bindings[target] != record:
                    raise ValueError('multiple different live binding expressions: ' + target)
                self.bindings[target] = record
                keys.add(target)
        self.keys = tuple(sorted(keys))
        self.version = 0
        self.read_cache = {}

    def empty(self):
        absent = Cell(z3.BoolVal(False), Value.Absent, z3.IntVal(0), z3.IntVal(0))
        return Store({key: absent for key in self.keys}, self._version())

    def symbolic(self, prefix, facts=None):
        from .type_facts import KINDS, symbolic_value
        facts = facts or {}
        def present(key, name):
            values = facts.get((key, 'present'))
            return z3.BoolVal(next(iter(values))) if values is not None and len(values) == 1 else z3.Bool(name)
        def mode(key, name):
            values = facts.get((key, 'kind'))
            if values is not None:
                options = sorted(values)
                result = z3.IntVal(options[-1])
                for index, value in enumerate(options[:-1]):
                    result = z3.If(z3.Bool(name + '_select_' + str(index)), value, result)
                return result
            return z3.Int(name) if key in self.aliases or key in self.bindings else z3.IntVal(0)
        return Store({key: Cell(present(key, f'{prefix}_{i}_present'),
                                symbolic_value(f'{prefix}_{i}_value', facts.get((key, 'value'), KINDS)),
                                z3.Int(f'{prefix}_{i}_identity'),
                                mode(key, f'{prefix}_{i}_kind'), z3.Int(f'{prefix}_{i}_order'))
                      for i, key in enumerate(self.keys)}, self._version(), z3.Int(prefix + '_next_order'))

    def _version(self):
        self.version += 1
        return self.version

    def cell(self, state, key):
        return state.cells.get(key, Cell(z3.BoolVal(False), Value.Absent, z3.IntVal(0), z3.IntVal(0)))

    def write(self, state, key, term, active=True, *, kind=0, present=True):
        if key not in state.cells:
            raise ValueError('source write absent from finite scalar schema: ' + key)
        old = state.cells[key]
        eq = self.equations
        value = eq.bind(z3.If(active, term.value, old.value), identity=
                        eq.native(z3.If(active, term.identity, old.identity)))
        new = Cell(eq.native(z3.If(active, z3.BoolVal(present), old.present)),
                   value.value, value.identity,
                   eq.native(z3.If(active, z3.IntVal(kind), old.kind)),
                   eq.native(z3.If(z3.And(active, present, z3.Not(old.present)), state.next_order, old.order)))
        return Store({**state.cells, key: new}, self._version(), eq.native(state.next_order +
                     z3.If(z3.And(active, present, z3.Not(old.present)), 1, 0)))

    def canonical_targets(self, state, key, active=True, seen=()):
        if key in seen:
            return [], z3.If(active, failure('cyclic source binding: ' + key), Status.Success)
        cell = self.cell(state, key)
        alias = z3.And(cell.present, cell.kind == 1)
        if key not in self.aliases or z3.is_false(z3.simplify(alias)):
            return [(active, key)], Status.Success
        targets, status = self.canonical_targets(state, self.aliases[key],
            z3.And(active, alias), (*seen, key))
        return [(z3.And(active, z3.Not(alias)), key), *targets], status

    def lookup_order(self, path, context):
        ref = self.graph.get('reference_bindings', {}).get(context + '::' + path[0]) if path and context else None
        absolute = ref.split('::') + path[1:] if ref else path
        candidates = ['::'.join(absolute)] if ref else []
        if context: candidates.append(context + '::' + '.'.join(path))
        candidates.extend(('.'.join(path), '::'.join(absolute)))
        if context: candidates.append(context + '::' + '::'.join(path))
        if self.graph.get('system_part'): candidates.append(self.graph['system_part'] + '::' + '::'.join(path))
        return list(dict.fromkeys(candidates))

    def evaluate(self, state, expression, context='', strict=False, active=()):
        def lookup(expr):
            result = Term(Value.Absent)
            candidates = expr.get('lookup_order') or self.lookup_order(expr['path'], context)
            for key in reversed(candidates):
                cell = self.cell(state, key)
                if z3.is_false(z3.simplify(cell.present)): continue
                current = self.read(state, key, active)
                result = self.equations.bind(z3.If(cell.present, current.value, result.value),
                    z3.If(cell.present, current.status, result.status),
                    self.equations.native(z3.If(cell.present, current.identity, result.identity)))
            return result
        return self.equations.expression(expression, lookup, strict=strict,
                                         context_key=(state.version, context, active))

    def read(self, state, key, active=()):
        cache_key = (state.version, key, active)
        if cache_key in self.read_cache: return self.read_cache[cache_key]
        if key in active:
            return Term(Value.Absent, failure('cyclic source binding: ' + key))
        cell = self.cell(state, key)
        if z3.is_false(z3.simplify(cell.present)):
            return Term(Value.Absent)
        result = Term(cell.value, identity=cell.identity)
        if key in self.aliases and not z3.is_false(z3.simplify(cell.kind == 1)):
            alias = self.read(state, self.aliases[key], (*active, key))
            result = self.equations.bind(z3.If(cell.kind == 1, alias.value, result.value),
                z3.If(cell.kind == 1, alias.status, result.status),
                self.equations.native(z3.If(cell.kind == 1, alias.identity, result.identity)))
        if key in self.bindings and not z3.is_false(z3.simplify(cell.kind == 2)):
            expression, context = self.bindings[key]
            bound = self.evaluate(state, expression, context, strict=True, active=(*active, key))
            result = self.equations.bind(z3.If(cell.kind == 2, bound.value, result.value),
                z3.If(cell.kind == 2, bound.status, result.status),
                self.equations.native(z3.If(cell.kind == 2, bound.identity, result.identity)))
        result = self.equations.bind(z3.If(cell.present, result.value, Value.Absent),
            z3.If(cell.present, result.status, Status.Success), result.identity)
        self.read_cache[cache_key] = result
        return result

    def well_formed(self, state, identity_counter):
        cells = list(state.cells.values())
        order = [z3.Implies(cell.present, z3.And(cell.order >= 0, cell.order < state.next_order)) for cell in cells]
        order += [z3.Implies(z3.And(a.present, b.present), a.order != b.order)
                  for i, a in enumerate(cells) for b in cells[:i]]
        return z3.And(identity_counter >= 0, state.next_order >= 0, *order, *[
            z3.And(z3.Or(cell.kind == 0,
                         z3.And(key in self.aliases, cell.kind == 1),
                         z3.And(key in self.bindings, cell.kind == 2)),
                   z3.Implies(cell.present, cell.identity <= identity_counter))
            for key, cell in state.cells.items()])
