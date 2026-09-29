# Source/execution correspondence: current implementation

Updated 2026-09-19. The proof pipeline contains a composed control-flow relation
and checked compact blocks. The blocks preserve that relation, but its runtime
operations have not been lowered into the complete next-decision equations used
by the MDP solver. The bundled models therefore remain uncertified.

## Fixed inputs and execution behavior

- Preserve the current CLARITY-compatible initialization handling, including
  `parser_values.py`; this implementation changes neither `=` nor `:=` handling.
- Preserve the corrected mixing liveness and termination properties and the
  corrected cruise controller contract, together with the existing proposition
  precedence. All 12 safety requirements retain their current source meanings.
- Preserve declared numeric types, including mixing's Integer declarations.
  Earlier statements in this document about changing them to Real are obsolete.
- Preserve the separate physical, held sensor, sent payload, and received values.
- Check all source safety properties together at completed initialization and
  `cycle_end`. Apply the final controller response before reporting completion.
- Preserve scenario generation, the 5 mL reserve, normalization, policies,
  observation/history interfaces, and experiment settings.

No model, parser, initialization, runtime, shield, or training change is made by
this transition-composition implementation.

## Composed transition description

`certification/decision_transition.py` composes the existing ordered source
programs into a graph. Its nodes retain assignments, declaration hoisting,
branches, performed actions, sends, accepts, state-machine dispatch, constraint
solving, neural requests/responses, completed-cycle checks, and completion.

Every node reads the preceding node's values. Explicit storage writer sites and
frame conditions identify which values may change and which are retained.
Reference lookup order, live bindings and stored aliases remain distinct from
sample copies. Send copies the current payload; accept copies the received
attributes. A later physical update does not change an already sent value.

The graph retains the existing simulator order: process state machines, solve
constraints, install dt, execute step bodies in source/parser order, advance the
engine clock, check all properties, then test pending completion. Synchronous
message dispatch calls the recipient machine and returns to the sending action.
The first enabled transition is selected in source order. Trigger attributes
are copied before its guard; a false guard retains those writes.

The next decision may follow several cycles. The graph retains that loop,
execution errors, blocked accepts and infinite paths without assuming a fixed
cycle count or eventual progress. Initialization is an explicit prefix using the
existing runtime algorithm and original parsed initialization records.

`compose_path` assigns separate read/write versions along any supplied finite
path. It verifies continuations and machine returns, retains branch conditions,
and labels incomplete paths as prefixes. It does not prove path feasibility or
that a finite set of paths covers an unbounded loop.

Execution inventory version 4 carries this description and its compact blocks in certificates. The
checker uses the same version constant and reconstructs the graph from the
current source and runtime. A recomputed hash alone cannot authorize an altered
order, copy operation, property list, or runtime identity.

## Checks and remaining solver work

`tests/certification/validate_decision_transition.py` independently walks source
operations, checks state/update coverage, rejects modified descriptions, tests
sequential versions and copies, and compares unchanged runtime executions at the
same decision/completed-cycle boundaries. It also checks reference lookup,
assignment targets, mailbox copies, constraint writes and elapsed time.

These tests do not establish the MDP theorem. The existing equation solver still
needs a checked lowering of the composed relation, including loops, initialization
and numerical behavior. Missing next-state equations remain errors; the new
`decision_transition_solver_required` diagnostic prevents an inventory or finite
trace from being accepted as that proof. The legacy equation view's machine-state
encodings are explicitly linked to `current_sm_state` storage; their encoding
correspondence is not silently assumed.

Continuous-interval preservation and the encoded controller-history theorem
remain separate proof obligations in the reviewed implementation plan.

## Compact equations and their checks

`certification/compact_transition.py` groups single-entry paths into basic blocks.
Each operation has a named input and result configuration. The next operation
uses the preceding result. Branches, cycles, calls, decisions, requirement checks
and exceptional exits retain their source edges. No model names, sensor counts,
periods or phase assumptions select the transformation.

Repeated expression syntax is shared without evaluating it early. Short-circuit
operators and conditional expressions retain their original expression trees
when decoded. Reference lookup order, live bindings, stored samples, sends and
accepts retain their separate source operations. The physical-step equations
used by discretization are not replaced with decision-transition blocks.

The independent structural checker decodes the expressions, verifies every
operation against the source graph, checks intermediate inputs, checks all
successors and rejects missing or repeated operations. It does not call the
compaction builder. Recomputed hashes cannot authorize semantic differences.
An additional Z3 query checks that equations with named intermediate results
equal direct source-operation composition at every block exit. Runtime operations
are uninterpreted in this particular query: its claim is preservation of the
representation, not the MDP theorem or numerical/runtime correspondence.

The existing numerical equation solver also uses named intermediate constraints.
For example, three squarings remain `x1 = x*x`, `x2 = x1*x1`, `x3 = x2*x2` rather
than expanding to a degree-eight expression. Every defining constraint is added
to the SMT query. The reported polynomial degree is the largest degree of an
individual constraint. Missing transitions, recursive definitions and unsupported
dynamic divisions still reject certification. The trajectory encoder retains its
original direct-expression interface.

Certificate generation reuses one extraction for its equation and dependency
views. Verification independently extracts the source once, checks compact
correspondence and reruns the relevant solver checks. It does not accept compact
representation preservation as a replacement for next-decision closure.

Validation for this change is recorded in
`outputs/compact_transition_20260919/RESULTS.md`, with a pre-change source snapshot
and a separately reversible implementation patch. The full pipeline still stops
at Stage 3 because the complete source-to-next-decision equations remain missing;
the new compaction checks do not resolve that missing implementation.
