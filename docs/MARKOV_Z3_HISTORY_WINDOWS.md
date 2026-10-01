# Linear finite-history windows

`clarity.certification.markov_z3_window` constructs the finite predecessor
window needed to relate the current controller buffer to prior decision
states. Its scope is still one execution and one history case. It does not
construct paired Markov counterexamples or change certificate status.

## Construction

For history length `L = max(b_obs, b_act)`, the steady-state case creates
exactly `L` historical decision transitions. Reset-prefix case `t` creates
exactly `t` transitions. Each historical transition:

- uses one checked sparse transition relation;
- is constrained to reach the unique next-decision exit;
- must have a continuing controller outcome;
- bridges all 36 persistent typed state cells to the next boundary;
- freshly initializes the 11 per-decision proposal, executed-action,
  shield-input, and property-accumulator cells; and
- exposes its boundary observation and shield-selected executed action for
  final-buffer correspondence.

For `b_obs=2, b_act=1`:

| Case | Historical transitions | Total transition copies including current | State bridges | Buffer correspondences |
|---|---:|---:|---:|---:|
| `reset_prefix_0` | 0 | 1 | 0 | 0 |
| `reset_prefix_1` | 1 | 2 | 36 | 6 |
| `steady_state` | 2 | 3 | 72 | 8 |

The case formula is constructed once and is reusable for every later
visible-difference predicate. No transition is copied per predicate and no
control-flow path is enumerated.

## Direct lag projection

The thermostat proof profile has one successful shield-selected executed
action for each boundary state. The action applied to the simulator is thus
independent of the policy proposal, even though the proposal remains in the
declared domain. The simulator transition begins after that executed action is
installed and does not read the policy buffer.

Under these checked profile facts, explicitly materializing every intermediate
buffer would repeat deterministic bookkeeping without adding behavior. The
compiler instead replays repeated shift structurally:

```text
observation lag j = observation at historical state S[-j]
action lag j      = one-hot executed action E[-j]
```

This mapping is exact for the thermostat buffer convention. A profile where
the executed action can depend on the proposal or intermediate buffer must not
use this reduction without a separate proof.

## Conservative initial state

The current `MarkovIR` begins at a controller decision boundary and does not
yet encode the simulator's source-reset relation. The window therefore uses
the full type-correct state domain (`I = true`) for its oldest state.

This is a sound over-approximation: every actual reset or steady-state history
has a witness in the window, while additional nonreachable histories may also
be admitted. Consequently:

- `UNSAT` over this window can remain sound;
- `SAT` may be spurious and requires replay/refinement; and
- the window must not be described as exact reset reachability.

Reset-prefix zero padding and elapsed lag population are nevertheless exact.
An exact source-reset anchor remains a blocking refinement and certificate
obligation.

## Vacuity and fail-closed checks

Before later accepting any visible-difference `UNSAT`, every history-case base
formula must itself be `SAT`. This detects contradictory bridges, impossible
padding, or accidentally overconstrained continuation equations. Mutation
checks also reject noncanonical buffer encodings, missing cases, altered
normalization, declaration loss, incomplete state bridges, and namespace
aliasing.

The production `ObligationManifest` remains untouched: all query and
structural statuses are still `NOT_RUN`, the reachability premise is still
open, and `certificate_ready` remains false.
