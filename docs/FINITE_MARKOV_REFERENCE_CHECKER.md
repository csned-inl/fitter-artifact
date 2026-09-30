# Finite Markov reference checker: Stage 1 verification note

## Claim

For a well-formed `FiniteDeterministicSystem`, if
`prove_finite_buffer_markov` returns `ProofStatus.PROVED`, then the exact set of
reachable augmented states `(source_state, decision_buffer)` satisfies the
deterministic buffer-quotient condition:

> Any two reachable augmented states with the same exact buffer have the same
> legal proposal set and, for every legal proposal, the same executed action,
> outcome constructor, visible output, and next buffer.

The method is a finite reference oracle for fixtures. It is not the production
algorithm for continuous/infinite source models.

The claim is conditional on the supplied Python callbacks denoting the stated
total deterministic mathematical functions.  The checker evaluates
availability, shielding, and transition callbacks twice and rejects an
immediate disagreement, but no finite test can establish purity of arbitrary
Python code.  Consequently, `PROVED` certifies the finite formal transition
system represented during the run; it does not by itself establish extraction
correctness or refinement to simulator/runtime behavior.  Those are separate
proof obligations in the production pipeline.

## Formal objects

Let:

- `S` be the finite declared source-state domain;
- `S0` be the nonempty reset-state set;
- `P` be the finite proposal domain;
- `E` be the finite executed-action domain;
- `B` be the finite set of buffers reachable from reset under the exact shift;
- `Z = S x B` be augmented states;
- `A(z)` be proposal availability;
- `execute(z,p)` be the deterministic executed action;
- `step(z,p,e)` be a total deterministic outcome;
- `next(z,p)` be the next augmented state for a continuing outcome.

Outcome equality includes the executed action, the disjoint outcome constructor,
the exact visible tuple, and the exact next buffer for continuing outcomes.

`exact_key` defines representation-sensitive equality. In particular it does
not merge Boolean `True` with integer `1`, or binary64 positive zero with
negative zero.

## Reachability algorithm

The checker initializes a queue with every reset state paired with its exact
padded buffer. It repeatedly removes one previously unseen augmented state,
enumerates every available proposal, evaluates `execute` and `step`, validates
the result, and adds every unseen continuing successor. It stops only when the
queue is empty.

The state and transition resource limits are checked before accepting new work.
Reaching a limit returns `INCOMPLETE`, never `PROVED`.

### Lemma 1: every recorded state is reachable

Proof by construction. Initial records are reset states and therefore reachable.
Every subsequently recorded state is the validated continuing successor of an
already recorded reachable state under an available proposal. Induction on the
insertion order proves the lemma.

### Lemma 2: every reachable augmented state is recorded

Assume the algorithm reaches its empty-queue fixed point without a resource or
validation failure. Proof by induction on path length from reset. All length-zero
states are inserted initially. Suppose every state reachable in at most `n`
transitions is recorded. Each is eventually removed from the finite queue, and
the algorithm evaluates every available proposal and inserts every continuing
successor. Therefore every state reachable in `n+1` transitions is recorded.
The union over finite path lengths is the reachable set.

Because `S`, proposal/action domains, and the buffer components are finite, the
number of augmented states is finite and the queue reaches a fixed point unless
an operational limit interrupts it. An interruption is not a proof result.

## Congruence algorithm

The checker partitions the complete reachable set by exact buffer key. For each
unordered pair in each block it:

1. compares exact legal-proposal sets;
2. for every common proposal, compares the cached exact transition records.

The pair loop includes self-pairs and every pair of distinct source states in a
buffer block.

### Lemma 3: every possible finite counterexample is checked

By Lemma 2, both endpoints of any reachable counterexample are recorded. Equal
buffers place them in the same partition block. Complete pair enumeration visits
their pair. If proposal availability differs, the first comparison reports it.
Otherwise the alleged distinguishing proposal is in both equal availability
sets and its two transition records are compared. Therefore any difference in
executed action, outcome kind, visible output, or next buffer is reported.

### Theorem: `PROVED` implies the deterministic finite-history Markov property

`PROVED` is returned only after the reachability queue reaches its complete fixed
point and all buffer-block pairs pass. Lemma 3 shows that no reachable
equal-buffer pair has different availability or a different complete outcome
under the same proposal. Hence the complete controller-facing outcome is a
function of the buffer and proposal. The exact shift makes the next quotient
state a function of the same arguments. This is the deterministic congruence
condition required for the finite buffer to be a Markov state.

## Fail-closed cases

The checker does not return `PROVED` if:

- reset/domain declarations are malformed;
- action availability leaves the declared proposal domain;
- shield execution leaves the declared executed-action domain;
- a decision has no proposal;
- a transition blocks, raises, or returns no `StepOutcome`;
- repeated evaluation reveals nondeterministic availability, shielding, or a
  transition outcome;
- a continuing state leaves the declared state domain;
- a supplied next observation disagrees with `observe(next_state)`;
- an augmented-state or transition resource limit is reached.

## Stage 1 fixture coverage

`tests/certification/validate_markov_reference.py` verifies:

- distinct physical, held, sent, and received storage identities;
- event-time copy semantics across later physical changes;
- exact padding and lag order;
- type-sensitive and IEEE-bit-sensitive equality;
- a directly observed Markov system;
- an insufficient delayed-state buffer with a reachable counterexample;
- sufficiency after adding the required executed-action history;
- hidden shield dependence;
- hidden action availability;
- hidden decision duration;
- terminal-versus-continuing distinction;
- blocked/non-total transitions;
- an impure transition detected by repeated evaluation;
- observation correspondence failure;
- resource truncation returning `INCOMPLETE` rather than `PROVED`.

## Reproducible validation

From the repository root, without installing the package:

```bash
PYTHONPATH=src python3 -m compileall -q src tests
PYTHONPATH=src python3 tests/certification/validate_markov_reference.py
```

The Stage 1 checkpoint contains 15 tests.  A successful run reports all 15 as
passing.  This test result validates the implementation against the listed
fixtures; the theorem above supplies the separate mathematical argument for
the exhaustive algorithm under its explicit assumptions.
