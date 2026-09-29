"""Select histories using the actual source-transition reconstruction proof.

The lexicographic order and configured bounds are unchanged. An inconclusive
query is recorded as inconclusive, never as a proof of insufficiency. Compiled
source equations are shared by the backend, but solver verdicts are not cached.
"""
from .source_solver import check_source_history_reconstruction


def select_source_buffer(model, q, *, dt, max_obs, max_act, b_obs=None, b_act=None,
                         timeout_ms=30000, include_artifacts=False):
    for name, value in (('max_obs', max_obs), ('max_act', max_act)):
        if type(value) is not int or value < 0:
            raise ValueError(name + ' must be a nonnegative integer')
    if (b_obs is None) != (b_act is None):
        raise ValueError('both explicit buffer lengths are required')
    if b_obs is not None:
        if any(type(v) is not int or v < 0 for v in (b_obs, b_act)):
            raise ValueError('buffer lengths must be nonnegative integers')
        candidates = [(b_obs, b_act)]
    else:
        candidates = ((o, a) for o in range(max_obs + 1) for a in range(max_act + 1))
    attempts = []
    for obs, act in candidates:
        evidence = check_source_history_reconstruction(model, q, dt=dt,
            b_obs=obs, b_act=act, timeout_ms=timeout_ms, include_artifacts=include_artifacts)
        accepted = (evidence.get('status') == 'discharged' and
                    evidence.get('claim') == 'source_history_reconstruction' and
                    evidence.get('b_obs') == obs and evidence.get('b_act') == act)
        attempts.append({'b_obs': obs, 'b_act': act, 'passes': accepted,
                         'status': evidence.get('status'), 'reason': evidence.get('reason')})
        if accepted:
            return {'selected': (obs, act), 'proof': evidence, 'attempts': attempts,
                    'minimality_claim': 'first proved pair in configured lexicographic order; unknown is not insufficiency'}
    return {'selected': None, 'proof': None, 'attempts': attempts,
            'minimality_claim': 'no buffer proved within configured candidates'}
