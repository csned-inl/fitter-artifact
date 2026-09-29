"""Compose shared source-node equations without enumerating configurations.

An acyclic decision interval is a guarded SSA graph. Cyclic intervals remain
Horn relations. Capacity, successor coverage and local-storage obligations are
bad states to prove unreachable, never premises that exclude source behavior.
"""
from collections import deque
from itertools import count
import time

import z3

from .lazy_expressions import Value, Status, Term, text_id
from .lazy_graph import fields
from .lazy_congruence import abstract_numeric
from .lazy_backend import bounded_check, bounded_acyclic_check
from .lazy_substitution import PreparedSubstitution, reduce_with_equalities
from .lazy_control import machine_return_targets, structural_source_progress


_serial = count()
_value_array = z3.ArraySort(z3.IntSort(), Value)
_status_array = z3.ArraySort(z3.IntSort(), Status)
Output = z3.Datatype('ClarityLazyOutput')
Output.declare('Result', ('outcome', z3.IntSort()), ('values', _value_array),
               ('statuses', _status_array))
Output = Output.create()
Events = z3.Datatype('ClarityLazyEvents')
Events.declare('Empty')
Events.declare('Event', ('boundary', z3.IntSort()), ('values', _value_array),
               ('statuses', _status_array), ('tail', Events))
Events = Events.create()


def variables(*expressions):
    """Free constants, traversing a shared DAG only once."""
    result, pending, seen = [], list(expressions), set()
    while pending:
        term = pending.pop()
        if term.get_id() in seen:
            continue
        seen.add(term.get_id())
        if z3.is_const(term) and term.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            result.append(term)
        else:
            pending.extend(term.children())
    return result


def arrays(terms):
    values = z3.K(z3.IntSort(), Value.Absent)
    statuses = z3.K(z3.IntSort(), Status.Success)
    for index, term in enumerate(terms):
        values = z3.Store(values, index, z3.If(term.status == Status.Success, term.value, Value.Absent))
        statuses = z3.Store(statuses, index, term.status)
    return values, statuses


def successors(graph, name, returns=None):
    node = graph['nodes'][name]
    if node['operation'] == 'outcome':
        return ()
    if node['operation'] == 'decision':
        return (node['on_exception'],)
    if node['operation'] == 'return':
        targets = set((machine_return_targets(graph) if returns is None else returns)[name])
    else:
        targets = set(node['successors'].values())
        # A call saves this edge; it does not execute it until return.
        if node['operation'] == 'call_machine':
            targets.discard(node['successors']['return'])
    targets.add(node['on_exception'])
    return tuple(sorted(targets))


def interval_order(graph, entry, returns=None):
    """Topological order up to successful boundaries, or None for a cycle."""
    returns = machine_return_targets(graph) if returns is None else returns
    adjacent, pending = {}, [entry]
    while pending:
        name = pending.pop()
        if name in adjacent:
            continue
        adjacent[name] = successors(graph, name, returns)
        pending.extend(adjacent[name])
    indegree = dict.fromkeys(adjacent, 0)
    for targets in adjacent.values():
        for target in targets:
            indegree[target] += 1
    ready = deque(sorted(name for name, degree in indegree.items() if degree == 0))
    ordered = []
    while ready:
        name = ready.popleft()
        ordered.append(name)
        for target in adjacent[name]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    return ordered if len(ordered) == len(adjacent) else None


class LazyTransitionQuery:
    def __init__(self, model, q, program):
        self.model, self.q, self.program = model, sorted(q), program
        self.graph, self.rows = program['graph'], program['nodes']
        self.returns = machine_return_targets(self.graph)
        self.prefix = 'lazy_query_' + str(next(_serial))
        self.boundaries = sorted({node['successors']['resume'] for node in self.graph['nodes'].values()
                                  if node['operation'] == 'decision'})
        self.orders = {name: interval_order(self.graph, name, self.returns) for name in self.boundaries}
        from .lazy_invocations import invocation_order
        self.invocations = {name: invocation_order(self.graph, name) for name in self.boundaries}
        self.progress_evidence = structural_source_progress(self.graph)
        self.progress = bool(self.boundaries) and self.progress_evidence['discharged']
        # A source inventory can omit a dynamically copied field. Establish
        # immutability from the actual lowered frame equations instead of
        # treating an empty inventory of writers as a proof of constancy.
        root = self.graph['initial_entry']
        initialized = self.rows[root]['relation'].state.store
        self.constants = []
        for parameter in self.graph['nodes'][root]['data']['parameters']:
            key = parameter['qualified_name']
            cell = initialized.cells[key]
            if not z3.is_true(z3.simplify(z3.And(cell.present, cell.kind == 0))):
                continue
            if all(row['input'].store.cells[key] is row['relation'].state.store.cells[key]
                   for name, row in self.rows.items() if name != root):
                self.constants.append(key)
        self.actions = [term.value for _, term in sorted(program['actions'].items())]
        # Outputs may read live bindings; build them before freezing equations.
        self.output, self.projections = {}, {}
        for name, row in self.rows.items():
            compiler, relation = row['compiler'], row['relation']
            if name in self.boundaries:
                self.projections[name] = self.projection(compiler, row['input'])
            if relation.outcome in ('decision', 'terminal', 'error'):
                self.output[name] = self.output_value(compiler, relation)
        self.rules = 0

    def projection(self, compiler, state):
        terms = []
        for name in self.q:
            entries = self.model.value_semantics['decision_updates'].get(name, [])
            if len(entries) != 1:
                raise ValueError('no unique source storage for q variable ' + name)
            key = entries[0]['runtime_key']
            if key.startswith('$machine:'):
                label = state.machines.get(key[len('$machine:'):], z3.IntVal(0))
                terms.append(Term(z3.If(label == 0, Value.Absent, Value.Text(label))))
            else:
                terms.append(compiler.store.read(state.store, key))
        terms.extend(compiler.store.read(state.store, key) for key in self.constants)
        return tuple(value for term in terms for value in (term.value, term.status))

    def output_value(self, compiler, relation):
        if relation.outcome == 'error':
            values, statuses = arrays([Term(Value.Absent, relation.state.error)])
        else:
            terms = []
            for name in self.q:
                entries = self.model.value_semantics['decision_updates'].get(name, [])
                if len(entries) != 1:
                    raise ValueError('no unique source storage for q variable ' + name)
                key = entries[0]['runtime_key']
                if key.startswith('$machine:'):
                    label = relation.state.machines.get(key[9:], z3.IntVal(0))
                    terms.append(Term(z3.If(label == 0, Value.Absent, Value.Text(label))))
                else:
                    terms.append(compiler.store.read(relation.state.store, key))
            for name in compiler.input_names:
                if relation.outcome == 'decision':
                    # This projection is consumed only when status=Success.
                    # The source decision then installs these exact values.
                    term = dict(relation.decision_inputs).get(name, Term(Value.Absent))
                    present = z3.BoolVal(name in dict(relation.decision_inputs))
                else:
                    present, term = relation.state.inputs.get(name, (z3.BoolVal(False), Term(Value.Absent)))
                terms.extend((Term(Value.Boolean(present)), term))
            values, statuses = arrays(terms)
        return Output.Result(text_id(relation.outcome), values, statuses)

    def events(self, relation, tail):
        result = tail
        for boundary, enabled, rows in reversed(relation.events):
            values, statuses = arrays([term for _, term in sorted(rows)])
            result = z3.If(enabled, Events.Event(text_id(boundary), values, statuses, result), result)
        return result

    def edge_conditions(self, name):
        row = self.rows[name]
        relation, compiler = row['relation'], row['compiler']
        terminal = relation.outcome in ('decision', 'terminal', 'error')
        active = relation.status != Status.Success if terminal else z3.BoolVal(True)
        return [(target, z3.And(active, relation.successor == compiler.ids[target]))
                for target in successors(self.graph, name, self.returns)]

    def obligations(self, name):
        row = self.rows[name]
        relation = row['relation']
        covered = z3.Or(*[guard for _, guard in self.edge_conditions(name)])
        if relation.outcome in ('decision', 'terminal', 'error'):
            covered = z3.Or(covered, relation.status == Status.Success)
        return [*row['compiler'].representation_obligations, covered]

    def acyclic_execution(self, entry):
        """Guarded SSA joins: one copy of each source node, not each path."""
        plan = self.invocations[entry]
        if plan is None:
            raise ValueError('cyclic or unresolved call interval requires recursive relations')
        order, adjacent = plan
        root = (entry, ())
        active, incoming = {}, {key: [] for key in order}
        incoming[root].append(z3.BoolVal(True))
        equations, failures = [], []
        output = Output.Result(0, z3.K(z3.IntSort(), Value.Absent), z3.K(z3.IntSort(), Status.Success))
        events = Events.Empty
        shared = {v.get_id() for v in variables(*self.actions)}
        substitutions = {}
        for index, key in enumerate(order):
            name, _ = key
            row = self.rows[name]
            compiler, relation = row['compiler'], row['relation']
            expressions = [*compiler.eq.equations, *row['premises'],
                *[v for _, v in fields(compiler, row['input'])],
                *[v for _, v in fields(compiler, relation.state)],
                relation.successor, relation.status, *self.obligations(name),
                self.events(relation, Events.Empty)]
            if name in self.output:
                expressions.append(self.output[name])
            substitutions[key] = PreparedSubstitution([] if key == root else [
                (v, z3.Const(self.prefix + '_invoke_' + entry + '_' + str(index) + '_' + str(v.get_id()), v.sort()))
                for v in variables(*expressions) if v.get_id() not in shared])
        def at(key, expression):
            return substitutions[key](expression)
        for index, key in enumerate(order):
            name, _ = key
            row = self.rows[name]
            compiler, relation = row['compiler'], row['relation']
            enabled = z3.Bool(self.prefix + '_active_' + entry + '_' + str(index))
            active[key] = enabled
            equations.append(enabled == z3.Or(*incoming[key]))
            equations.extend(at(key, e) for e in compiler.eq.equations)
            equations.extend(z3.Implies(enabled, at(key, premise)) for premise in row['premises'])
            terminal = relation.outcome in ('decision', 'terminal', 'error')
            guards = []
            for original_target, target in adjacent[key]:
                guard = at(key, relation.successor == compiler.ids[original_target])
                if terminal:
                    guard = z3.And(guard, at(key, relation.status != Status.Success))
                guards.append(guard)
                edge = z3.And(enabled, guard)
                incoming[target].append(edge)
                target_row = self.rows[target[0]]
                equations.extend(z3.Implies(edge, at(target, a) == at(key, b)) for (_, a), (_, b) in zip(
                    fields(target_row['compiler'], target_row['input']), fields(compiler, relation.state)))
            covered = z3.Or(*guards, at(key, relation.status == Status.Success) if terminal else z3.BoolVal(False))
            # In particular, a mismatched saved return is an explicit failure.
            # It cannot disappear because only its source call site was expanded.
            failures.append(z3.And(enabled, z3.Not(z3.And(covered,
                *[at(key, obligation) for obligation in compiler.representation_obligations]))))
            if name in self.output:
                output = z3.If(z3.And(enabled, at(key, relation.status == Status.Success)), at(key, self.output[name]), output)
        for key in reversed(order):
            relation = self.rows[key[0]]['relation']
            if not relation.events:
                continue
            # Rename local event expressions, not the already assembled tail.
            emitted = at(key, self.events(relation, Events.Empty))
            if len(relation.events) > 1:
                raise ValueError('source node emits multiple boundary events')
            events = z3.If(z3.And(active[key], z3.Not(Events.is_Empty(emitted))),
                Events.Event(Events.boundary(emitted), Events.values(emitted), Events.statuses(emitted), events), events)
        return equations, output, events, z3.Or(*failures)

    def checked_constants(self):
        from .constant_facts import derive_constant_facts, check_constant_facts
        if not hasattr(self, '_constant_facts'):
            facts = derive_constant_facts(self.program)
            check_constant_facts(self.program, facts)
            self._constant_facts = facts
        return self._constant_facts

    def node_equations(self, name):
        """Fold local equations under independently checked incoming constants."""
        from .constant_facts import constant_premises
        facts = self.checked_constants()
        if not hasattr(self, '_node_equations'):
            self._node_equations = {}
        if name not in self._node_equations:
            row = self.rows[name]
            self._node_equations[name] = reduce_with_equalities(
                [*row['compiler'].eq.equations, *row['premises']],
                constant_premises(row, facts[name]))
        return self._node_equations[name]

    def acyclic_projection(self, timeout_ms, include_artifacts=False):
        if not self.progress or any(plan is None for plan in self.invocations.values()):
            return {'status': 'cyclic'}
        if not self.boundaries:
            return {'status': 'no_decision'}
        from .constant_facts import constant_premises
        facts = self.checked_constants()
        invariant_counts = {name: sum(value is not None for value in facts[name].values())
                            for name in self.boundaries}
        summaries = {name: self.acyclic_execution(name) for name in self.boundaries}
        checks, artifacts = 0, []
        shared = {v.get_id() for v in variables(*self.actions)}
        for left in self.boundaries:
            for right in self.boundaries:
                ae, ao, av, ab = summaries[left]
                be, bo, bv, bb = summaries[right]
                ae = [*ae, *constant_premises(self.rows[left], facts[left])]
                be = [*be, *constant_premises(self.rows[right], facts[right])]
                bq = self.projections[right]
                replacements = [(v, z3.Const(self.prefix + '_right_' + str(v.get_id()), v.sort()))
                                for v in variables(*be, bo, bv, bb, *bq) if v.get_id() not in shared]
                other = PreparedSubstitution(replacements)
                # Eliminate definitional SSA names and propagate equal boundary
                # values before invoking numeric theories. This is equivalence
                # preserving preprocessing; it does not split source paths.
                constraints = [z3.And(*ae), other(z3.And(*be)),
                           *[a == other(b) for a, b in zip(self.projections[left], bq)],
                           z3.Or(ab, other(bb), ao != other(bo), av != other(bv))]
                # Fold exact numeric operations after installing checked source
                # constants, before numeric operations become uninterpreted.
                # Retain the equalities: this is equivalence, not an assumption
                # that a delayed field equals its physical source.
                constants = [*constant_premises(self.rows[left], facts[left]),
                             *[other(e) for e in constant_premises(self.rows[right], facts[right])]]
                constraints = reduce_with_equalities(constraints, [*constants,
                    *[a == other(b) for a, b in zip(self.projections[left], bq)]])
                checked = bounded_acyclic_check(constraints, timeout_ms,
                    prefix=self.prefix + '_congruence', include_artifacts=include_artifacts)
                result = checked['status']
                abstract_status = checked.get('abstract_status', 'unknown')
                refined = checked.get('exact_refinement_attempted', False)
                checks += checked.get('queries', 0)
                if include_artifacts and 'smt2' in checked:
                    artifacts.append(checked['smt2'])
                if result != 'unsat':
                    return {'status': result, 'queries': checks,
                            'checked_constant_fields': invariant_counts,
                            'abstract_status': abstract_status,
                            'stage': checked.get('stage'),
                            'exact_refinement_attempted': refined,
                            'reason': checked['reason'] if result == 'unknown' else
                                      ('unrestricted_source_state_counterexample' if refined else
                                       'numeric_abstraction_requires_exact_query')}
        return {'status': 'unsat', 'queries': checks,
                'checked_constant_fields': invariant_counts,
                'method': 'ssa_preprocessing_then_numeric_congruence_with_exact_refinement',
                **({'artifacts': artifacts} if include_artifacts else {})}

    def rule(self, head, *body, quantified=None):
        formula = z3.Implies(z3.And(*body), head) if body else head
        if quantified is None:
            quantified = [v for v in variables(formula) if v.decl() != self.bad]
        self.fixedpoint.add_rule(z3.ForAll(quantified, formula) if quantified else formula)
        self.rules += 1

    def construct(self, *, flatten_state=True, sparse=True):
        if sparse and flatten_state:
            from .source_relation import construct_sparse
            return construct_sparse(self)
        if hasattr(self, 'fixedpoint'):
            if self.flatten_state != flatten_state or getattr(self, 'relation_layout', None) == 'checked_constants_per_location_v1':
                raise ValueError('relation layout is already fixed for this query')
            return self
        from .constant_facts import constant_premises
        facts = self.checked_constants()
        self.flatten_state = flatten_state
        first = next(iter(self.rows.values()))
        signature = fields(first['compiler'], first['input'])
        if flatten_state:
            state_sorts = [value.sort() for _, value in signature]
            def pack(row, state):
                return tuple(value for _, value in fields(row['compiler'], state))
        else:
            datatype = z3.Datatype(self.prefix + '_State')
            datatype.declare('State', *[('field_' + str(i), value.sort()) for i, (_, value) in enumerate(signature)])
            datatype = datatype.create()
            state_sorts = [datatype]
            def pack(row, state):
                return (datatype.State(*[value for _, value in fields(row['compiler'], state)]),)
        def fresh_state(label):
            return tuple(z3.Const(self.prefix + label + '_' + str(i), sort)
                         for i, sort in enumerate(state_sorts))
        self.fixedpoint = z3.Fixedpoint()
        # Keep eager inlining enabled: the omitted-state regression returns
        # false UNSAT with it disabled on the workstation Z3 build.
        self.fixedpoint.set(engine='spacer', **{'xform.inline_eager': True, 'xform.inline_linear': False})
        action_sorts = [value.sort() for value in self.actions]
        reachable, transition, steps = {}, {}, {}
        for i, name in enumerate(self.rows):
            reachable[name] = z3.Function(self.prefix + '_reach_' + str(i), *state_sorts, *action_sorts, z3.BoolSort())
            transition[name] = z3.Function(self.prefix + '_transition_' + str(i), *state_sorts, *action_sorts,
                                           Output, Events, z3.BoolSort())
            self.fixedpoint.register_relation(reachable[name], transition[name])
            steps[name] = z3.Function(self.prefix + '_step_' + str(i), *state_sorts, *action_sorts,
                                      *state_sorts, z3.IntSort(), Status, Events, z3.BoolSort(), z3.BoolSort())
            self.fixedpoint.register_relation(steps[name])
        # Expose the exact local relations to other proof obligations. History
        # reconstruction must compose these operations, not legacy equations.
        self.state_sorts, self.pack_state = state_sorts, pack
        self.step_relations, self.reachable_relations = steps, reachable
        self.bad = z3.Function(self.prefix + '_bad', z3.BoolSort())
        self.fixedpoint.register_relation(self.bad)
        root = self.graph['initial_entry']
        self.rule(reachable[root](*pack(self.rows[root], self.rows[root]['input']), *self.actions))
        output = z3.Const(self.prefix + '_out', Output)
        tail = z3.Const(self.prefix + '_events', Events)
        for name, row in self.rows.items():
            compiler, relation = row['compiler'], row['relation']
            before, after = pack(row, row['input']), pack(row, relation.state)
            equations = self.node_equations(name)
            local_events = self.events(relation, Events.Empty)
            valid = z3.And(*self.obligations(name))
            self.rule(steps[name](*before, *self.actions, *after, relation.successor,
                                 relation.status, local_events, valid), *equations)
            # Give composition fresh result variables. Reusing numerical RHS
            # expressions here would reintroduce the body into every edge.
            after_var = fresh_state('_after_' + name)
            before_var = fresh_state('_before_' + name)
            next_var = z3.Int(self.prefix + '_next_' + name)
            status_var = z3.Const(self.prefix + '_status_' + name, Status)
            events_var = z3.Const(self.prefix + '_emitted_' + name, Events)
            valid_var = z3.Bool(self.prefix + '_valid_' + name)
            # Flattening the record exposes independent state fields to the
            # solver's relation slicing. No field or source equation is dropped
            # by this compiler. The reference record layout remains testable.
            step = steps[name](*before_var, *self.actions, *after_var, next_var, status_var, events_var, valid_var)
            reach = reachable[name](*before_var, *self.actions)
            self.rule(self.bad(), reach, step, z3.Not(valid_var))
            # Every current source operation emits at most one boundary event.
            # This is checked from the compiler result, not assumed for graphs.
            if len(relation.events) > 1:
                raise ValueError('source node emits multiple boundary events')
            emitted = z3.If(Events.is_Empty(events_var), tail,
                Events.Event(Events.boundary(events_var), Events.values(events_var),
                             Events.statuses(events_var), tail))
            for target in successors(self.graph, name, self.returns):
                guard = next_var == compiler.ids[target]
                if relation.outcome in ('decision', 'terminal', 'error'):
                    guard = z3.And(guard, status_var != Status.Success)
                self.rule(reachable[target](*after_var, *self.actions), reach, step, guard)
                self.rule(transition[name](*before_var, *self.actions, output, emitted),
                          step, guard, transition[target](*after_var, *self.actions, output, tail))
            if name in self.output:
                # Output projection is a small separate relation. These terminal
                # bodies occur once, and retain exact live-binding evaluation.
                self.rule(transition[name](*before, *self.actions, self.output[name], self.events(relation, Events.Empty)),
                          *equations, relation.status == Status.Success)
            if relation.outcome == 'decision':
                # Action constructors remain constrained to the source types.
                native = variables(*self.actions)
                replacement = [(v, z3.Const(self.prefix + '_fresh_' + str(i), v.sort()))
                               for i, v in enumerate(native)]
                next_actions = [z3.substitute(v, *replacement) for v in self.actions]
                target = self.graph['nodes'][name]['successors']['resume']
                self.rule(reachable[target](*after_var, *next_actions), reach, step,
                          status_var == Status.Success)
        left_output, right_output = z3.Consts(self.prefix + '_ol ' + self.prefix + '_or', Output)
        left_events, right_events = z3.Consts(self.prefix + '_el ' + self.prefix + '_er', Events)
        for left in self.boundaries:
            a = pack(self.rows[left], self.rows[left]['input'])
            for right in self.boundaries:
                b = pack(self.rows[right], self.rows[right]['input'])
                shared = {v.get_id() for v in variables(*self.actions)}
                replacements = [(v, z3.Const(self.prefix + '_right_' + str(v.get_id()), v.sort()))
                                for v in variables(*b, *self.projections[right],
                                    *self.rows[right]['compiler'].eq.equations) if v.get_id() not in shared]
                other = PreparedSubstitution(replacements)
                # Live-binding projection has its own named equations.
                self.rule(self.bad(), reachable[left](*a, *self.actions), reachable[right](*[other(v) for v in b], *self.actions),
                    transition[left](*a, *self.actions, left_output, left_events),
                    transition[right](*[other(v) for v in b], *self.actions, right_output, right_events),
                    *self.rows[left]['compiler'].eq.equations,
                    *[other(e) for e in self.rows[right]['compiler'].eq.equations],
                    *[x == other(y) for x, y in zip(self.projections[left], self.projections[right])],
                    z3.Or(left_output != right_output, left_events != right_events))
        return self
