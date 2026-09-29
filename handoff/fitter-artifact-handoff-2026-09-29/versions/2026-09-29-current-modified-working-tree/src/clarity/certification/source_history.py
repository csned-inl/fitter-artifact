"""Finite observation/action histories over the shared source-node equations.

The recursive reachability relation carries a fixed-size buffer, not a list of
paths. Reset starts with zero observation padding and absent executed actions.
Both compared executions have the same decision count and immutable parameters.
An UNSAT result proves reconstruction. SAT still needs a feasible source replay.
"""
import hashlib
import json
import math
from itertools import count
from pathlib import Path

import z3

from .lazy_expressions import Value, Status, Term
from .lazy_solver import successors, variables
from .lazy_graph import fields
from .lazy_backend import bounded_query
from .lazy_substitution import PreparedSubstitution

_serial = count()


def normalization_contract(model_path, dt):
    from clarity.runtime import env
    path = Path(env.__file__).with_name('normalization.json')
    digest = hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
    rows = json.loads(path.read_text())['records']
    matches = [r for r in rows if r['source_sha256'] == digest and r['dt'] == dt]
    if len(matches) != 1:
        raise ValueError('source history needs an explicit, recorded observation scale')
    return {'scale': float(matches[0]['scale']), 'source_sha256': digest,
            'normalization_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'dtype': 'float32', 'rounding': 'nearest_ties_even'}


def encode_observation(term, scale):
    """SysMLEnv._state_to_obs: bool bypasses scale, others use float64 division.

    Float32 is embedded exactly in Float64 for a uniform buffer value sort.
    Unsupported values and failed conversions are obligations, never premises.
    """
    if not math.isfinite(scale) or scale < 1:
        raise ValueError('observation scale must be finite and at least one')
    value = term.value
    fp64, fp32 = z3.Float64(), z3.Float32()
    integer = z3.fpRealToFP(z3.RNE(), z3.ToReal(Value.integer(value)), fp64)
    numeric = z3.If(Value.is_Integer(value), integer, Value.floating(value))
    normalized = z3.If(Value.is_Boolean(value),
        z3.If(Value.boolean(value), z3.FPVal(1, fp64), z3.FPVal(0, fp64)),
        z3.fpDiv(z3.RNE(), numeric, z3.FPVal(scale, fp64)))
    narrowed = z3.fpToFP(z3.RNE(), normalized, fp32)
    valid = z3.And(term.status == Status.Success,
        z3.Or(Value.is_Boolean(value), Value.is_Float(value),
              z3.And(Value.is_Integer(value), z3.Not(z3.fpIsInf(integer)))))
    return z3.simplify(Value.Float(z3.fpToFP(z3.RNE(), narrowed, fp64))), z3.simplify(valid)


def push(buffer, values, depth):
    """Newest-first fixed-width shift, retaining exactly depth rows."""
    result = z3.K(z3.IntSort(), Value.Absent)
    width = len(values)
    for lag in range(depth):
        for column, value in enumerate(values):
            result = z3.Store(result, lag * width + column,
                value if lag == 0 else z3.Select(buffer, (lag - 1) * width + column))
    return result


def push_slots(buffer, values, depth):
    """The finite entries of push(), without an unbounded array sort."""
    width = len(values)
    if len(buffer) != depth * width:
        raise ValueError('buffer width does not match its fixed history depth')
    return tuple(values) + tuple(buffer[:(depth - 1) * width]) if depth else ()


class SourceHistoryQuery:
    def __init__(self, transition, b_obs, b_act, *, scale):
        if type(b_obs) is not int or type(b_act) is not int or min(b_obs, b_act) < 0:
            raise ValueError('buffer lengths must be nonnegative integers')
        if not transition.boundaries:
            raise ValueError('source graph has no controller decision')
        self.transition, self.b_obs, self.b_act, self.scale = transition, b_obs, b_act, scale
        graph = transition.graph
        names = []
        for row in graph['nodes'].values():
            if row['operation'] == 'decision':
                data = row['data']
                names.append(tuple(name for name in data['expected_inputs']
                                   if name != data['completion_input']))
            if row['operation'] == 'apply_executed_action':
                if any(kind != 'Boolean' for kind in row['data']['output_types'].values()):
                    raise ValueError('executed-action buffer requires discrete Boolean outputs')
        if not names or any(row != names[0] for row in names):
            raise ValueError('source decisions disagree on the observation interface')
        self.names = names[0]
        self.prefix = transition.prefix + '_history_' + str(next(_serial))

    def construct(self):
        query = self.transition
        from .constant_facts import constant_premises
        facts = query.checked_constants()
        self.fixedpoint = z3.Fixedpoint()
        # Keep eager inlining enabled: the omitted-state regression returns
        # false UNSAT with it disabled on the workstation Z3 build.
        self.fixedpoint.set(engine='spacer', **{'xform.inline_eager': True,
                                               'xform.inline_linear': False})
        from .source_layout import ConstantStateLayout
        layout = ConstantStateLayout(query.program, facts)
        self.layout = layout
        def pack(destination, row, state):
            return layout.pack(destination, row['compiler'], state)
        obs_width = (self.b_obs + 1) * len(self.names)
        act_width = self.b_act * len(query.actions)
        buffer_sorts = [Value] * (obs_width + act_width)
        action_sorts = [term.sort() for term in query.actions]
        trace, boundary = {}, {}
        self.bad = z3.Function(self.prefix + '_bad', z3.BoolSort())
        self.fixedpoint.register_relation(self.bad)
        def rule(head, *body):
            formula = z3.Implies(z3.And(*body), head) if body else head
            symbols = [v for v in variables(formula) if v.decl() != self.bad]
            self.fixedpoint.add_rule(z3.ForAll(symbols, formula) if symbols else formula)
        # One shared conversion relation per scalar, with native arguments in
        # each constructor rule. This avoids arbitrary datatype selectors inside
        # floating-point operators and does not enumerate combinations of inputs.
        encoding = z3.Function(self.prefix + '_encode', Value, Value, z3.BoolSort(), z3.BoolSort())
        encoding_registered = False
        for index, name in enumerate(query.rows):
            trace[name] = z3.Function(self.prefix + '_trace_' + str(index),
                *layout.sorts[name], *action_sorts, *buffer_sorts, z3.IntSort(), z3.BoolSort())
            self.fixedpoint.register_relation(trace[name])
        for index, name in enumerate(query.boundaries):
            boundary[name] = z3.Function(self.prefix + '_boundary_' + str(index),
                *layout.sorts[name], *buffer_sorts, z3.IntSort(), z3.BoolSort())
            self.fixedpoint.register_relation(boundary[name])
        obs = tuple(z3.Const(self.prefix + '_observation_' + str(i), Value)
                    for i in range(obs_width))
        act = tuple(z3.Const(self.prefix + '_action_' + str(i), Value)
                    for i in range(act_width))
        count = z3.Int(self.prefix + '_decision_count')
        zero = Value.Float(z3.FPVal(0, z3.Float64()))
        obs_padding = (zero,) * obs_width
        act_padding = (Value.Absent,) * act_width
        root = query.graph['initial_entry']
        rule(trace[root](*pack(root, query.rows[root], query.rows[root]['input']),
                         *query.actions, *obs_padding, *act_padding, 0))
        for name, row in query.rows.items():
            compiler, relation = row['compiler'], row['relation']
            before = pack(name, row, row['input'])
            reached = trace[name](*before, *query.actions, *obs, *act, count)
            equations = query.node_equations(name)
            rule(self.bad(), reached, *equations, z3.Not(z3.And(*query.obligations(name))))
            for target in successors(query.graph, name, query.returns):
                guard = relation.successor == compiler.ids[target]
                if relation.outcome in ('decision', 'terminal', 'error'):
                    guard = z3.And(guard, relation.status != Status.Success)
                rule(trace[target](*pack(target, row, relation.state), *query.actions, *obs, *act, count), reached, *equations, guard)
            if relation.outcome != 'decision':
                continue
            encoded, admissible, encoding_steps = [], [], []
            for key in self.names:
                term = dict(relation.decision_inputs)[key]
                fixed_kind = any(z3.is_true(z3.simplify(Value.recognizer(i)(term.value)))
                                 for i in range(Value.num_constructors()))
                if fixed_kind:
                    value, supported = encode_observation(term, self.scale)
                else:
                    if not encoding_registered:
                        self.fixedpoint.register_relation(encoding)
                        variants = [Value.Absent, Value.Boolean(z3.Bool(self.prefix + '_bool')),
                                    Value.Integer(z3.Int(self.prefix + '_int')),
                                    Value.Float(z3.FP(self.prefix + '_float', z3.Float64())),
                                    Value.Text(z3.Int(self.prefix + '_text'))]
                        for variant in variants:
                            mapped, valid = encode_observation(Term(variant), self.scale)
                            rule(encoding(variant, z3.simplify(z3.If(valid, mapped, Value.Absent)), valid))
                        encoding_registered = True
                    value = z3.Const(self.prefix + '_encoded_' + name + '_' + key, Value)
                    supported = z3.Bool(self.prefix + '_supported_' + name + '_' + key)
                    encoding_steps.append(encoding(term.value, value, supported))
                encoded.append(value)
                admissible.extend((supported, term.status == Status.Success))
            source_before = pack(name, row, row['input'])
            premises = [trace[name](*source_before, *query.actions, *obs, *act, count),
                        *equations, *encoding_steps,
                        relation.status == Status.Success]
            rule(self.bad(), *premises, z3.Not(z3.And(*admissible)))
            target = query.graph['nodes'][name]['successors']['resume']
            source_after = pack(target, row, relation.state)
            shifted = push_slots(obs, encoded, self.b_obs + 1)
            # Unsupported encodings do not silently remove executions: they
            # also reach bad above. Resumption retains exact source state.
            rule(boundary[target](*source_after, *shifted, *act, count),
                 *premises, *admissible)
            renamed = [(v, z3.Const(self.prefix + '_next_' + str(i), v.sort()))
                       for i, v in enumerate(variables(*query.actions))]
            next_actions = [z3.substitute(term, *renamed) for term in query.actions]
            rule(trace[target](*source_after, *next_actions, *shifted,
                               *push_slots(act, next_actions, self.b_act), count + 1),
                 *premises, *admissible)
        for left in query.boundaries:
            a = pack(left, query.rows[left], query.rows[left]['input'])
            aq = query.projections[left]
            for right in query.boundaries:
                row = query.rows[right]
                b = pack(right, row, row['input'])
                bq = query.projections[right]
                substitutions = [(v, z3.Const(self.prefix + '_right_' + str(v.get_id()), v.sort()))
                                 for v in variables(*b, *bq, *query.node_equations(right))]
                other = PreparedSubstitution(substitutions)
                q_size = 2 * len(query.q)
                # Equal n fixes which slots are padding, and is the existing
                # env.step_count augmentation of the certificate contract.
                rule(self.bad(), boundary[left](*a, *obs, *act, count),
                    boundary[right](*[other(v) for v in b], *obs, *act, count),
                    *query.node_equations(left),
                    *[other(e) for e in query.node_equations(right)],
                    *[x == other(y) for x, y in zip(aq[q_size:], bq[q_size:])],
                    z3.Or(*[x != other(y) for x, y in zip(aq[:q_size], bq[:q_size])]))
        return self

    def check(self, timeout_ms=30000, include_artifacts=False):
        self.construct()
        result = bounded_query(self.fixedpoint, self.bad(), timeout_ms)
        evidence = {'status': 'discharged' if result['status'] == 'unsat' else 'unknown',
                    'claim': 'source_history_reconstruction' if result['status'] == 'unsat' else 'not_claimed',
                    'z3_check_sat': result['status'], 'reason': result.get('reason'),
                    'b_obs': self.b_obs, 'b_act': self.b_act,
                    'observation_keys': list(self.names), 'observation_scale': self.scale,
                    'observation_dtype': 'float32', 'augmented_state': ['env.step_count'],
                    'scope': 'all finite source histories with fixed immutable parameters',
                    'reset_padding': 'zero observations and absent one-hot actions',
                    'buffer_layout': 'finite_slots_v1',
                    'state_layout': 'checked_constants_per_location_v1',
                    'omitted_constant_fields': self.layout.omitted,
                    'timeout_ms': timeout_ms}
        if 'error' in result:
            evidence['error'] = result['error']
        if result['status'] == 'sat':
            evidence['reason'] = 'source_history_counterexample_requires_replay'
        if include_artifacts:
            evidence['smt2'] = self.fixedpoint.to_string([self.bad()])
        return evidence
