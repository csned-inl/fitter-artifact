THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

**AUTHORITATIVE MODEL SOURCE RULE:** SysML model semantics come only from `csned-inl/clarity-standalone`. `fitter-artifact`, generated certificates, backups, exported SMV, simulator traces, and all prior verification work are non-authoritative and must never be treated as model ground truth. Any disagreement stops production and requires user review.

# Analytical buffer-candidate stage

This is the first pipeline stage before the paired Z3 Markov prover. It proposes
history depths; it never certifies them.

## Method

For the supported direct-equation profile, work backward from every selected
next observation, outcome, reward, and property status. Every relevant state
component must have one checked reconstruction explanation:

- direct current observation;
- fixed process context;
- a specific prior-observation lag;
- a specific prior-executed-action lag;
- overwritten before any selected use; or
- proved outside the selected result and successor cone.

The candidate depths are the maximum required observation and action lags. An
unresolved component produces no candidate. The analysis does not use solver
timeouts, simulator execution, historical certificates, or an assumption that
the proposed buffer is Markov.

## Soundness boundary

The evidence list is a candidate-generation argument, not a theorem. A bad or
incomplete candidate may reduce completeness or be rejected, but it cannot
produce a certificate. The independent paired Z3 query must still prove source
initialization, invariant containment, invariant closure, and absence of a
same-buffer/same-action result disagreement.

## Current source-derived candidates

| Model | Candidate | Reason |
| --- | --- | --- |
| Thermostat | `(b_obs=0, b_act=1)` | Current observation gives setpoint and sensed/physical temperature; the prior executed action reconstructs actuator flags used by pre-action completion |
| Mixing Machine | `(b_obs=0, b_act=0)` | At each Policy boundary, sampled level plus fixed tolerance reconstructs physical level; incoming actuator state is overwritten before selected results and properties |

`b_obs` counts prior observations; the current observation is always present.
The earlier `(2,1)` queries remain regression proofs and are not claimed to be
minimal.

## Supported growth

Future source profiles may add exact rules for affine inversion, finite sample
delay, phase, queues, or timestamp reconstruction. Any rule must name its
source equation and required lag. Arbitrary nonlinear observability or an
unresolved schedule returns `NO_CANDIDATE`; it does not trigger enumeration or
silently add state.
