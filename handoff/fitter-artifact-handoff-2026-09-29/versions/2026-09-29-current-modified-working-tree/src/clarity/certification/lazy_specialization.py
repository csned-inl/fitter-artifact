"""Checked native-field specialization for compact scalar transitions.

The initial values propose an invariant; they do not establish it. A separate
SMT check must prove that every successful transition preserves every proposed
constructor before a native-field recursive relation can use the projection.
This module does not certify source coverage, progress or the MDP theorem.
"""
from dataclasses import dataclass

import z3

from .lazy_expressions import Value, Status, Term, scalar


_KINDS = {
    'Boolean': (Value.Boolean, Value.is_Boolean, Value.boolean, z3.BoolSort()),
    'Integer': (Value.Integer, Value.is_Integer, Value.integer, z3.IntSort()),
    'Float': (Value.Float, Value.is_Float, Value.floating, z3.Float64()),
    'Text': (Value.Text, Value.is_Text, Value.text, z3.IntSort()),
}


def literal_kind(value):
    if value is None:
        return 'Absent'
    return {bool: 'Boolean', int: 'Integer', float: 'Float', str: 'Text'}[type(value)]


@dataclass(frozen=True)
class NativeField:
    kind: str
    payload: object

    @property
    def term(self):
        return Term(Value.Absent if self.kind == 'Absent'
                    else _KINDS[self.kind][0](self.payload))


def propose_fields(initial, prefix):
    """Propose kinds from actual runtime literals, never SysML declarations."""
    return {name: NativeField(kind, None if kind == 'Absent' else
                             z3.Const(f'{prefix}_{i}', _KINDS[kind][3]))
            for i, (name, value) in enumerate(sorted(initial.items()))
            for kind in (literal_kind(value),)}


def _is_kind(kind, value):
    return Value.is_Absent(value) if kind == 'Absent' else _KINDS[kind][1](value)


def check_specialization(initial, fields, equations, outputs, status,
                         *, guard=None, timeout_ms=1000):
    """Check initiation, preservation on success, and the reverse projection.

    All input payloads are universally quantified by checking for a violating
    assignment. Exceptional transitions remain explicit through ``status``;
    no absence of errors is inferred from success-conditional preservation.
    The caller must supply a source-complete transition before this evidence
    can support a recursive source relation.
    """
    if set(initial) != set(fields) or set(outputs) != set(fields):
        raise ValueError('specialization requires the complete same field set')
    if any(type(value) is not Term for value in outputs.values()):
        raise TypeError('each output must retain value and status')
    guard = z3.BoolVal(True) if guard is None else guard
    conditions = []
    for name, field in fields.items():
        value = outputs[name].value
        conditions.append(_is_kind(field.kind, value))
        if field.kind == 'Absent':
            conditions.append(value == Value.Absent)
        else:
            construct, _, project, _ = _KINDS[field.kind]
            # Constructor equality preserves FP signed zero and NaN values.
            conditions.append(construct(project(value)) == value)
    initiation = z3.And(*[_is_kind(fields[n].kind, scalar(initial[n]))
                          for n in fields])
    success = z3.And(status == Status.Success,
                     *[t.status == Status.Success for t in outputs.values()])
    queries = {
        'initiation': [z3.Not(initiation)],
        'successful_preservation': [*equations.equations, guard, success,
                                    z3.Not(z3.And(*conditions))],
    }
    evidence = {}
    for name, constraints in queries.items():
        solver = z3.Solver()
        solver.set(timeout=timeout_ms)
        solver.add(*constraints)
        result = solver.check()
        evidence[name] = {'status': str(result), 'smt2': solver.to_smt2()}
        if result == z3.unknown:
            evidence[name]['reason'] = solver.reason_unknown()
    accepted = all(q['status'] == 'unsat' for q in evidence.values())
    # A separate result: successful-preservation does NOT prove total success.
    solver = z3.Solver()
    solver.set(timeout=timeout_ms)
    solver.add(*equations.equations, guard, z3.Not(success))
    result = solver.check()
    evidence['execution_error'] = {'status': str(result), 'smt2': solver.to_smt2()}
    if result == z3.unknown:
        evidence['execution_error']['reason'] = solver.reason_unknown()
    native_outputs = ({name: (None if field.kind == 'Absent' else
                             z3.simplify(_KINDS[field.kind][2](outputs[name].value)))
                       for name, field in fields.items()} if accepted else None)
    return {'accepted': accepted, 'kinds': {n: f.kind for n, f in fields.items()},
            'native_outputs': native_outputs,
            'queries': evidence,
            'claim': 'constructor invariant and reversible projection on successful transitions'}
