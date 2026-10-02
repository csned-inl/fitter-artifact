THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

**AUTHORITATIVE MODEL SOURCE RULE:** SysML model semantics come only from `csned-inl/clarity-standalone`. `fitter-artifact`, generated certificates, backups, exported SMV, simulator traces, and all prior verification work are non-authoritative and must never be treated as model ground truth. Any disagreement stops production and requires user review.

# Sparse finite-buffer Z3 view

`clarity.certification.markov_z3_history` implements one narrow component of
the buffered-process Markov proof: the exact policy-buffer layout, reset
padding, and deterministic one-step shift. It is not a generic verifier, does
not unroll the simulator, and does not issue the paired counterexample query.

## Exact representation

For configured lengths `b_obs` and `b_act`, the flattened Float32 policy input
is represented in runtime order:

1. current `setPoint` and `temperatureCelcius` observation;
2. `b_obs` prior two-field observations, newest first; and
3. `b_act` prior four-field one-hot **executed** actions, newest first.

The scalar width is therefore

```text
2 * (b_obs + 1) + 4 * b_act.
```

For the checked `b_obs=2, b_act=1` configuration this is 10 Float32 scalar
positions. The current observation is the exact runtime conversion: binary64
division by the recorded normalization scale followed by RNE conversion to
binary32. Padding is the exact positive Float32 zero bit pattern.

On a continuing step, the next current observation uses the two already
shared interface results. The prior-observation section shifts the current
observation into lag 1, and the prior-action section shifts a four-element
one-hot encoding of the shielded `ExecutedAction` into lag 1. Older entries
move exactly one position and the oldest entries are dropped.

There is no next decision buffer on terminal or error outcomes. The shared
`next-buffer-available` predicate retains all three source availability
conditions: executed action and both next-observation fields. The compiler
does not simplify these conditions away based on an unproved implication.

## Reset-prefix cases

With `L = max(b_obs, b_act)`, the component emits `reset_prefix_0` through
`reset_prefix_(L-1)` plus `steady_state`. At reset-prefix decision count `t`,
every observation or action lag greater than `t` is constrained to exact
positive Float32 zero. For `b_obs=2, b_act=1`:

| Case | Exact padding constraints |
|---|---:|
| `reset_prefix_0` | 8 scalar history positions |
| `reset_prefix_1` | 2 scalar positions at observation lag 2 |
| `steady_state` | 0 |

These are only the padding constraints for the cases. The separate linear
finite-history-window component now connects populated entries to historical
observations and executed actions. Calling either component a complete
reset-reachability proof would still be unsound because an exact source-reset
anchor has not yet been encoded.

## Anti-redundancy boundary

The buffer component references the existing sparse controller-visible
interface. It does not clone any of the 635 transition declarations or any
transition assertion. In the checked configuration it adds:

- 8 independent current-history scalars;
- 2 shared definitions for the normalized current observation;
- 10 shared definitions for next-buffer positions; and
- 1 shared next-buffer availability definition.

Growth is linear in buffer width. The shift is a source-to-target expression
map, not a collection of independently solved transition copies.

## Soundness guardrails

The compiler fails closed if the checked contract or interface is changed in
ways that could weaken the theorem, including:

- recording the policy proposal instead of the executed action;
- changing observation/action order or one-hot width;
- changing exact Float32 reset padding;
- changing next-observation or executed-action availability;
- losing the current/next width correspondence; or
- aliasing a history declaration with transition state.

The production `ObligationManifest` is deliberately unchanged. Its structural
shift obligations remain `NOT_RUN`, and `certificate_ready` remains false.
The linear predecessor-window component now supplies exact continuing
transitions, full typed-state bridges, and final-buffer lag correspondence. It
starts from the full type-correct state domain, so the implementation still
needs an exact reset anchor, paired same-buffer runs, reachability/invariant
inclusion, progress checks, and exhaustive visible-difference queries before
those statuses can change.
