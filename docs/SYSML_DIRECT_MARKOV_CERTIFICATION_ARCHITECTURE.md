THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

# Architecture record: direct SysML-to-Markov certification

Status: revised conceptual record after five adversarial reviews. This document authorizes no implementation. The current reset-witness plan remains the only binding implementation plan. Work on this architecture may not proceed in parallel with that plan. If the user selects this architecture for implementation, the user must first explicitly freeze, retire, or supersede the reset-witness plan and approve a new model-specific binding plan.

## 1. Governing correction

For certification, the supplied SysML model is the complete formal ontology of the OT system. The certifier does not infer a hidden physical plant beneath the model and does not prove that the model matches external reality. The model supplier owns that correspondence.

The certifier's obligation is narrower and absolute: relative to the declared SysML semantics, it must make no semantic mistake while interpreting the model, simplifying it, formulating the requested property, or reporting a result.

The permitted direction is:

```text
SysML source closure + exact query + narrow semantic profile
    -> finite source-coverage ledger
    -> exact model equations and discrete relations in the query dependency cone
    -> checked local simplifications
    -> lifted controller-boundary transition relation
    -> minimal Markov obligation
    -> result and implementation prescription
```

The forbidden direction is:

```text
SysML source
    -> simulator implementation
    -> symbolic simulator control graph
    -> attempted recovery of the source mathematics
    -> proof
```

The simulator is outside the proof path. It may be used for development tests or concrete replay, but simulator coverage is not proof and simulator control flow is not the transition relation.

## 2. First approved scope must remain small

The first possible implementation target is only a deterministic thermostat Markov query over the exact SysML constructs that query requires. This record does not authorize:

- arbitrary SysML v2;
- probabilistic models;
- unresolved nondeterminism;
- general scheduler synthesis;
- a reusable simulator or interpreter;
- a generic abstraction or certificate framework;
- importation of the earlier CLARITY simulator, fitting pipeline, or proof machinery.

Any unresolved nondeterminism, implicit probability, unsupported construct, or unspecified behavior required by the query produces `UNSUPPORTED`. Stochastic certification or another model requires a separately reviewed plan.

## 3. Exact inputs and identities

The certifier consumes only:

1. the root SysML model and its finite transitive source/import/library closure, whose complete contents are identified for reproducibility;
2. a versioned semantic profile for the deliberately supported SysML fragment;
3. one exact certification query.

The query must state:

- the controller decision epoch;
- the controller-visible observation fields;
- the proposed-action alphabet and enabled-action rule;
- whether a shield or another mechanism maps proposed actions to executed actions;
- the exact buffer fields, ordering, initialization, and recurrence;
- the returned tuple, including next buffer, reward, completion/termination, and relevant error status;
- allowed initial and reset states;
- terminal behavior: trace ends, absorbing state, or an explicit reset transition;
- numeric representations and conversions;
- whether time or phase is fixed, included in state, or otherwise proved irrelevant.

The default controlled action is the proposed controller action. If a shield or actuator changes it, that transformation is inside the model transition relation; the executed action is not silently substituted as the MDP action.

The audit record identifies the complete source closure, semantic profile, query, parser/elaborator version, and arithmetic interpretation. Root-file identity alone is insufficient.

## 4. Definitions, assumptions, and properties must remain distinct

Every source construct must be classified according to its actual role:

- **Definition:** contributes behavior, initialization, observation, timing, or an update relation.
- **Contract assumption:** restricts conforming implementations or admissible scenarios only when the semantic profile and query explicitly say so.
- **Property:** is checked or reported but does not prune behavior unless separately and explicitly assumed.
- **Irrelevant:** is proved unable to affect the exact query.
- **Unsupported:** has no approved exact meaning in this certification scope.

A SysML `requirement` must not silently become transition semantics. In particular, a neural requirement, prohibition, obligation, or scenario constraint is not automatically an assignment or a reachability restriction. Its role must come from the approved semantic profile and query.

The implementation prescription may select behavior left open by the source only when the query makes that selection explicit and the result is parameterized by it. It may never override behavior fixed by the source.

## 5. Finite source-coverage ledger

The frontend performs conservative source-level dependency analysis beginning with the exact returned tuple, buffer recurrence, action boundary, initialization/reset, decision timing, and every construct capable of altering their numeric or scheduling semantics.

It then produces a lightweight coverage ledger for every root-model construct and every declaration transitively referenced by the model. Unused declarations in imported libraries are identified as part of the source closure but are not individually elaborated or classified. Each ledger entry is only:

- retained in the query dependency cone;
- conservatively irrelevant, with a checked reason;
- or unsupported.

The ledger covers name resolution, imports needed by the model, metadata applications, specialization/redefinition, bindings, assignments, constraints, state transitions, action calls, events, flows, and numeric types encountered by the selected model. It is coverage metadata, not a general intermediate representation.

No ledger, AST graph, import graph, scheduler graph, property catalog, or irrelevant source construct may be emitted wholesale to Z3. Only the compact equations and unresolved obligations in the proved dependency cone may reach the solver. An unclassified construct stops certification.

## 6. Direct extraction and local simplification

The extracted object is the source mathematics needed by the query:

- relevant model state and parameters;
- initial/reset relation;
- finite relevant modes;
- guards and enabled proposed actions;
- state-update equations;
- observation and proposed-to-executed-action relations;
- exact buffer update;
- decision-epoch timing;
- reward, terminal, and relevant error functions.

Extraction is syntax-directed. Simplification uses small local rewrites with checked evidence: constant substitution, affine normalization, dead-dependency removal, equivalent-guard merging, and model-specific algebra. No general abstraction-map framework is authorized.

The thermostat source contains a syntactically affine-looking thermal assignment and threshold-like requirements. That is only candidate structure until the approved profile resolves the meanings of `#ContinuousRate`, `#Completion`, policy requirements, component-step execution, message delivery, repeated commands, initialization, and decision-epoch order. The certifier must not call those expressions a complete transition system before those meanings are fixed.

Sampling and fitting are outside the first implementation scope. They cannot be built as discovery subsystems or used to justify certification. Any future proposal to use an inferred candidate requires separate user approval and an exact equivalence proof before that candidate enters a proof.

Unrelated safety properties do not enter the Markov solver merely because they are declared. A property enters only if it changes the modeled transition/result, is an explicit query assumption, or is itself the separately approved theorem being checked.

## 7. Lifted state and reachable proof domain

A history buffer is not generally a function of instantaneous SysML state. Define the lifted semantic state

```text
z = (x, m),
```

where `x` is the complete query-relevant SysML state and `m` is exactly the query-defined buffer and any relevant delay, queue, phase, reset, or other memory. The source/profile and query define the initialization and one-step recurrence of `z` directly. Let

```text
beta(z) = b
```

be the proposed controller-facing Markov state.

The positive theorem is proved on a domain `R` that:

1. has a nonempty approved initial/reset relation;
2. contains every state allowed by that relation;
3. is proved inductive and transition-closed for every enabled proposed action;
4. contains all reachable lifted states.

`R` may be a sound overapproximation. That can make certification conservative, but it cannot make a positive result unsound. Bounded exploration or an underapproximation may never support a positive result.

For the first thermostat plan, `R` must be either the declared admissible lifted domain or one fixed model-specific invariant written in and approved with the binding plan. Failure of that invariant produces `NO_RESULT`. It does not authorize an invariant generator, iterative invariant discovery, or repeated strengthening.

## 8. Deterministic controlled-Markov obligation

Before congruence is considered, the extracted deterministic decision-boundary step must be proved total and single-valued for every `z` in `R` and every enabled proposed action. An explicitly modeled terminal or error result is a defined result. A partial, conflicting, multivalued, deadlocked, or otherwise unresolved step is `UNSUPPORTED` in this deterministic scope.

Define the exact query result projection:

```text
Result_Q(z, a) = (
    beta(next_Q(z, a)),
    every controller-boundary field that Q declares returned
)
```

The returned fields include completion, termination, reward, exposed executed action, truncation, reset indicators, errors, or any other field when and only when the exact query declares them, interpreted under the query's terminal/reset rule.

For every `z1, z2` in `R` with `beta(z1) = beta(z2)`, the certifier must then establish:

1. `z1` and `z2` have identical enabled proposed-action sets; and
2. for every shared proposed action `a`, their complete query-defined one-step controller-boundary results are equal:

```text
Result_Q(z1, a) = Result_Q(z2, a).
```

This is a deterministic strong-lumpability/congruence condition for the lifted transition system. When proved over `R`, it yields one action-conditioned transition function on `b` for every allowed initial/reset state and every nonanticipating policy over enabled proposed actions.

The result is a stationary MDP only if the derived transition is time-homogeneous. If time, scan phase, or episode phase affects a result, it must be included in `b` or eliminated by proof. The first thermostat target does not authorize a time-indexed MDP generalization.

Completion and termination are not interchangeable. The query must state whether completion merely labels a result, ends the trace, enters an absorbing state, or triggers a reset, and the obligation must use that exact rule.

For affine modes, linear algebra and observability should eliminate whole continuous regions before SMT. If invisible differences satisfy `H d = 0`, the compact question is whether they can affect the next projected state or cross an action, reward, terminal, error, or mode boundary. Z3 checks only the residual compact obligations; it does not enumerate real-valued states or procedural paths.

## 9. Positive and negative result rules

Exactly four semantic result classes are permitted:

- `CERTIFIED_UNDER_CONTRACT`: all coverage, extraction, reduction, invariant, and Markov obligations are proved within the approved scope.
- `DISPROVED_FOR_QUERY`: two concrete feasible histories from approved initialization/reset produce the same buffered state and proposed action but different returned tuples. Both histories and every transition establishing reachability are part of the witness.
- `UNSUPPORTED`: a required source construct or semantic feature lies outside the approved deterministic fragment or is genuinely unspecified by the selected model/profile/query.
- `NO_RESULT`: timeout, solver `UNKNOWN`, failed proof obligation without a reachable counterexample, resource-budget failure, tool error, or incomplete analysis.

A pair of unequal states found only in an overapproximation refutes the chosen strong-lumpability proof attempt but does not by itself prove the process non-Markov. It produces `NO_RESULT` unless concrete reachable histories establish `DISPROVED_FOR_QUERY`.

Failure to find a counterexample, testing coverage, fitted residuals, bounded execution, and timeout never produce `CERTIFIED_UNDER_CONTRACT`.

## 10. Non-circular implementation prescription

The output contract is a finite operational prescription, not the desired conclusion restated as an assumption. It may specify only such items as:

- state fields and allowed initialization;
- equations and guarded updates;
- decision and update order;
- proposed and executed action relations;
- observation timing;
- buffer recurrence;
- reward and terminal/reset rules;
- numeric types, rounding, and conversions;
- allowed fixed parameters and exogenous values.

The contract may not say that the implementation "is Markov," "has no relevant hidden state," or "equals the desired kernel" as an unexplained premise. The Markov theorem must follow from the finite operational rules.

A downstream stage may implement those rules in any language or directly as mathematics. Claiming that a concrete implementation conforms to the contract is a separate obligation. It requires a total, progress-preserving decision-boundary relation: from every related implementation state, the implementation must expose the same enabled proposed-action set; accept every contract-enabled proposed action; complete exactly one decision step with the prescribed timing, arithmetic, and complete `Result_Q`; and reach a state related to the prescribed successor. Ordinary trace inclusion is insufficient because it can hide disabled actions, deadlock, divergence, or omitted behaviors. This architecture does not require or certify a simulator design.

## 11. Minimal audit bundle

Unless a separately approved proof-producing backend supplies independently checkable proof objects, the output is called an audit bundle or certification record, with the solver and exact frontend inside the trusted base. It must contain only:

1. identities of the source closure, semantic profile, query, frontend, arithmetic theory, and solver;
2. the exact operational implementation prescription;
3. the finite coverage-ledger result;
4. the compact extracted equations and necessary local reduction evidence;
5. the reachable/inductive domain statement;
6. the exact Markov obligation and solver result;
7. the result class, assumptions, scope, and qualified warnings;
8. a reachable witness when the result is `DISPROVED_FOR_QUERY`.

Warnings must distinguish sufficient conditions from proved necessities. Violating a sufficient implementation condition removes the guarantee; it proves non-Markovity only when a reachable counterexample or necessity proof establishes that stronger statement.

## 12. Mandatory anti-growth gate for every implementation plan

Before any code is authorized, the model-specific binding plan must set hard limits for:

- supported source constructs;
- retained state variables, modes, guards, and equations;
- source files and production/test modules allowed to change;
- Z3 query count;
- solver variables and assertions;
- serialized formula size and AST size;
- solve time and total runtime;
- permitted local reduction kinds.

Before formula generation, a solver manifest must name every symbolic variable and constraint and explain why it is indispensable to the exact Markov obligation. The following are always forbidden:

- simulator control-flow encoding;
- path, event, or scheduler enumeration;
- whole-AST or whole-ledger encoding;
- formula growth as a response to timeout without a new approved plan;
- construction of another SMT solver;
- one-error-at-a-time timeout patching.

Exceeding an approved complexity budget produces `NO_RESULT` and immediate stop. It never authorizes a larger encoding.

## 13. Soundness boundary

The claim assumes the supplied SysML model is the intended OT ontology. Model-to-reality validation is outside scope. Within that assumption, certification is fail-closed.

The trusted base is limited to the source/profile elaborator for the approved fragment, arithmetic interpretation, compact obligation generator, solver, and result checker. Simulator and RL-training code are outside it.

No positive result may depend on empirical simulation, statistical confidence, implicit runtime defaults, unproved translations, omitted query-relevant behavior, or solver timeout.

## 14. Decisions required before an implementation plan

The user must explicitly decide:

1. whether to freeze/retire the current reset-witness plan and select this direction;
2. the exact deterministic thermostat query and proposed-action boundary;
3. the exact supported thermostat SysML constructs and semantic profile;
4. component-cycle, event, initialization, and annotation semantics not fixed directly by the root source;
5. whether reward/termination are in SysML or explicitly supplied by the query;
6. the exact buffer, returned tuple, terminal/reset rule, and numeric semantics;
7. the model-specific complexity budgets.

Until those choices are approved, this document records direction only and authorizes no implementation.
