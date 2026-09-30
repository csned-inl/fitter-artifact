# Thermostat MarkovIR extraction and finite-interval checkpoint

Status: Phase 2 gate passed; Phase 3 finite interval and structural relevance
gate passed. This is not a finite-history Markov certificate and does not claim
equivalence to arbitrary Python runtime behavior.

## Source-bound artifacts

The thermostat controller-step contract is lowered into an immutable, typed,
unsliced `MarkovIR`. The extraction records:

- 80 operational graph nodes and 31 source events, with exact coverage;
- 136 storage identities: 17 semantic identities plus all 119 graph storage or
  lookup identities retained until reduction;
- explicit event-entry reads, event-exit writes, frame rules, successors,
  exception exits, source witnesses, machine modes, and visible outputs;
- 80 replayable local source-rule obligations;
- distinct physical, sensor-held, sent-payload, and received-payload identities;
- the executed-action response convention and first-outcome boundary.

The new extractor augments the legacy decision graph with field-level reads for
the three SysML flows. This is necessary because the legacy graph listed flow
destinations as writes but did not list the corresponding source fields as
reads. In particular, the environment-temperature flow is resolved through its
stored port alias back to `system::environment::temperatureCelcius`. The
independent validator reconstructs those reads from the parsed flow declarations
instead of trusting the extractor.

## Finite first-outcome interval

Starting at `system::controller/step/3/resume`, the interval derivation expands
explicit `(node, return-stack)` configurations. Calls push their statically
declared continuation; returns must pop a matching continuation. The expansion
stops at the first of:

- `system::controller/step/3` (the next decision request);
- `terminal`;
- `execution_error`.

The derived relation has:

| Metric | Value |
| --- | ---: |
| Control configurations | 132 |
| Control edges, including exception exits | 281 |
| Distinct source/runtime nodes | 78 |
| Maximum node visits to a first outcome | 102 |
| Maximum return-stack depth | 1 |

The pre-outcome graph is acyclic, has no unmatched return or dead end, and every
first-outcome state is a sink. This is a structural progress result for the
extracted formal control relation. It is not yet the SMT totality proof over all
data/branch constraints required by the final theorem.

## Relevance slice

Backward closure starts from the full controller-visible signature, next raw
observation/completion inputs, executed action, shield inputs, time, ordered
property statuses/errors, and all control choices that can alter the first
outcome. It closes over event data, writes, branch and machine control, flow
copies, frame-visible state, and source/runtime exception exits.

Current structural metrics are:

| Metric | Value |
| --- | ---: |
| Unsliced storage identities | 136 |
| Retained native storage identities | 41 |
| Interval source/runtime nodes | 78 |
| Retained relevant events | 51 |
| Split theorem-visible terms | 17 |
| Replayable reduction records | 134 |
| Generic record/queue/stack sorts retained | 0 |

The 41 retained locations use only Boolean, integer, binary64, binary32, enum,
or presence sorts. Generic local dictionaries, mailbox queues, call stacks,
machine frames, aggregate state containers, and the requirement ledger do not
enter the theorem state. Their relevant effects are projected to typed scalar,
mode, presence, property-status, or control-configuration terms. Every omitted
IR storage and every omitted interval event has one reduction record, and the
validator independently replays the fixed point and reduction coverage.

## Current assurance boundary

Passing these gates establishes source identity, typed coverage, local rule
shape, a finite first-outcome control bound, and a container-free structural
slice. It does not yet establish:

- full composed source-to-IR forward simulation;
- data-level feasibility, determinism, or totality for every branch;
- exact Float64/Float32 operator lowering;
- shield equations, reward equations, or buffer-history consistency;
- reachability inclusion or an inductive invariant;
- any Z3 `UNSAT` counterexample result;
- runtime refinement.

Those are later proof gates. Any failure or unknown result there remains a
non-certificate.

## Phase 4 prerequisite manifest

Before constructing SMT expressions, `markov_obligations.py` fixes the complete
query surface for each candidate `(b_obs, b_act)`. It generates every exact
reset-prefix case and the steady-state history-window case. Each case contains:

- one disagreement obligation for every theorem-visible term;
- separate no-outcome, multiple-first-outcome, and unsupported-representation
  obligations;
- an explicit structural replay obligation for the exact buffer shift.

Float32 and Float64 visible terms require IEEE bit equality. Outcome equality is
constructor-sensitive. Boolean, integer, enum, and action-mask terms use native
typed equality. Query acceptance requires `UNSAT`; `SAT`, `UNKNOWN`, timeout,
errors, missing query hashes, or missing solver identities can never make a
manifest certificate-ready.

For the illustrative candidate `(b_obs=2, b_act=1)`, the manifest has three
history cases, 57 required solver queries, and three buffer-shift replay
obligations. `markov_metrics.py` reports pre-SMT state, event, control-selector,
sort, and query counts and applies explicit fail-closed budgets.

The manifest remains deliberately non-ready: exact numeric operator lowering,
shield/reward/outcome equations, reachability inclusion, buffer-history replay,
and every solver query are still unrun. Z3 is pinned by the repository but is
not installed in the current execution environment.
