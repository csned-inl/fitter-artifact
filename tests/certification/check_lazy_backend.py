"""Pre-integration solver capability checks for the compact source backend.

These check theory support, not the MDP theorem for the benchmark models.
Run on the workstation before replacing certificate generation/checking.
"""
import json
import time

import z3
from z3.z3util import get_vars

from clarity.certification.lazy_expressions import Equations, Term, Value, Status, scalar
from clarity.certification.lazy_specialization import propose_fields, check_specialization


def check_recursive_float():
    """A recursive sample/hold relation retains two separate float fields."""
    fp = z3.Float64()
    relation = z3.Function('sample_hold', z3.IntSort(), fp, fp, z3.BoolSort())
    bad = z3.Function('sample_hold_bad', z3.BoolSort())
    n = z3.Int('n')
    physical, sample = z3.FPs('physical sample', fp)
    solver = z3.Fixedpoint()
    solver.set(engine='spacer', timeout=1000)
    solver.register_relation(relation, bad)
    solver.rule(relation(0, z3.FPVal(0, fp), z3.FPVal(0, fp)))
    # Preserve the old sample when the physical state advances. This is a
    # theory-support fixture; n < 3 is its definition, not a benchmark bound.
    solver.rule(z3.ForAll([n, physical, sample], z3.Implies(
        z3.And(relation(n, physical, sample), n < 3),
        relation(n + 1, z3.fpAdd(z3.RNE(), physical, z3.FPVal(1, fp)), sample))))
    solver.rule(z3.ForAll([n, physical, sample], z3.Implies(
        z3.And(relation(n, physical, sample), sample != z3.FPVal(0, fp)), bad())))
    try:
        result = solver.query(bad())
        return {'status': str(result), 'reason': solver.reason_unknown() if result == z3.unknown else None}
    except z3.Z3Exception as exc:
        return {'status': 'unknown' if 'canceled' in str(exc) else 'unsupported',
                'reason': 'solver_timeout' if 'canceled' in str(exc) else str(exc)}


def check_named_float_equations():
    fp = z3.Float64()
    physical, sample, result = z3.FPs('physical sample result', fp)
    guard = z3.Bool('sample_enabled')
    solver = z3.Solver()
    solver.set(timeout=1000)
    solver.add(result == z3.If(guard, physical, sample))
    solver.add(z3.Or(z3.And(guard, result != physical),
                     z3.And(z3.Not(guard), result != sample)))
    return {'status': str(solver.check())}


def check_compiled_recursive_values():
    """Exercise the actual compiler's tagged arithmetic and error equations."""
    n=z3.Int('compiled_n'); value=z3.Const('compiled_value',Value)
    status=z3.Const('compiled_status',Status)
    equations=Equations('compiled_step')
    updated=equations.binary('+',Term(value,status),Term(scalar(1.0)))
    reached=z3.Function('compiled_reached',z3.IntSort(),Value,Status,z3.BoolSort())
    bad=z3.Function('compiled_bad',z3.BoolSort())
    solver=z3.Fixedpoint();solver.set(engine='spacer',timeout=1000)
    solver.register_relation(reached,bad)
    solver.rule(reached(0,scalar(0.0),Status.Success))
    variables=get_vars(z3.And(reached(n,value,status),*equations.equations))
    solver.rule(z3.ForAll(variables,z3.Implies(z3.And(reached(n,value,status),n<3,*equations.equations),
        reached(n+1,updated.value,updated.status))))
    solver.rule(z3.ForAll([n,value,status],z3.Implies(z3.And(reached(n,value,status),status!=Status.Success),bad())))
    try:
        result=solver.query(bad())
        return {'status':str(result),'reason':solver.reason_unknown() if result==z3.unknown else None}
    except z3.Z3Exception as exc:
        return {'status':'unknown' if 'canceled' in str(exc) else 'unsupported',
                'reason':'solver_timeout' if 'canceled' in str(exc) else str(exc)}


def check_compiled_typed_recursive_values():
    """Prove constructor preservation before using a native recursive field."""
    initial={'value':0.0}
    fields=propose_fields(initial,'typed')
    n=z3.Int('typed_n');value=fields['value'].payload
    equations=Equations('typed_step')
    updated=equations.binary('+',fields['value'].term,Term(scalar(1.0)))
    evidence=check_specialization(initial,fields,equations,{'value':updated},updated.status)
    if not evidence['accepted'] or evidence['queries']['execution_error']['status']!='unsat':
        return {'status':'unproved_specialization',
                'checks':{name:q['status'] for name,q in evidence['queries'].items()}}
    # Recheck the serialized obligations before consuming the native projection.
    for query in evidence['queries'].values():
        checker=z3.Solver();checker.set(timeout=1000);checker.from_string(query['smt2'])
        if checker.check()!=z3.unsat:
            return {'status':'specialization_recheck_failed'}
    result=evidence['native_outputs']['value']
    reached=z3.Function('typed_reached',z3.IntSort(),z3.Float64(),z3.BoolSort())
    bad=z3.Function('typed_bad',z3.BoolSort())
    solver=z3.Fixedpoint();solver.set(engine='spacer',timeout=1000)
    solver.register_relation(reached,bad)
    solver.rule(reached(0,z3.FPVal(0,z3.Float64())))
    solver.rule(z3.ForAll([n,value,result],z3.Implies(z3.And(reached(n,value),n<3,*equations.equations),
        reached(n+1,result))))
    solver.rule(z3.ForAll([n,value],z3.Implies(z3.And(reached(n,value),z3.fpLT(value,z3.FPVal(0,z3.Float64()))),bad())))
    try:
        result=solver.query(bad())
        return {'status':str(result),'reason':solver.reason_unknown() if result==z3.unknown else None}
    except z3.Z3Exception as exc:
        return {'status':'unknown' if 'canceled' in str(exc) else 'unsupported',
                'reason':'solver_timeout' if 'canceled' in str(exc) else str(exc)}


if __name__ == '__main__':
    started = time.monotonic()
    result = {'z3': z3.get_version_string(),
              'named_float_equations': check_named_float_equations(),
              'recursive_float': check_recursive_float(),
              'compiled_recursive_values': check_compiled_recursive_values(),
              'compiled_typed_recursive_values': check_compiled_typed_recursive_values(),
              'claim': 'backend capability only, not benchmark certification'}
    result['seconds'] = time.monotonic() - started
    print(json.dumps(result, indent=2), flush=True)
    raise SystemExit(0 if all(result[k]['status'] == 'unsat' for k in
                             ('named_float_equations', 'recursive_float', 'compiled_recursive_values',
                              'compiled_typed_recursive_values')) else 1)
