# Compact proof compilation with distinct physical and delayed values

Revised after code review — 2026-09-19. This document proposes implementation
changes; it does not record completed implementation or passing certification.

The review tightened five decisions: establish solver feasibility before broad
integration; derive finite storage bounds instead of introducing an unbounded
symbolic interpreter; distinguish exact execution equations from invariant
overapproximations; check progress and history reconstruction explicitly; and
verify property transformations independently of the compiler.

## 1. Objective and scope

Retain the original SysML properties and their current, corrected parsing while
proving them through a compact representation of execution. Physical values,
held readings, sent payloads and received readings retain their separate
meanings. Conditions remain formulas instead of becoming an enumerated set of
execution paths.

The implementation changes the proof compiler and its certificate integration.
It preserves the current SysML files, parser, initialization behavior, simulator,
shield, requirement-check boundaries, scenario distribution, policy interfaces
and training settings. In particular, it does not change `=`/`:=` handling or
add requirement checks between source operations.

The existing lazy discretization checker remains. The substantial replacement
is the numerical MDP compiler that currently expands paths before that checker
can run.

## 2. Current implementation and the replacement boundary

`decision_transition.py` already extracts an ordered source graph, including
assignments, message operations, machine dispatch, constraint propagation,
decisions and completed-cycle checks. `compact_transition.py` already groups
that graph into blocks and shares expression syntax. Those structures and
their source-coverage checker should be reused.

The expansion occurs in `transition_equations.py`. It instantiates a runtime
configuration with fresh symbolic scalar values, calls `guarded_evaluations`,
and stores each resulting branch and configuration. Compilation continues over
new configuration layouts. `source_solver.py` then constructs its proof from
that expanded collection. Conditional formulas are compacted only after many
paths have already been generated. Disabling feasibility checks also allows
impossible combinations to occupy that collection.

Replace this collection with a graph of typed equations attached directly to
source locations. A branch is an edge condition; a varying value is an argument
of a relation. Neither creates another copy of the entire remaining program.
`execution_equations.py` remains a concrete reference for differential tests,
not the mechanism that discovers symbolic paths.

The current discretization reduction already has an empty sampled-to-physical
substitution and retains `sampled_state`. This work must preserve and verify
that correction, not restore the archived `physical_aliases` rule.

## 3. The mathematical contract

Let `s` be the full execution state at a controller decision. It includes
physical and stored sensor values, pending messages, machine control state,
continuations and counters. Let `a` be the executed action after shielding.
Let `T(s,a,s',o)` describe execution from this decision to the first next
decision, terminal result or execution error. The output `o` includes the
original requirement results produced along that execution.

Let `enc` encode a well-formed runtime state into the symbolic state schema.
The compact relation `C` must describe the same execution:

```
T(s, a, s', o)  <=>  C(enc(s), a, enc(s'), enc(o)).
```

This is a correspondence obligation, not a consequence of both relations using
the same variable names. It is established from checked local operation rules
and preservation of graph edges. Source initialization supplies the starting
states. Any restriction used in a proof must follow from that initialization
and preserved transitions.

State well-formedness includes legal presence/type tags, valid message order,
valid references and continuations. Check that initialization establishes it
and every operation preserves it. A symbolic output must decode to a valid
source output; otherwise the right-to-left direction is unproved. Local rules
must cover both normal and exceptional exits, with the source's guard priority.
These obligations prevent a missing branch or empty relation from satisfying
the correspondence claim vacuously.

An invariant `I` may overapproximate reachable states if its initiation and
inductiveness are checked. Proving agreement for every state in `I` is sound.
A disagreement found only in `I` is a candidate, not a source counterexample:
it requires a feasible execution from initialization. Exact compilation and
this optional reachability overapproximation must be labeled separately.

For a proposed reduced state `q(s)`, the deterministic part of the MDP claim is
that independently reachable states with the same `q` and the same executed
action produce the same next `q` and relevant outputs. This is checked with two
copies of `C`, sharing only the values explicitly identified by the theorem.
Reward, termination, truncation counters and the shield interface retain their
existing separate obligations. Random scenario initialization does not justify
assuming arbitrary later random transitions have this deterministic property.

The equal-state comparison fixes the same immutable model parameters and
includes any counters required by the existing certificate contract. It does
not equate hidden mutable sensor or message fields to make the query pass.
Define the output projection from the current runtime/certificate contract:
next reduced state and observation, safety results/errors, reward, termination,
truncation and executed shield action. Internal allocation identifiers and
proof-log formatting are not added as new controller outputs.

Finite-history reconstruction is also a separate obligation: the selected
observation/action buffer must determine `q` under this same transition
relation. An existing reconstruction rule can be reused only if its equation
and timing premises are established by the new relation. Passing closure alone
does not discharge reconstruction.

Reconstruction must cover initialization, short histories, reset and subsequent
decisions. Warmup duration and sample age may be used only when derived from
source execution. Do not restore the old bounded-sampling assumptions merely
because the compact transition solver succeeds.

For every source property `P`, the certificate must preserve its evaluation:

```
P_source(s) = P_encoded(s)
```

including undefined/error results, at its existing checking boundaries.
Discretization then proves interval preservation using those same state
meanings and the declared continuous evolution. Replacing a stored reading by
a physical trajectory is not an allowed proof shortcut.

## 4. Compact equations and conditional evaluation

Each write creates a named value version. For example, sampling physical value
`x` into held reading `h` under guard `g` produces:

```
h_after = ite(g, x_before, h_before)
```

Here `ite` means if–then–else. The equation explicitly preserves the hold case.
A subsequent physical update writes `x_after` without changing `h_after`.
Sending a message copies the appropriate value version into its payload;
receiving it reads that payload, not the then-current `x`.

A sequence of `n` conditional assignments is stored as `n` equations with
references to shared expression nodes. It is not converted into up to `2^n`
paths. At a branch with different source successors, use two guarded graph
edges and a shared join. At a join, select incoming versions with guarded
equalities or `ite`; do not duplicate the successor blocks.

This transformation has a direct proof. For each Boolean guard, `ite(g,u,v)`
equals the selected branch value. Induction over a finite block establishes
equality of all written values and its exit state. Untouched storage satisfies
explicit frame equations: its value is unchanged. This preserves operation
order and does not require assuming branches are independent.

Expression sharing is keyed by typed operation, operand versions and evaluation
context. Source text alone is insufficient: the same expression before and
after a write can have different values. Shared expressions remain a directed
acyclic graph in memory and in certificates; serialization must not expand
them back into expression trees.

Short-circuit evaluation must also be preserved. An expression is represented
by its value and evaluation status. The right operand of `and` is required only
when the left operand is true; the unselected arm of a conditional contributes
no error. Potential division errors and missing references remain guarded.
Merging branches must not evaluate an inactive source expression.

Use an explicit activation guard for each expression and write. For example,
on a successful Boolean left operand `L`, the right operand of `L and R` has
activation `active and L`. Only active successful writes change storage. When
joining branches, merge value, presence, type and status together; retaining
just the value can hide an error or invent an absent field. Keep conditions
in their original order when guards have priority or expressions can fail.

Keep native numeric kinds and ordered floating-point operations. Symbolic
equations use the existing integer and IEEE binary64 semantics; compaction
does not replace them with real arithmetic or reassociate operations. Numeric
behavior and the continuous mathematical trajectory remain separate layers.
An endpoint equality over mathematical reals alone is not proof of equality
with a rounded runtime update. Identify the exact claim of each existing
interval certificate and check its numeric correspondence explicitly. Any
needed rounding/error bound must be derived and verified, not introduced as
a new tolerance or silently treated as zero.

## 5. State representation without configuration enumeration

Build a state schema from declared source storage and runtime operation
semantics. Each location carries a value, its type where needed, and whether it
is present. Physical, held, sent and received locations have distinct identities.
Machine states and continuation locations are finite source labels stored as
values, not reasons to duplicate the graph.

Message handling must preserve order and replacement semantics. The existing
send operation replaces a pending item of the same exact type, and accept
selects the first matching subtype. Where initialization and all writers prove
a finite mailbox bound, use a symbolic ordered sequence with that derived
capacity, presence bits and payload fields. The bound comes from the source's
finite sendable types and the replacement rule, not a chosen queue limit.
Specifically, prove at most one pending item of each exact type: initialization
establishes it, replacement preserves it, and removal cannot increase it.
Subtype matching does not merge distinct exact types. Reject an unsupported
writer or initializer if this argument fails; this implementation does not
add an unbounded queue backend or truncate the mailbox.

The existing source-call graph supplies continuation structure. Acyclic calls
permit a stack bound derived from that graph, with symbolic stack contents.
Recursive dispatch remains explicitly unsupported unless separately handled;
it is not silently bounded. This retains the current recursion restriction.

Object sharing must be represented where runtime behavior observes it, including
message removal/equality behavior and floating-point NaN identity cases already
covered by tests. Use explicit references for shared payloads rather than
assuming structural equality is always enough. A value-only representation is
permitted only after establishing that identity cannot affect the relevant
operation. Derive the maximum live payloads from mailboxes, local references
and live continuation frames. Reuse a slot only when it is no longer referenced.
Identity renaming requires preservation of equality and aliasing, not equality
of arbitrary allocation numbers. If a finite supported representation cannot
be justified, report that source operation as unsupported rather than building
a general symbolic heap. This avoids both configuration enumeration and an
unplanned interpreter rewrite.

The schema is shared across graph locations. Sparse updates and named unchanged
state components avoid copying whole dictionaries into every equation. Presence,
type, machine state and message-order choices remain symbolic fields.

Use statically typed fields when source operations establish one kind. Use
tagged values only where actual writes can change kind or presence; declared
SysML types alone do not establish Python runtime kinds. Persist constants and
source-established relations across joins. Never replace the whole incoming
state with fresh unconstrained values as an optimization.

## 6. Loops, constraint propagation and lazy proof queries

Represent each block by a relation between entry state and exit state, with
typed operation equations in its body. Compose those relations along the
source graph. A loop remains a recursive relation; compilation does not unfold
it to a guessed number of scans. A decision transition stops at the first
subsequent decision or existing terminal/error boundary.

Constraint propagation needs its own compact representation because it currently
creates many branches inside a single source operation. Preserve its source
ordering, convergence comparison and existing iteration limit. That limit is
`number of constraints + number of flows + 2` in the current source graph.
Represent passes with named versions and an `active` flag through exactly that
source-derived limit. A pass that returns or raises a source-terminal error
disables later passes; those passes hold state and emit no additional events.
Temporary unresolved constraints remain pending exactly as in the runtime and
do not themselves end propagation. This bounded straight-line encoding avoids
requiring a second recursive solver for an already bounded
runtime algorithm. Symbolic implication guards condition the assignments.
Convergence and failure remain formulas rather than an enumeration of all combinations of
constraint truth values. Preserve individual failed-constraint indicators and
their source order so execution-error evidence can be reconstructed exactly.

The iteration limit here is the simulator's existing algorithmic limit. It is
not a new cap on controller scans or on proof coverage.

The solver first receives the compact relation needed by the particular
obligation. Dependency closure includes control dependencies, writes, presence,
errors and message effects, not just arithmetic references. Omitted components
need a checked irrelevance argument; they cannot be dropped merely because a
test did not exercise them.

For acyclic regions, use named intermediate equations directly. For cycles, use
recursive relations and independently checked invariants or summaries. Any
proposed summary must satisfy initialization and preservation obligations before
it is used. Failure to find an invariant does not authorize a fixed scan schedule.

Process strongly connected components of the source graph separately. Reuse
acyclic blocks and verified component summaries by reference instead of
recursively substituting their contents at every caller. Cache keys include
source/runtime identity, numeric semantics, dt, state schema and proved
premises. Sharing syntax is allowed before proof; reusing a verdict requires
all of those premises to match.

Finite-output agreement and progress are distinct checks. A relation describing
terminating executions does not prove that every execution reaches a next
boundary. Retain the progress gate: acyclic control flow can discharge it
structurally; cyclic behavior requires a checked termination argument for the
admissible states and actions. A ranking function must decrease in a well-founded
order on the relevant loop edges. Otherwise retain UNKNOWN for the existing
MDP claim. Do not invent a divergence outcome or alter the controller interface
to make that claim pass.

Lazy evaluation means the compiler preserves shared conditions and the proof
backend refines them when needed. It does not mean sampling paths or declaring
unexplored paths safe. If a backend requests a split, retain complementary guard
coverage and cache the resulting shared subproblem. No global Cartesian product
of unrelated conditions is constructed.

There is no new custom search engine in this design. Use named equations in
the existing SMT backend and the existing lazy discretization checker.
Recursive source components require a backend-feasibility check before further
integration: the current combination of recursive relations, IEEE floating
point, integers and tagged storage must not be assumed tractable or even
supported simply because Z3 accepts the formulas. Prove actual small source
components, including one cyclic case, and obtain independently checkable
evidence. If that fails, retain the compact representation and identify the
specific unsupported theory/obligation before designing another backend.

## 7. Property preservation and discretization integration

The default is no substitution between physical and delayed storage. A source
live binding can be expanded with its exact expression; an assignment, sampling
event or delivery event is an update, not a timeless equality.

For a proposed optimization substituting `h` by expression `e`, establish
`I => h = e` at the exact use boundary, or establish equivalence of the complete
property there. `I` must be an independently checked reachable-state invariant.
For floating-point values, equality must preserve the operations in which the
value is used, including signed-zero behavior; ordinary numeric `==` is not
automatically sufficient. Record the premise and proof in the certificate.

In particular, a dependency path `h -> x` supplies provenance only. It never
authorizes `h = x`. The archived `y = x + 1` counterexample becomes a permanent
rejection regression.

The discretization stage retains its factored sampled-point and interval
obligations and its lazy checker. Physical quantities follow their declared
trajectories. A stored reading holds between its actual update events. If a
sample, delivery or action change occurs within an interval, the relation must
account for that event and its order; holding through it requires proof that
no such event occurs. Reuse interval endpoints and event handling only with
that correspondence established. Do not infer observation timing from a
dependency graph.

The certificate checker reconstructs property expressions, storage identities,
operation ordering and interval bindings from the fixed source. It verifies
transformation evidence, not just hashes or a producer's reported solver result.
Changing a sampled reference to a physical reference must be rejected even when
the certificate's hashes are recomputed.

Check the producer's `expand_definitions` and the verifier's
`_expand_definitions` as well as post-update substitution. Both currently follow
the definitions table without first testing sampled storage. Establish that a
stored sampled location cannot appear there as a timeless definition; reject
conflicting classifications before expansion. Guarding only the later
post-update function would miss an earlier replacement.

Compiler and checker must not establish semantic correspondence merely by
running the same lowering code twice. The checker validates typed local rule
instances against a separately specified operation semantics, guard coverage,
frame conditions and source order. SMT equivalence checks over uninterpreted
operation names prove composition only, not the meanings of send, accept or
constraint solving. Runtime comparison tests support that validation but are
not universal proofs. The parser and local semantic rules form an explicit
trusted base, tied to the fixed runtime version.

## 8. Integration sequence and completion gates

Start with a reversible source snapshot, including the existing uncommitted
work. Record protected input hashes. Before pipeline rewiring, establish the
storage bounds and complete the solver-feasibility check above on the workstation.
This is an additional development check, not a shortened substitute for the
full validation run. Its output is a supported-operation/schema inventory and
checked solver evidence, not a benchmark certification claim.

Implement and test the typed conditional equation representation first, then
the symbolic state schema and local rules
for assignment, hold, copy, accept and guarded control. Reuse the existing
compact-block coverage checks. Prove local rule equivalence before composing
the decision relation.

Next replace `compile_program`'s configuration/path worklist with translation
over source nodes and shared operation relations. Replace the dependence in
`source_solver.py` on lists of expanded configurations. Keep the public
`one_step_transition_closure` interface so certificate generation and checking
can consume the new evidence without changing model execution.

Then connect finite-history reconstruction and the property-preservation checks
to the same transition semantics. Update certificate evidence versions and
source/runtime identity checks. Old path-enumerator certificates are not treated
as evidence for the new implementation. Remove the enumeration path from normal
certification once the new backend is wired; it must not run as an automatic
fallback after a timeout.

Require one complete source-to-certificate route before extending integration
across every caller: local rule checks, next-decision relation, progress,
reconstruction and independent certificate verification must connect. Use the
same source-driven implementation for each model; do not introduce model-name
branches or hand-assigned sampling calendars. Do not add unrelated features
while resolving a failed obligation.

Finally verify the discretization reduction's use of distinct storage, retain
its factored obligations, and rerun the pipeline. A stage is complete when its
generator and independent checker both succeed. A resource stop, unsupported
operation, counterexample or solver UNKNOWN remains an unsuccessful proof; none
is converted into a certificate to make the pipeline finish.

## 9. Validation on the workstation

All computational validation will run through Harnesslite with
`placement.target_worker = "kubuntu-workstation"`. The documented coordinator
is `http://100.113.122.109:8765`; worker setup and job submission are described in
`infrastructure/harnesslite/docs/GETTING_STARTED.md` and
`infrastructure/harnesslite/skills/local-gpu-harness/references/job-spec.md`.
Use a pinned Git snapshot containing the implementation and tests in the
worker's project checkout. Record its commit and protected-source hashes in
each result. There is no desktop fallback.

Validation starts with semantic regressions: delayed readings differing from
physical values; conditional holds; message order, replacement and copies;
short-circuit errors; native numeric edge cases; and existing check timing.
Mutation tests must reject changed property operands, guards, operation order,
missing writes and altered certificate evidence with recomputed hashes.

For finite small fixtures, compare compact equations with exhaustive semantics
and prove both directions with SMT. These fixtures validate the transformation;
the benchmark theorem still requires complete certification. Include a family
with increasing numbers of independent guards and measure representation size
to verify that it tracks source operations rather than enumerated paths.

Measure size before and after solver translation and certificate serialization,
not just in the intermediate graph. For a fixed state schema, extending the
independent-guard fixture must add equations in proportion to source operations.
Record schema width separately: a dense state vector across every edge can cost
`O(source edges * state fields)` even without path enumeration. Use sparse
frames and checked live-variable signatures to avoid that duplication. Also
test error-producing guards and constraint convergence, since simple Boolean
assignments alone would miss the observed failure mechanism.

Retain generality tests with renamed instances, different component counts,
changed source periods/phases and state-dependent guards.

Run existing source-execution, certification and discretization tests, then all
600 saved-policy episodes using their original seeds and checkpoints. Compare
runtime values and original requirement results with the compact semantics at
the same boundaries. Run the full configured pipeline with `dt=0.1`, six
training jobs, automatic collection, three collection workers per job, spawn,
override tolerance 0.01, optimization timeout 250 ms and SMT timeout 30000 ms.
These settings and episode counts remain unchanged.

Run one top-level validation job at a time, scheduling independent tests and
model queries concurrently inside it. Preserve dependency ordering between
proof generation, independent verification, and discretization. Use focused
component checks during development and require their success before expensive
dependent validation. Put the whole job and all children in one cancellable
foreground scope with an aggregate 64 GiB memory limit and zero swap. Admit
concurrent jobs according to CPU availability and memory reservations, with
headroom inside that aggregate limit. The
Harnesslite `ram_gb` request is scheduling information, not a memory limit, so
verify enforcement separately. Record peak resident memory, elapsed time,
equation nodes, source relations and actual lazy splits. If interrupted, verify
that the entire child-process group has stopped before another run starts.

## 10. What success means

Success is a source-faithful, compact transition representation that retains
distinct physical and delayed values, passes the independent transformation
checks, and supports the complete MDP and discretization certificates for the
configured models. The independent certificate checks and original-property
runtime tests must also pass. Compilation must no longer allocate a separate
configuration for every combination of numeric conditions.

The representation change removes an avoidable exponential construction step.
It does not establish a solver runtime bound. Performance will be reported from
the full workstation run alongside proof outcomes, so a fast rejected query or
passing execution test cannot be mistaken for completed certification.
