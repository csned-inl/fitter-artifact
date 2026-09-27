# Compact source proof compiler

Implementation record, 2026-09-19. This records compiler integration; benchmark
certification results are recorded separately in the workstation run report.

## Production path

`source_solver.check_source_transition_closure` now calls `lazy_graph.compile_graph`.
The compiler lowers each source node once. `lazy_solver.LazyTransitionQuery`
connects those equations through guarded joins for acyclic decision intervals
and recursive Horn relations for cyclic intervals. The former configuration
enumerator is not a production fallback. Its classes remain available to the
existing comparison tests.

The certificate implementation identity includes all compact compiler modules.
The certificate checker re-extracts the source and invokes the same public proof
entry point; old enumerator identities cannot validate as the new encoding.
Buffer selection now invokes the source-history proof after transition closure succeeds. The former scalar reconstruction searches are not used to select production buffers.

## Stored values and ordering

Scalar locations preserve presence, numeric kind, value, alias/live-binding
classification and dictionary insertion order. Physical quantities, held sensor
readings, sent copies and received values occupy distinct locations. No
physical-to-sampled or sampled-to-physical replacement is introduced.

Messages retain exact-type replacement, first-compatible-subtype selection,
parent-port fallback, payload copying, attribute insertion order and shared
object references. Calls retain shared local dictionaries and saved machine
frames. Storage bounds come from the source call graph and mailbox replacement
rule. Exhausting a derived bound is a proof failure, not a discarded execution.

Constraint propagation uses the source's existing pass limit and update order.
Its convergence test preserves Python dictionary equality, including observable
NaN identity. Original source requirement boundaries and error results remain
part of the transition output.

An immutable parameter must have scalar storage after initialization and an
unchanged cell in every subsequent node relation. An empty source inventory of
writers is not accepted as an immutability argument.

## Why numeric congruence is a sound first proof query

The acyclic equality query first replaces each deterministic numeric operator
with an uninterpreted function of exactly the same argument and result sorts.
Each occurrence of the same operator uses the same function. Its arguments,
guard, operation order, value location and error propagation remain present.

Let `F` be the exact quantifier-free counterexample formula and `A(F)` the
formula after this replacement. Every valuation satisfying `F` extends to a
valuation satisfying `A(F)`: interpret each introduced function as its original
numeric operator. Structural induction on the expression DAG establishes this
inclusion. Consequently,

```
SAT(F) => SAT(A(F)),
UNSAT(A(F)) => UNSAT(F).
```

This permits proving agreement by congruence without evaluating every possible
floating-point operation. A satisfiable abstract query is not a source
counterexample. It is retried with exact arithmetic within the remaining query
timeout before recursive reachability is attempted. A timeout is not a proof.
Failure of the recursive query remains an unsuccessful certification result.

The serialized acyclic proof query records this abstraction method and can be
replayed in a separate SMT solver. The regression fixture checks both proof
replay and rejection when a required state variable is omitted. A separate
regression checks that distinct operators and operands remain distinct.

## Coverage and progress

Every nonterminal node must select a represented successor. Allocation, stack,
mode and successor-coverage failures are included in the bad-state query. They
are never assumed away. Recursive relations describe finite executions to the
first next decision, terminal result or error. Proving their output agreement
does not itself prove progress; cyclic source intervals still require a
termination argument before the MDP gate can pass.

## Workstation validation

All execution and solver tests run through Harnesslite on
`kubuntu-workstation`, under an enforced 64 GiB per-process-group memory limit
with swap disabled. The full pipeline retains its existing model inputs,
parser, runtime, checkpoints, seeds and experimental settings. Git snapshots
are recorded on `codex-compact-lazy-worker`; the user's ordinary index and HEAD
are preserved.

## Checked constructor propagation and scalar layout, 2026-09-20

`type_facts.py` propagates finite sets of runtime constructors, cell presence,
and storage modes through the source equations. A separate edge replay checks
the proposed facts, including exception and decision-resume edges. Unknown
value expressions retain all constructors. Unvisited nodes keep unrestricted
facts rather than supplying an unreachability assumption. Physical and held
locations are never merged.

The compiler uses the checked incoming facts to preserve constructors across
source-node boundaries. Float-only and integer-only arithmetic are selected
before constructing mixed-numeric branches. Solver equivalence tests compare
the specialized operations with the generic operations, including errors,
unbounded integers, signed zero, infinities and NaNs.

The recursive state signature uses native payloads for cells with a checked
single non-Absent constructor. Optional values retain an explicit discriminator.
Raw payload values of cells with `Cell.present == false` remain represented.
Packing is injective on the checked domain, including signed zero and Absent.
Mixed cells and message heaps retain their generic representations.

The certificate includes the derivation facts and scalar layout. The checker
compares them with a fresh source-derived result, and the compiler implementation
identity includes the analysis module. Execution provenance includes the
propositional parser itself as well as the value parser.

The current implementation first lowers a generic graph to derive transfer
facts, then lowers the specialized graph. It therefore still pays for generic
construction once. The worklist and its checker terminate over finite domains.
This does not discharge controller progress or finite-history reconstruction.

The continuation is saved on `codex-proof-continuation`, without changing the
user's ordinary Git index or HEAD. Full validation outcomes are recorded in
the workstation output directory rather than inferred from component tests.

## Source control and mailbox bounds

Return edges now connect to source-compatible callers. Nested machine bodies
are analyzed separately, and shared bodies retain every compatible continuation.
An unclassified return retains all continuations. Structural progress uses shared
procedure summaries and an acyclic local graph, without enumerating call stacks.
Source loops that remain cyclic still require a separate termination argument.

Mailbox type capacities come from the declarations of objects sent to each
port. Accepted objects retain every compatible declared subtype. Missing sender
provenance falls back to all declared types. This changes storage bounds only,
not message replacement, delivery order, or the source runtime.

Named expressions preserve fixed constructors. Optional or mixed named
expressions retain the generic tagged representation. Checked node inputs and
scalar layouts still retain their optional domains. The generic graph remains
available for comparison tests. Complete-node equivalence tests compare decoded
state, successor, errors, representation obligations and requirement events
under equal inputs using independently renamed solver variables.


## Constant dataflow and arithmetic abstraction

`constant_facts.py` derives per-field constants over all source edges. Its
checker replays initiation and every edge with freshly constructed evaluators.
Facts about a sampled field are never inferred from its physical provenance.
The worklist uses reverse postorder. Each node retains expression results until
an incoming fact changes, then invalidates that term and all cached ancestors.
Only changed outgoing facts need another meet with a successor's existing facts.
These changes affect the calculation of invariants, not the set of source edges.
Regression tests compare both incremental transfer results and the final worklist
result with fresh evaluation and the previous full FIFO computation.

Before arithmetic abstraction, the acyclic query simplifies under its explicit
checked constant equalities and retains those equalities. If `E` is the
conjunction of the substituted equalities, congruence establishes
`E and F <=> E and substitute(F)`. Native numerical simplification therefore
happens before operators become uninterpreted. Physical and held storage keep
their separate expressions. The abstraction's function signatures include
actual argument counts and sorts because Z3 shares some associative operator
declarations across different argument counts.

Repeated renaming uses the same native Z3 substitution operation with its
argument arrays prepared once. Source and replacement terms, as well as cached
input expressions, remain strongly referenced to prevent AST identifier reuse.
The independent solver/checker still reruns proof obligations. These compiler
optimizations alone do not establish a production MDP certificate.

Workstation validation now runs each command in a foreground systemd scope,
with 64 GiB memory and zero swap. The scope stays in Harness's process group,
so cancellation reaches both the command and its children. Production solver
queries retain their 30-second deadline.


## Connected source reconstruction and validation workflow, 2026-09-20

Certificate schema 6 selects buffers in the configured lexicographic order by
checking reconstruction against the source transition relation. UNKNOWN does
not prove a smaller buffer insufficient. The selected source-history evidence,
target state, graph identity and buffer lengths are connected through the
producer and independent checker. Scalar next-decision correspondence diagnostics
remain blocking. Replacing the buffer search does not establish correspondence
between the scalar equations used by discretization and the executed source.
The independent source replay is required even when file-hash comparison is
explicitly disabled.

The acyclic backend now applies Z3's satisfiability-preserving SSA preprocessing
before introducing numeric uninterpreted functions. This permits exact folding
of named numeric definitions before abstraction. Preprocessing, abstraction and
both abstract and exact solver attempts share one external query deadline.
The subprocess reports its current phase so a timeout identifies where it
occurred instead of being attributed speculatively.

Development validation uses focused commands selected with `--only`. Independent
suites run as separate processes under one cancellable foreground scope with an
aggregate 64 GiB memory ceiling and zero swap. The scheduler admits jobs according
to CPU slots and memory reservations, leaving 4 GiB of the enforced ceiling for
supervision. Small suites reserve 4 GiB and expensive suites 30 GiB initially.
Reservations govern admission; the parent cgroup enforces the actual total.
Dependent test phases require the previous phase to pass. Full replay and the
configured pipeline run only after all prerequisite suites pass.

The next integration work is source-to-scalar transition correspondence and
cyclic progress, followed by source-to-discretization evidence. Production
queries are tested in isolation before those dependent validation stages run.


## 2026-09-20: checked sparse state and solver regression

The source transition and history relations now use per-location signatures.
They omit only fields fixed by independently replayed constant invariants.
The remaining fields and the fixed literals reconstruct the full state.
Edges pack the destination signature. Physical and held Boolean payloads in
the delayed-state regression remain independent relation arguments.

Constant propagation now recognizes typed constructor equations, checks root
initiation, and verifies that excluded control-flow edges have false guards.
A newly feasible edge contributes every field to the join. A regression checks
this case independently of worklist convergence.

A negative transition test uncovered false acceptance with the existing
`xform.inline_eager=False` configuration. Concrete source execution with two
previous actions and one identical current action produces distinct next
observations. Both the full and sparse recursive relations returned UNSAT
when required state was omitted. Independent transition queries admitted both
concrete results. Enabling eager inlining returned SAT for the same comparison.
Disabling slicing or coalescing, enabling only linear inlining, and splitting
the two mismatch disjuncts did not repair this fixture. Eager inlining is now
explicitly enabled for both transition layouts and history reconstruction.
This records an observed configuration-dependent incorrect solver result. It
does not identify the internal Z3 defect.

Workstation snapshot `8df85cfa399f48981a2aeb0711a93680db98cba3` passed seven
concurrent suites: batching, source buffer, checked type specialization,
checked constant propagation, source call composition, source history, and
compact lazy implementation. Both full and sparse transition layouts reject
the omitted-state fixture. The complete-state fixture remains provable.
The history suite took 0.692 seconds versus 5.542 seconds before sparse layout
implementation (the suite also gained a reconstruction regression). These
are component timings, not completed production certification results.

Tests run inside one cancellable workstation scope with an aggregate 64 GiB
memory cap, no swap, and 30-second solver deadlines. Production certification
still requires successful closure, progress, history, source/scalar
correspondence, and independent certificate verification.

The subsequent 18-test transition-solver run passed on snapshot
`039fb18aeffd893d3a28c1eaefb83e1a4ab7ec56`, including full/sparse comparisons
that reject held-only state and accept physical state, and the production
closure wrapper. Thermostat and cruise-controller production closure queries
still returned UNKNOWN at their 30-second solver limits. Total elapsed times
were 86.638 and 95.302 seconds, respectively, with peak RSS 9,825,696 and
9,756,892 KiB. They ran concurrently under one 64 GiB cap. No completed
production certificate or end-to-end success is claimed.
