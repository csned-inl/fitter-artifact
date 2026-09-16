# Source/execution correspondence: implementation status

This integration is **incomplete**. A source-bound action inventory is implemented;
it is not a composed decision-to-decision transition proof. The three bundled
models remain uncertified. Passing regression tests is not a safety theorem.

## Source rules and actual implementation

| Construct | Authority and representation | Checked behavior / remaining work |
|---|---|---|
| `=` versus `:=` | Source declaration operator; parsed binding versus initial-value records | Live evaluation versus once-only initialization. Assigning to a bound feature is rejected. |
| Operator precedence | KerML 1.0 §8.2.5.8.1 and the reference expression grammar linked below | `implies` now associates to the left. Comparisons precede equality, which precedes `and`/`or`. The shield's heuristic regrouping is removed. |
| Boolean and numeric values | Source attribute/input/message declarations | Boolean/numeric operator misuse and incomplete expressions reject. Declared Integer sorts are retained in the solver; assigning a Real result cannot silently change an Integer declaration. |
| Physical versus held values | Source storage identities and assignment/send/accept events | Separate physical, sensor, payload and receiver entries. Send snapshots its payload. No replacement of a reading by current physics. |
| Requirement inventory | Independent scan of source declarations, then comparison to parsed records | All 12 safety requirements, all tags, expression text/AST, source spans and declared types are retained. |
| Action bodies | Ordered AST of assignments, branches, performed actions and calls | Both branches and per-event read/write identities retained. This is an inventory; decision composition and its independent proof are not implemented. |
| Runtime initialization | Scenario draw precedes any source advancement | Existing distribution and 5 mL reserve retained. No inactive reset controls. Initial failures remain in evaluation denominators. |
| Runtime outcomes | Structured reset/advance result | Decision, completion and execution error wake the caller; no invented action/observation on an error. Completion is the environment's `#Completion` boundary, not proof that physics stops. |
| Requirement accounting | Original AST at initialization, assignments, accepts, decisions and cycle ends | Every false result/error persists in a monotonic event ledger. These observations do not prove coverage of all source-permitted executions or continuous interiors. |
| Encoding | Fixed source/dt-indexed normalization record; actual float32 output | Existing scales preserved without executing controls during construction. Float32 injectivity and equality of deployed transition laws are unproved. |
| Proof artifacts | Markov 4 / reduced spec 3 / discretization 5 / verification report 2 | Source inventories, types, equations and interfaces checked; old artifacts reject. Arithmetic predicate subproofs cannot authorize full source preservation. |

References: [KerML 1.0, §8.2.5.8.1](https://www.omg.org/spec/KerML/1.0/PDF)
and the [SysML reference expression grammar, `ImpliesExpression`, `AndExpression`, `EqualityExpression`](https://raw.githubusercontent.com/Systems-Modeling/SysML-v2-Pilot-Implementation/master/org.omg.kerml.expressions.xtext/src/org/omg/kerml/expressions/xtext/KerMLExpressions.xtext).
The former specifies precedence; the latter explicitly constructs left-associated
implication chains. Neither source authorizes CLARITY's former heuristic regrouping.

## Approved source corrections and remaining runtime failure

The user approved the two proposed source corrections on 2026-09-16. Git
checkpoint `e7aa3ce` preserves the complete implementation before these edits.

1. **Cruise controller contract — corrected.** The source now explicitly uses
   `(target > speed + tolerance and gap >= safe) == throttle` and the corresponding
   parenthesized brake condition. The shield evaluates that literal expression;
   it does not repair precedence internally. The five safety requirement
   expressions are unchanged.
2. **Mixing numeric domain — corrected.** Physical tank levels, volume-reading
   payloads, Modbus volume responses, stored observations, and the two current
   volume policy inputs now declare Real. Integer addresses, initial scenario
   values and transfer targets remain Integer. This explicitly permits fractional
   values along the volume path; it does not establish the missing numeric or
   continuous-flow proof. All four safety requirement expressions are unchanged.
3. **Mixing requirement during initialization.** With reference-grammar
   implication grouping, `Fluid Transfer Liveness` becomes false while the
   first scan is being initialized. The source updates its scan timestamp before
   accepting its two readings and applying the actuator outputs. The ledger
   retains this result; it does not infer atomic scan behavior from the prose
   comment about the next scan cycle.

## Unresolved proof obligations

These are proof obligations, not replacement safety properties:

- Define/check permitted initial states, including simulator defaults for
  uninitialized Boolean/Real attributes and scenario replacement of literal
  tank initial levels.
- Establish source-supported cross-part scheduling, action atomicity and
  message delivery. The runtime currently visits parts in parser order,
  dispatches recipient state machines synchronously and uses latest-per-type
  mailboxes. Recording that behavior is not a proof of source correspondence.
- Compose these events through the next decision, retaining paths that block,
  terminate or have no next decision. Do not treat missing receive equations as
  holds, or cycle-count age bounds as observed decision history.
- Establish the continuous flow and numeric relation. A numerical update by
  `dt` is not an enclosure proof; decimal-to-binary64 and normalized float32
  rounding also require a checked relation.
- Prove initialization, execution coverage, correspondence, controller
  premises, property transfer and induction for each source requirement.
  The current sampled-point and physical-interval arithmetic backends do not
  supply these additional proofs. The checker therefore refuses full safety
  certification, including for the small algebraic positive fixture.

## Scope of installed checks

The new source-preservation inventory rejects omitted/changed obligations and
unsupported evidence. It deliberately has no accepting rule for the unfinished
source correspondence proof. Implementing that rule, ordered decision
composition, and the actual encoded-interface MDP proof remains required work.
No complete positive artifact chain or bundled-model safety certificate is
claimed by this integration.
