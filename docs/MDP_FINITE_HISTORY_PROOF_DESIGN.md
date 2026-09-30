# Finite-History Markov Proof for Buffered CLARITY Controllers

Status: soundness-reviewed design specification, revision 2; no implementation is authorized by this document alone  
Target branch for future implementation: a new branch derived from `codex/workstation-primary`  
Purpose: replace the oversized Stage 3 proof encoding with a minimal, source-bound, fail-closed proof that a selected finite observation/action history is a Markov state for the controller-facing environment

## 0. Assurance statement and permitted claim

This design requires a mathematically valid implication:

```text
all proof obligations discharged
    => the selected finite buffer is a Markov state
       for the stated formal CLARITY execution profile
```

Timeouts and resource limits affect completeness only. A timeout, `UNKNOWN`, crash, unsupported construct, failed validation, missing obligation, or unverified optimization can prevent a certificate but can never create one.

No software-based proof is unconditional. The permitted claim is explicitly relative to a trusted computing base (TCB):

- the bytes of the model and fixed runtime/profile definitions named by the certificate;
- the parser for the supported source subset;
- the small formal operational semantics defined for that subset;
- the independent translation/proof-obligation checker;
- the exact observation/reward/shield interface definitions;
- Z3's soundness for the emitted logic, unless an independently checked proof artifact is also produced;
- the certificate checker's hashing and exact-arithmetic support code.

Tests, differential replay, two solvers agreeing, and source hashes increase assurance but are not themselves mathematical proofs. The certificate must never claim equivalence to arbitrary Python runtime behavior merely because tests agree. It may claim equivalence to the actual CLARITY simulator only after either:

1. the supported simulator path executes the same validated semantic kernel used by the proof; or
2. a separately checked refinement proof establishes that the simulator path implements the formal profile.

Until then, the formal result must be worded as a theorem about the named formal CLARITY profile, with runtime correspondence reported as validation evidence rather than silently assumed.

## 1. Design objective

Given:

- a CLARITY SysML model;
- the CLARITY simulator/runtime semantics used by training and evaluation;
- a fixed simulation step `dt` and the source-owned controller decision boundaries;
- a candidate buffer containing the current observation, `b_obs` prior observations, and `b_act` prior **executed** actions;
- the exact shield, reward, intrinsic termination, and checked requirement semantics;

determine whether the buffered controller interface is a sufficient Markov state.

The proof engine must answer the following counterexample question:

> Do there exist two admissible source executions with identical current controller buffers and the same current policy proposal, but different next controller buffers, executed actions, rewards, intrinsic terminal outcomes, execution outcomes, or required property results?

For the supported deterministic profile:

- `SAT` means that the encoded abstraction contains a distinguishing pair. It is a concrete counterexample only after reachability/replay validation.
- `UNSAT` means that no distinguishing pair exists in the sound over-approximation and therefore no reachable distinguishing pair exists.
- `UNKNOWN`, timeout, unsupported semantics, failed correspondence, failed invariant checking, or exceeded complexity budget means **no certificate**.

The proof generator is not a second SMT solver and is not a symbolic implementation of the full CLARITY runtime. Its job is to extract, validate, reduce, and encode only the semantic objects needed for this relational counterexample query. Z3 performs the residual satisfiability proof.

## 2. Non-goals

This design does not attempt to:

- verify arbitrary Python or arbitrary SysML programs;
- represent every internal runtime object in SMT;
- reproduce generic dictionaries, heaps, call stacks, or tagged values unless the controller-facing theorem demonstrably depends on them;
- prove termination of unrestricted recursive programs;
- infer equality between a sampled value and its physical provenance;
- prove a chosen buffer is globally smallest when smaller candidates returned `UNKNOWN`;
- certify learned neural-network weights;
- use bounded simulation as a substitute for a proof over all admissible histories;
- silently treat time-limit truncation as intrinsic MDP termination;
- accept a solver timeout or resource failure as evidence.

The active supported profile should be deliberately narrower than the full CLARITY runtime. An unsupported construct is a diagnostic, not an invitation to expand the SMT model into a general runtime interpreter.

## 3. The theorem

### 3.1 Full and reduced decision-boundary states

Let `X` be a complete configuration of the **formal supported CLARITY profile** at a controller decision boundary. Let `R_X` be the set of configurations reachable at such boundaries from an allowed reset under allowed policy proposals.

Let `S = Project(X)` be the typed proof state obtained after validated relevance reduction. `S` may contain only source objects that are needed to characterize admissible execution or that can influence a theorem-visible result. Candidate categories are:

- physical plant state;
- sensor-held state;
- sent payload state;
- delivered/received payload state;
- actuator and state-machine modes;
- relevant clocks and scheduler phases;
- relevant immutable scenario parameters;
- relevant finite transport-validity bits;
- intrinsic completion and error state.

Each storage location has a unique identity. Provenance never identifies two locations. The source-to-IR soundness obligation requires every reachable `X` to have at least one corresponding `S` and requires every concrete decision transition from `X` to be represented by the IR transition from `S`. Exact reconstruction of irrelevant fields is unnecessary.

Let `O(S)` be the exact controller observation generated at the boundary, including the runtime's normalization and Float32 conversion. Let `A` be the policy proposal. Let `Execute(S,B,A)` be the actual extracted shield/environment action transformation, and let `E` denote the resulting executed action. The source-state argument is retained until a separate obligation proves that execution depends only on `B`, `A`, and certified immutable parameters.

The candidate controller buffer is:

```text
B[p,q] = (
    O_t,
    O_{t-1}, ..., O_{t-p},
    E_{t-1}, ..., E_{t-q}
)
```

where `p = b_obs` and `q = b_act`. Reset padding and the ordering of fields must exactly match the runtime policy-input layout.

Define the augmented controller-boundary state as `Z = (S,B)`. The proposed quotient-state map is `beta(Z) = B`. Reachability is fundamentally a relation over `Z`, not over `S` alone, because the same source state can in principle be paired with different finite histories.

### 3.2 Total one-decision semantics

Let:

```text
DecisionStep(S, B, A, E) -> Outcome
```

be the deterministic formal transition from one successful controller decision boundary. `Outcome` is a disjoint sum:

```text
Continue(S', V)
Terminal(V)
Error(V)
```

`V` contains:

- next observation `O(S')` when a next decision exists;
- reward accumulated for the controller step;
- intrinsic `terminated` result;
- elapsed simulator ticks/time and any transition-specific discount used by training;
- next dynamic action availability/mask, if the action domain is not globally fixed;
- execution error/terminal outcome;
- the ordered sequence of all required property boundary identities, statuses, and errors, including its length;
- any other environment output consumed by training or evaluation.

`B` and the raw proposal `A` are explicit inputs because shield-adjacent wrappers, reward, or other environment logic might consult controller history or distinguish proposed from executed actions. If checked dependency proofs show that the source transition and outputs depend on them only through `E`, the encoder may remove the redundant arguments.

The next buffer is the exact shift:

```text
B' = Shift(B, O(S'), E)
```

for `Continue`, with terminal and error buffer behavior defined separately by the environment contract.

The source profile must prove **progress and totality**: for every admissible decision-boundary state, compatible buffer, legal proposal, and resulting executed action, exactly one of `Continue`, `Terminal`, or `Error` occurs within the proved finite decision-interval bound. Blocking or divergence cannot be ignored or treated as the absence of a counterexample. If blocking/divergence is possible and is not an explicit environment outcome, the MDP certificate fails.

### 3.3 Deterministic finite-history Markov property

Let `R_Z[p,q]` be the actual set of reachable augmented states `(S,B)` induced by reset, exact buffer updates, and the formal source transition. Let `J[p,q](S,B)` be a checked over-approximation satisfying:

```text
R_Z[p,q] subseteq J[p,q]
```

This inclusion, not mere plausibility of `J`, is a mandatory proof obligation.

Let `Avail(S,B)` be the actual set/mask of legal policy proposals. The first profile uses a fixed finite proposal space, so this function is constant. A future dynamic-action profile must first prove that `Avail(S1,B) = Avail(S2,B)` for every admissible pair with the same buffer. Define:

```text
F((S,B), A) =
    map DecisionStep(S, B, A, Execute(S,B,A))
    to the complete visible outcome and, for Continue, the shifted B'
```

For every two admissible states and every policy proposal:

```text
J(S1, B1) and J(S2, B2)
and B1 = B2
and A1 = A2 and A1 in Avail(S1,B1) and A2 in Avail(S2,B2)
```

must imply equality of the complete outcomes under an explicitly defined equality for the disjoint outcome type:

```text
Execute(S1,B1,A1), Execute(S2,B2,A2)
F((S1,B1),A1), F((S2,B2),A2)
```

In addition, equal buffers must imply equal current availability masks. Two outcomes are equal only when they use the same constructor (`Continue`, `Terminal`, or `Error`) and every theorem-visible field is equal. Continuing outcomes additionally require equal next buffers and equal next action availability.

The existential counterexample formula is therefore:

```text
J(S1,B1)
and J(S2,B2)
and BufferEqual(B1,B2)
and ProposalEqual(A1,A2)
and (
    AvailabilityDifference(Avail(S1,B1),Avail(S2,B2))
    or (
        EnabledOnBoth(A1)
        and ValidOutcome(S1, B1, A1, Execute(S1,B1,A1), Y1)
        and ValidOutcome(S2, B2, A2, Execute(S2,B2,A2), Y2)
        and OutcomeDifference(Y1,Y2)
    )
)
```

If that formula is `UNSAT`, and progress/totality and correspondence obligations also discharge, the buffered state defines a well-defined deterministic quotient transition and is a sufficient Markov state within the certified source profile. This is the deterministic strong-lumpability/congruence condition for the quotient map `beta`.

Strictly, the theorem says that the **buffered controller-facing environment process** is an MDP with state `B`; a controller or policy is not itself an MDP. Certificate language should use that precise formulation.

The congruence condition is stronger than proving Markovianity only under one particular distribution over hidden states. It proves a distribution-independent deterministic quotient over all admissible reachable states. This can reject a buffer that would appear Markov only because of a special probabilistic mixture, but it cannot falsely certify a non-Markov buffer. That is an intentional completeness tradeoff for the initial profile.

This direct relational theorem replaces the unnecessarily broad combination of:

1. complete generic source-runtime closure;
2. reconstruction of a large internal `q` state;
3. a separate claim that reconstructed `q` determines visible behavior.

Internal state need not be reconstructed if all hidden distinctions are proved observationally irrelevant to the next controller transition.

### 3.4 Probabilistic and nondeterministic semantics

The initial implementation supports deterministic execution after reset/scenario selection. Scenario parameters sampled at reset are modeled as symbolic immutable state constrained to their declared domains. Different scenario values may occur on the two sides of the counterexample query; if they can cause different visible successors from the same buffer/action, the buffer is not Markov.

If future models contain per-step randomness, uncontrolled nondeterminism, or probabilistic message delivery, equality of one deterministic successor is insufficient. Equality of successor distributions or transition sets would be required. The extractor must reject such models until a separate probabilistic/nondeterministic theorem and encoding are implemented.

### 3.5 Soundness argument for the supported profile

The final certificate relies on the following checked premises:

1. **Forward simulation:** every reachable transition of the formal source profile is represented by a `MarkovIR` transition with the same executed action and theorem-visible outcome.
2. **Progress/totality:** every admissible source decision state/action produces exactly one represented `Continue`, `Terminal`, or `Error` outcome within the proved finite bound.
3. **Reachability inclusion:** `R_Z[p,q] subseteq J[p,q]` for the exact buffer convention.
4. **Exhaustive disequality:** the split difference obligations have a checked disjunction identical to `OutcomeDifference`, including action availability, executed action, outcome constructor, next buffer, reward, time/discount, intrinsic termination, errors, and required statuses.
5. **Reduction direction:** every proof reduction is equivalence-preserving or produces an over-approximation of possible counterexamples. No under-approximation is used to prove `UNSAT`.
6. **Solver discharge:** every reset-prefix and steady-state counterexample obligation is `UNSAT` under exact emitted semantics or a proved sound over-approximation.

Take any two actually reachable controller histories with equal buffers and the same legal policy proposal. Premise 3 places both endpoint pairs in `J`. Premise 1 gives corresponding satisfying `MarkovIR` transitions, and premise 2 ensures neither behavior disappears through blocking or divergence. If their executed actions or complete outcomes differed, the pair would satisfy one of the exhaustive counterexample obligations by premise 4. Premise 5 ensures that all concrete counterexamples remain in the solved formula. This contradicts premise 6. Therefore the complete outcome is a function only of the current buffer and proposal.

That function defines the quotient transition, reward, intrinsic termination, and required status outputs on buffer states. The exact shift equation produces the next quotient state. Reapplying the argument inductively at the next boundary establishes the Markov property for every finite continuation in the supported deterministic profile.

Because reductions, `MarkovIR`, and `J` may over-approximate source behavior, they can introduce spurious satisfying assignments but cannot remove an actual reachable counterexample. Thus `UNSAT` is sound; unvalidated `SAT` is not automatically a concrete counterexample.

## 4. Trust boundary and proof layers

The proof has five distinct layers. No layer may claim the theorem of a later layer.

1. **Source extraction:** Parse the exact model and identify the relevant runtime profile, decision boundaries, observations, actions, shield, reward, completion, and property checks.
2. **Translation validation:** Independently check that the finite typed intermediate representation preserves every supported, theorem-relevant source operation and its order.
3. **Sound reduction:** Backward-slice the validated transition and apply only checked substitutions, invariants, and solver reductions.
4. **Z3 counterexample proof:** Query each complete Markov obligation over two renamed executions.
5. **Independent certificate checking:** Re-extract, revalidate, regenerate, and rerun the required proof queries from source and recorded settings.

Hashes detect stale artifacts; hashes do not prove semantic correspondence.

### 4.1 Formal source semantics and refinement relation

Define a small-step operational semantics for the supported source subset:

```text
<program-counter, typed-store, machine-modes, transport, clock, outcome>
    --event-->
<program-counter', typed-store', machine-modes', transport', clock', outcome'>
```

The rules explicitly define evaluation order, floating-point rounding, branch priority, assignment, hold, sampling, send-copy, accept-copy, state-machine dispatch, decision suspension/resumption, reward, property checks, and terminal/error behavior. Unsupported syntax has no rule and is rejected before proof generation.

Define a representation relation `rho(C,S)` from a formal source configuration `C` to a `MarkovIR` state `S`. For proof soundness, the required compiler theorem is a **forward simulation**:

```text
SourceReset(C0) => exists S0. ResetDecisionState(S0) and rho(C0,S0)

rho(C,S) and SourceDecisionStep(C,B,A,E,C',V)
    => exists S'. IRDecisionStep(S,B,A,E,S',V) and rho(C',S')
```

with corresponding terminal and error cases. Forward simulation is the essential direction: the IR may contain extra behaviors, but it may not omit a source behavior. Extra IR behavior can create spurious `SAT`; omitted source behavior could create an unsound `UNSAT` and is forbidden.

The translation validator checks an instance proof assembled from local rules:

- expression translation preserves or over-approximates source evaluation and errors;
- every source event has a corresponding IR event or a checked non-influence abstraction;
- source sequential composition maps to IR SSA composition;
- source branch union is contained in the guarded IR branch union;
- source copy and hold rules map to distinct storage identities;
- source decision, terminal, and error boundaries map to the matching IR outcome constructor;
- the finite scheduler composition covers every source event sequence up to the next outcome.

Structural source coverage without these semantic simulation obligations is insufficient.

### 4.2 Runtime correspondence

The mathematical theorem is first established for the formal source semantics. Correspondence with the executable CLARITY simulator is a separate refinement obligation:

```text
ConcreteRuntimeStep(c,b,a,e) = (c',v)
    => SourceDecisionStep(encode(c),b,a,e,encode(c'),v)
```

The preferred implementation is to share one small, versioned semantic kernel between concrete execution and proof extraction for the supported operations. If that is not practical, keep the theorem claim scoped to the formal semantics until a checked runtime-refinement argument exists. Differential testing alone does not discharge this obligation.

## 5. Minimal typed intermediate representation

Introduce a new, isolated representation, referred to here as `MarkovIR`. It must not depend on the current `lazy_*` state representation.

### 5.1 Storage identity

Every persistent value is represented by a `StorageId`:

```text
StorageId = (
    owner_instance,
    declaration_path,
    storage_role,
    declared_type
)
```

`storage_role` is one of:

- `physical`
- `sensor_held`
- `payload_sent`
- `payload_received`
- `controller_memory`
- `actuator`
- `machine_mode`
- `clock_or_phase`
- `immutable_parameter`
- `transport_valid`
- `intrinsic_terminal`

The role is metadata used for checking and diagnostics; identity is never collapsed based on a common physical origin.

Required invariant:

```text
physical StorageId != sensor-held StorageId
sensor-held StorageId != sent-payload StorageId
sent-payload StorageId != received-payload StorageId
```

even when every value currently happens to be equal.

### 5.2 Native sorts

Each retained location has one checked native sort:

- SysML `Boolean` -> Z3 `Bool`
- SysML `Integer` -> Z3 `Int`, unless the runtime defines a bounded machine integer
- runtime binary64 `Real` -> Z3 `Float64` where bit-exact runtime behavior matters
- normalized policy observation -> Z3 `Float32` or its IEEE bit-vector representation
- finite state-machine mode -> finite enum or minimum-width bit-vector
- optional finite transport value -> `present: Bool` plus a typed payload

Do not encode all scalars in a generic `Absent | Boolean | Integer | Float | Text` datatype. Unsupported dynamic type changes fail closed. A missing optional value and a present payload are separate; absent payload bits are excluded from equality unless they are observable under source semantics.

Numeric lowering follows a versioned semantic table, not the SysML type name alone. For every supported operator the table states:

- accepted runtime constructors;
- operand-evaluation order;
- exact promotion/conversion behavior;
- IEEE-754 format and rounding mode;
- division-by-zero, overflow, infinity, NaN, and signed-zero behavior;
- comparison semantics;
- returned constructor or error outcome.

Decimal source literals are encoded from the exact runtime-parsed IEEE bit pattern. The observation encoder models the exact runtime sequence—for example binary64 arithmetic followed by the runtime's binary32 cast—rather than replacing it with mathematical-real arithmetic.

Distinguish three equalities:

1. source-language numeric comparison, such as Python/CLARITY equality where NaN and signed zero have language-defined behavior;
2. structural/store equality used by the formal runtime semantics;
3. controller-buffer representation equality, defined by the exact tensor/array value representation consumed by the policy.

The translator selects the required equality explicitly. It must not substitute one notion for another. Unsupported math-library functions, host-dependent behavior, or unproved extended-precision/FMA behavior cause rejection.

### 5.3 Event SSA

Relevant source operations are lowered to typed, event-ordered SSA equations. Every event reads explicit versions and creates explicit result versions.

Examples:

```text
x_phys@4 = PlantUpdate(x_phys@3, actuator@3, dt)
x_held@5 = x_phys@4                  # sampling event
x_sent@6 = x_held@5                  # send-copy event
x_recv@9 = x_sent@6                  # delivery/accept event
```

If the plant changes at event 7, `x_sent@6` and `x_recv@9` do not become aliases of the new physical value. A copy equation authorizes equality only at that event. Subsequent hold equations preserve the destination's own previous version.

Live SysML bindings remain expressions evaluated at their read event. Stored initialization and assignment remain writes. A translation must never turn a stored sample into a live binding.

### 5.4 Supported operations

The first proof profile should support only operations needed by the bundled models:

- typed literals and immutable parameters;
- arithmetic and Boolean expressions;
- assignments and conditional assignments;
- finite `if` branches;
- statically resolvable performed actions, inlined with source witnesses;
- finite state-machine mode updates with source-order transition priority;
- synchronous typed sends and accepts, or explicitly modeled finite transport registers;
- fixed-order constraint updates used by the active models;
- controller decision request/response;
- fixed scheduler steps and derived finite scan phases;
- property evaluation at declared boundaries;
- intrinsic completion and execution errors relevant to the environment contract.

Reject initially:

- recursion;
- dynamic allocation relevant to the transition;
- unbounded message queues;
- unresolved dynamic dispatch;
- source loops without a statically proved finite decision interval;
- dynamic type changes;
- per-step randomness;
- unsupported string/object identity behavior;
- runtime reflection or arbitrary Python callbacks.

The rejection identifies the exact source construct and location.

## 6. Source-to-decision extraction

### 6.1 Boundary definition

The extractor starts immediately after a successful controller response has been installed and ends at the next:

- controller decision request;
- intrinsic terminal result;
- source execution error.

The boundary must match `SysMLEnv.step` and the policy pause used by training. Property evaluations and reward accumulation occurring between these points remain in the transition output.

This proof concerns the simulator's discrete decision-boundary transition. Continuous-interval safety between simulator steps remains a separate discretization/safety obligation and is neither assumed nor replaced by the Markov proof.

### 6.2 Scheduler extraction

Scheduler order is source data, not a solver choice. The extractor records the actual runtime sequence, including:

- state-machine processing;
- constraint propagation when applicable;
- `dt` installation;
- part step execution in runtime/source order;
- engine clock advance;
- property evaluation;
- completion evaluation;
- controller pause/resumption.

For each scheduler stage, the extractor records:

- source/runtime implementation identity;
- input and output versions;
- enabled operation set and order;
- frame conditions for untouched storage;
- decision/terminal/error successors.

### 6.3 Finite decision interval

The new backend uses quantifier-free finite formulas and must not use recursive Horn relations.

For a controller invoked every simulation step, compose the finite remainder of the current step and the finite prefix of the next step.

For periodic controllers such as the mixing scan cycle, derive the maximum steps to the next decision from checked clock equations, `dt`, scan-period constants, reset phase, and comparison semantics. Prove the bound with exact arithmetic or a sound interval argument. Encode a finite phase/mode variable when that is smaller than repeated clock arithmetic.

If a finite next-decision bound cannot be proved, return `unsupported_or_unproved_decision_interval`. Do not respond by building a general recursive interpreter in Z3.

For a proved bound `N`, the bounded composition must encode the **first** outcome, not merely some outcome at or before `N`:

- before the selected outcome index, no decision, terminal, or error boundary has occurred;
- at the selected index, exactly one outcome constructor is active;
- later unrolled slots are inactive and cannot alter the output;
- every admissible initial state/action has at least one represented trace selecting an index and constructor;
- a separate counterexample query for `no outcome by N` is `UNSAT`;
- within each represented trace, a separate counterexample query for multiple active first outcomes is `UNSAT` or exclusivity follows structurally from the encoding.

The formal supported source semantics is deterministic. `MarkovIR` may deliberately over-approximate it and therefore admit more than one trace for the same IR input. The relational Markov queries range over every admitted trace; they may not choose a convenient output and ignore another. Extra traces can cause spurious `SAT`, never a false `UNSAT`.

These are totality and progress obligations, not performance heuristics.

### 6.4 Translation validation

The builder and validator must be separate implementations.

The validator consumes the source execution inventory and `MarkovIR` and checks:

- every retained source event appears exactly once in the correct order;
- every theorem-relevant source write has a corresponding SSA write;
- every removed source write is accompanied by a checked non-influence record;
- branch guards and source-order priority are preserved;
- every successor is represented or proved infeasible;
- every stored location has an explicit write or hold on every represented path;
- sampling, send, and receive are copy events between distinct `StorageId`s;
- live bindings are evaluated at reads and never stored by accident;
- observations, shield inputs, reward, terminal, and properties reference the correct versions;
- unsupported operations cause rejection.

The validator must not invoke the builder to compute its expected answer.

## 7. Relevance and state minimization

### 7.1 Observable seeds

Backward slicing begins from the complete theorem-visible signature:

- next normalized observation components;
- next buffer shift;
- executed action and shield status;
- reward;
- intrinsic `terminated`;
- elapsed ticks/time and transition discount when consumed by training/evaluation;
- dynamic action availability/mask;
- source execution outcome/error;
- every required property status and error at every checked boundary;
- control decisions that determine whether and when the next decision occurs.

Time-limit `truncated` is handled separately as described in Section 11.

### 7.2 Dependency closure

The slice includes:

- data dependencies;
- branch/control dependencies;
- scheduler-phase dependencies;
- state-machine mode dependencies;
- presence/delivery dependencies;
- frame/hold dependencies;
- writers capable of changing a retained location;
- immutable parameters used by any retained expression;
- initialization constraints for retained state;
- constraints required to establish invariant premises or eliminate an infeasible branch.

A source field is not removed merely because it is absent from a visible expression. It can be removed only after proving it cannot affect any retained value, control choice, outcome, or admissibility constraint.

### 7.3 Reduction records

Every removal or substitution emits a replayable record:

```text
ReductionRecord(
    rule,
    source_terms,
    result_terms,
    premises,
    local_check,
    source_locations
)
```

Permitted early reductions include:

- literal propagation;
- immutable constant substitution;
- SSA single-use substitution under an AST-size guard;
- dead local removal after dependency closure;
- exact copy propagation between event versions, never between storage identities across time;
- infeasible branch removal with an independently checked proof;
- affine variable elimination;
- finite enum simplification.

## 8. Reachability without a generic recursive runtime proof

### 8.1 Inductive invariant

Let `I(S)` be a checked inductive over-approximation of decision-boundary reachable source states:

```text
ResetDecisionState(S) => I(S)
I(S) and E in ExecutedActionDomain
     and A in ProposalDomain
     and B in BufferDomain
     and DecisionStep(S,B,A,E) = Continue(S',V)
    => I(S')
```

Initiation and preservation are themselves counterexample queries whose `UNSAT` results are required. Preservation initially quantifies over the entire declared executed-action domain, a sound over-approximation of actions producible by the shield. It may use a smaller action set only after proving that the set still contains every executable action. Using only observed training actions would be an unsound under-approximation. Together with the progress and forward-simulation obligations, induction establishes that every reachable nonterminal decision state satisfies `I`.

The initial implementation may use a conjunction of:

- declared type domains;
- immutable parameter domains;
- finite mode domains;
- physical bounds already justified by the model/certification inputs;
- linear equalities and inequalities;
- finite transport invariants;
- phase/clock invariants.

An invariant is usable only after initiation and preservation checks discharge. Failed or unknown checks remove the proposed fact; they never add a solver premise.

`I = True` over the type-correct state domain remains a sound fallback. It can cause spurious `SAT`, but it cannot cause a false `UNSAT` proof.

### 8.2 Exact finite buffer-consistency window

At decision index `t`, define the convention exactly:

```text
B_t[p,q] = (O_t, O_{t-1}, ..., O_{t-p}, E_{t-1}, ..., E_{t-q})
```

where `E_i` is the action actually executed by the transition from `S_i` to `S_{i+1}`. Therefore `L = max(p,q)`; there is no unspecified alignment adjustment.

Construct a finite chain:

```text
S[-L] -> S[-L+1] -> ... -> S[0]
```

Constrain the current-observation field of every `B[i]` to `O(S[i])`. For each `i` from `-L` through `-1`, introduce an existential historical policy proposal `A[i]`, compute `E[i] = Execute(S[i],B[i],A[i])`, and constrain the exact `Continue` transition from `S[i]` to `S[i+1]`. Constrain `O(S[-j])` to observation lag `j` for `0 <= j <= p`, and constrain `E[-j]` to action lag `j` for `1 <= j <= q`. Each intermediate `B[i+1]` is exactly `Shift(B[i],O(S[i+1]),E[i])`; fields older than the selected candidate buffer in the initial `B[-L]` may remain existential.

The predecessor `S[-L]` is constrained by `I`, not assumed to be reset. This represents every actual older history whose last `L` decisions match the buffer, because its actual `S[t-L]`, proposals, executed actions, observations, and shifts form a witness. The construction may admit nonreachable predecessors or existential older slots, which is a sound over-approximation.

This is a sound over-approximation of steady-state buffer consistency and grows linearly with buffer length and the sliced transition size. It does not enumerate execution paths.

### 8.3 Reset and warm-up cases

Early-episode padding is not represented by an arbitrary predecessor. Generate separate cases for each decision count before the buffer becomes full:

- begin from exact reset/source initialization;
- apply the exact number of decisions;
- apply the runtime's exact zero/absent padding convention;
- include warm-up shield/action behavior and property checks.

The steady-state case and every reset-prefix case are complete obligations. All must be `UNSAT` for certification.

Formally, `J[p,q]` is the union of:

- every exact reset-prefix relation for decision counts `0 <= t < L`; and
- the steady-state finite-window relation for `t >= L` beginning in `I`.

The certificate checker proves `R_Z[p,q] subseteq J[p,q]` by induction on the decision count: reset-prefix cases cover `t < L`; for `t >= L`, the actual state at `t-L` satisfies `I`, and the actual last `L` transitions witness the steady-state window. This inclusion proof is part of the theorem, not an informal rationale.

### 8.4 Abstract SAT results

A `SAT` result obtained with an over-approximating invariant is an abstract counterexample candidate. Decode it and attempt:

1. exact formula replay;
2. reference-runtime replay when the starting state is concretely reachable;
3. bounded reachability from reset where practical;
4. invariant refinement if the candidate is spurious.

Only a validated reachable witness is reported as an actual controller counterexample. An unvalidated witness yields `UNKNOWN` with the candidate artifact preserved.

## 9. Linear and convex preprocessing

Linear and convex methods reduce the query; they do not replace the final source-bound theorem.

### 9.1 Permitted uses

- affine constant and equality elimination;
- linear observability/reconstruction analysis;
- interval and polyhedral invariant generation;
- LP feasibility checks for branch/mode pairs;
- bound tightening;
- convex separation of impossible paired states;
- source-derived periodic phase bounds;
- detection of variables that cannot influence the visible signature.

### 9.2 Proof discipline

Let `C` be the exact counterexample formula before a transformation and `C'` the formula submitted after it. A transformation used for an `UNSAT` proof must establish one of:

```text
C <=> C'                         # exact equivalence
models(C) subseteq models(C')    # counterexample over-approximation
```

Consequently, `UNSAT(C') => UNSAT(C)`. The reverse inclusion is not sufficient. An under-approximation may be used only to search for concrete witnesses and can never support certification.

Every result used to remove behavior must be checkable independently:

- exact rational LP where possible;
- verified primal/dual certificates or Farkas witnesses for infeasibility;
- exact substitution replay;
- conservative outward rounding for floating-point interval abstractions;
- an exact implication/equivalence query against the unreduced local formula for any reduction that lacks a compact independently checkable certificate.

Mathematical-real reasoning is not automatically sound for binary floating point. A real or convex abstraction may be used for pruning only when a proved concretization relation includes every relevant IEEE-754 result, rounding error, signed zero, infinity, and NaN behavior. Otherwise it is heuristic guidance and cannot remove a path or premise. Algebraic identities such as associativity, distributivity, cancellation, and `x - x = 0` must not be applied to Float32/Float64 without a valid IEEE-754 side-condition proof.

### 9.3 Structural numeric abstraction

When the theorem depends only on congruence, a deterministic numeric operation may be represented by a shared uninterpreted function of the same operand sorts, result sort, operation identity, rounding mode, and exceptional semantics. Equal concrete inputs therefore map to equal abstract outputs, while the uninterpreted function admits additional mappings. `UNSAT` of this over-approximation is sound. `SAT` must be retried with exact arithmetic before it can support a counterexample. Distinct operations, arities, rounding modes, or semantic contexts must use distinct function symbols unless their equality is separately proved.

## 10. Z3 encoding

### 10.1 Formula form

The production proof uses quantifier-free existential counterexample formulas. It does not use Z3 Fixedpoint/Spacer for the supported finite profile.

The formula contains:

- two independently renamed finite history windows;
- a shared candidate buffer equality;
- a shared policy proposal;
- exact shield equations;
- one finite next-decision transition for each side;
- one visible-difference obligation.

Use one shared set of symbolic buffer slots and one shared current policy proposal. Each execution copy constrains its independently renamed source states and transitions to those shared observations and executed-action slots. This is equivalent to introducing two buffers plus equality constraints, while avoiding duplicated buffer variables and large structural equalities.

The transition is represented as shared SSA definitions and guarded mode selectors. Do not recursively substitute definitions into exponential expression trees. Preserve DAG sharing with named terms or `let`-equivalent constraints.

### 10.2 Split complete obligations

Use separate queries with the same checked transition base for:

1. shield/executed-action disagreement;
2. each next observation component or small related group;
3. each next action-buffer component when not syntactically identical;
4. reward disagreement;
5. elapsed-time/discount disagreement;
6. next action-availability/mask disagreement;
7. outcome-constructor or intrinsic-termination disagreement;
8. source error disagreement;
9. each property status/error group;
10. successor/coverage/progress failures;
11. representation or unsupported-value obligations.

The union of these difference conditions must equal the complete `VisibleDifference` predicate. Every query must return `UNSAT`. Splitting is an optimization, not weakening.

### 10.3 Equality semantics

- Boolean, integer, enum, and bit-vector values use native equality.
- Controller Float32 buffer equality uses exact IEEE bit equality, including signed zero. NaNs must either be prohibited by a proved admissibility obligation or compared according to exact runtime byte/value behavior.
- Floating-point reward, duration, or other visible outputs use the exact representation/equality observed by the consumer; they are not compared as mathematical reals.
- Binary64 state equality is required only where the theorem uses equality; hidden states are intentionally allowed to differ.
- Optional values compare presence first and payload only when present, unless the runtime makes absent payload bits observable.
- Property status compares success/failure and error identity required by the source contract.
- Outcome equality is constructor-sensitive: `Continue`, `Terminal`, and `Error` are never equal across constructors.

### 10.4 Modes and branches

Prefer a small finite mode variable and guarded equations over nested, expanded `If` expressions. For a small number of scheduler modes, enumerate mode pairs outside Z3 and prune infeasible pairs with checked linear constraints. Never enumerate complete source paths when SSA joins suffice.

### 10.5 Solver results

- `unsat`: obligation discharged;
- `sat`: decode candidate, validate as described above;
- `unknown`: no proof;
- timeout, crash, signal, memory cap, malformed model, or unsupported result: no proof.

Each query runs in an isolated process with a wall-clock deadline and memory limit. Parent-process completion is not solver success.

Timeout enforcement must terminate the solver process and classify the obligation as `UNKNOWN`. The query text/hash and all premises must be finalized before launch; a timeout must not leave behind a partial formula that a caller can mistake for a completed obligation.

The certificate records Z3 as part of the TCB. When practical, export canonical SMT-LIB and independently rerun it with a second implementation or check a solver proof artifact. Such corroboration is valuable but does not repair an incorrect source encoding; source-to-IR forward simulation and reachability inclusion remain mandatory.

## 11. Reward, termination, truncation, and shield semantics

### 11.1 Reward

The proof must use the exact reward returned by the environment, including accumulation order and any dependence on properties, completion, actuator behavior, elapsed time, or source errors. A reward component not present in the SysML model still belongs in the interface proof if training observes it. If the learner uses duration-dependent discounting, the transition duration or resulting discount is also theorem-visible.

### 11.2 Intrinsic termination versus time-limit truncation

Intrinsic completion belongs to the Markov theorem and must be determined by the candidate buffer and action.

An external training horizon is an external time-limit wrapper, not an intrinsic plant transition. Choose one explicit contract:

- exclude `truncated` from the plant MDP theorem and record it as an external wrapper; or
- include remaining horizon/step count in the controller-visible state.

Do not quietly add hidden `env.step_count` to the proof state while claiming that the policy's unaugmented finite buffer is Markov. If step count affects reward, shield, observation, or intrinsic termination, it must be reconstructed from or included in the policy input.

If `truncated` is excluded, the certificate states that the theorem concerns the underlying time-homogeneous plant/controller process. Training code must treat the horizon as an external truncation contract and must not reinterpret it as an intrinsic terminal reward transition. If training behavior depends on remaining horizon, remaining horizon is part of the controller state and must be represented in the buffer/interface theorem.

### 11.3 Shield

The policy proposes `A`; the environment executes `E = Execute(S,B,A)` using the actual extracted shield/environment semantics. Executed-action equality is part of the relational query. A separate noninterference obligation may prove `Execute(S,B,A) = Shield(B,A)` for all admissible `S`, permitting the state-independent simplification. If hidden source state can change the executed action for equal `B,A`, the query must produce a counterexample rather than assuming that dependency away.

`Execute` must be deterministic and total on every legal proposal in the initial profile. If shield evaluation can fail, that failure is an explicit `Error` outcome compared by the theorem; it cannot be represented by an unconstrained action or a missing transition.

Action-history slots contain executed actions after shield override, matching the runtime and reduced-MDP contract.

## 12. Complexity controls

The encoding must have a predictable structural bound:

```text
O(2 * (L + 1) * N * size(sliced simulator-step relation))
```

where `L = max(b_obs,b_act)`, `N` is the proved maximum simulator steps to the first next outcome, and `2` is the relational pair. Shared subterms may reduce the constant. The encoding must not scale with the number of complete paths through the source program.

Before invoking Z3, record:

- retained persistent locations by role and sort;
- removed locations and reduction reasons;
- SSA equations and guards;
- finite scheduler modes and mode pairs;
- history-window length;
- Boolean, integer, FP32, FP64, enum, and presence variables;
- AST/DAG node count after sharing;
- maximum `ite` depth;
- nonlinear operations and polynomial degree where meaningful;
- arrays, datatypes, quantifiers, and recursive relations—expected to be zero except explicitly justified finite encodings;
- estimated serialized query size.

Use configurable pre-solver budgets. Initial development budgets should be intentionally conservative and calibrated from the small fixtures before production models. Crossing a budget returns `query_complexity_budget_exceeded` with the inventory; it does not launch an uncontrolled solver job.

Absolute budget values are operational settings, not proof assumptions. Raising a budget cannot change the theorem, only whether a query is attempted.

Performance acceptance requires demonstrating linear growth with buffer length and sliced model size on controlled fixtures. A smaller certificate file or faster Python compilation is not evidence that the Z3 problem itself is smaller.

## 13. Certificate contents

### 13.1 Mandatory soundness obligations

The certificate schema contains an explicit, closed inventory. Unknown obligation names are rejected, and no required entry may be omitted.

| ID | Obligation | Required evidence |
| --- | --- | --- |
| O1 | Source/profile completeness | Every source construct and runtime hook affecting the decision transition is supported or the model is rejected |
| O2 | Storage separation | Distinct physical, held, sent, received, actuator, mode, clock, and validity locations with checked copy/hold writers |
| O3 | Source-to-IR forward simulation | Checked local semantic translations plus complete scheduler/event composition |
| O4 | First-outcome progress and totality | No missing outcome by bound; each encoded trace has exactly one first `Continue`/`Terminal`/`Error` constructor; all admitted traces remain in later queries |
| O5 | Invariant initiation | `ResetDecisionState and not I` is `UNSAT` |
| O6 | Invariant preservation | `I and admissible transition to S' and not I(S')` is `UNSAT` |
| O7 | Buffer-relation coverage | Checked induction establishing `R_Z[p,q] subseteq J[p,q]` |
| O8 | Reduction soundness | Equivalence or concrete-counterexample inclusion for every reduction |
| O9 | Visible-signature completeness | Observation, executed action, reward, time/discount, action availability, outcome, terminal/error, and property inventory matches the interface contract |
| O10 | Difference partition completeness | Checked identity between `OutcomeDifference` and the disjunction of split query predicates |
| O11 | Reset-prefix Markov obligations | Every required prefix query is `UNSAT` |
| O12 | Steady-state Markov obligations | Every required finite-window query is `UNSAT` |
| O13 | Independent regeneration/replay | Checker reconstructs identities, IR, reductions, formulas, and required `UNSAT` results |

If a model needs no nontrivial invariant, O5/O6 still record the checked type-domain invariant or explicit `True` invariant. If a visible component is absent by contract, O9 records why; it is not silently omitted.

A successful certificate records:

- source model hash and path-independent identity;
- parser, runtime scheduler, observation encoder, shield, reward, and extractor identities;
- `dt` and all source/runtime settings affecting semantics;
- supported-profile declaration;
- exact buffer layout and reset padding;
- every retained `StorageId`, role, native sort, source declaration, readers, and writers;
- explicit physical/sensor-held/sent/received separation records;
- validated `MarkovIR` identity;
- source-to-IR validation report;
- relevance seeds and complete dependency/reduction records;
- invariant facts and initiation/preservation evidence;
- finite decision-interval proof and scheduler modes;
- reset-prefix and steady-state obligation inventory;
- exact Z3 version, options, per-query hash, result, time, and peak memory;
- complexity inventory and budgets;
- SAT-candidate validation status where applicable;
- final theorem statement and scope.

The checker must:

1. reject structurally unsuccessful certificates before expensive work;
2. reparse source and recompute semantic identities;
3. independently validate the IR and reductions;
4. reconstruct every required query;
5. verify query hashes and rerun the UNSAT checks;
6. reject missing, additional, timed-out, unknown, or mismatched obligations.

Caching may reuse immutable extracted equations under complete content keys. It must not cache or trust an acceptance verdict.

## 14. Proposed implementation boundaries

Create a new package rather than extending the current `lazy_*` modules:

| Module | Responsibility |
| --- | --- |
| `markov_ir.py` | Immutable typed `StorageId`, event SSA, modes, outputs, and serialization |
| `markov_extract.py` | Source/runtime-profile extraction into unsliced `MarkovIR` |
| `markov_validate.py` | Independent coverage, order, storage, copy/hold, and boundary validator |
| `markov_slice.py` | Complete data/control/scheduler dependency closure and reduction records |
| `markov_interval.py` | Finite next-decision bound and scheduler-phase derivation |
| `markov_invariants.py` | Candidate invariant generation plus initiation/preservation checking |
| `markov_linear.py` | Exact affine/LP/convex preprocessing and replayable certificates |
| `markov_history.py` | Exact buffer layout, reset prefixes, and steady-state finite windows |
| `markov_z3.py` | Native-sort translation, shared base formula, and split obligations |
| `markov_witness.py` | Model decoding, exact formula replay, runtime replay, refinement classification |
| `markov_certificate.py` | Evidence schema, generation, checking, and content-addressed query artifacts |
| `markov_metrics.py` | Formula inventory, scaling measurements, budgets, and resource reports |

Integration point:

```python
prove_buffer_markov(
    model_path,
    dt,
    b_obs,
    b_act,
    proof_profile,
    resource_limits,
) -> BufferMarkovResult
```

`select_source_buffer` may retain its lexicographic candidate loop but must consume `prove_buffer_markov` directly. It must not require a separate generic transition-closure proof followed by full-state reconstruction. A smaller candidate returning `UNKNOWN` does not establish that the first later proof is mathematically minimal.

The existing `source_solver`, `source_history`, and `lazy_*` implementation remains frozen for regression comparison until the new proof passes independent validation. It is not a production fallback.

## 15. Validation strategy

### 15.1 Theorem fixtures

Required small fixtures include:

1. **Direct observation:** current observation alone is Markov; expect `UNSAT`.
2. **Delayed Boolean:** current observation is insufficient but one prior executed action is sufficient; expect `SAT` then `UNSAT`.
3. **Physical/held divergence:** same held reading, different physical state, different next reading; expect a counterexample for an insufficient buffer.
4. **Sample-and-hold sufficiency:** enough observation/action history determines the hidden physical/held pair; expect `UNSAT`.
5. **Sent-copy stability:** physical and sensor values change after send; received payload retains the sent version.
6. **Received-hold stability:** sender changes after delivery; controller-held value remains unchanged until the next accept.
7. **Phase dependence:** same numeric observation with different scheduler phase changes the next observation; expect `SAT` unless phase is reconstructible/included.
8. **Reset padding:** distinguish every partially filled buffer case.
9. **Shield override:** history records the executed action, and equal buffers/proposals produce equal shield outcomes.
10. **Intrinsic terminal:** hidden completion state affecting `terminated` produces `SAT` unless represented.
11. **Time-limit truncation:** verify the explicit wrapper contract; never silently certify hidden step count.
12. **Float encoding:** Float32 rounding, signed zero, infinities, and prohibited/handled NaNs match runtime behavior.
13. **Variable decision duration:** equal buffers with different elapsed ticks produce `SAT` when duration/discount is visible.
14. **Dynamic action availability:** equal buffers cannot hide different legal-action sets.
15. **Blocked transition:** a possible accept/block or divergent interval fails progress rather than disappearing from the query.
16. **First-boundary behavior:** later decisions cannot be selected while an earlier decision/terminal/error boundary is skipped.
17. **Scenario uncertainty:** differing reset parameters are allowed on the two sides and expose non-Markov behavior when relevant.
18. **Unsupported stochastic step:** per-step randomness is rejected by the deterministic profile.

### 15.2 Mutation tests

The certificate/checker must reject mutations that:

- merge physical and sampled `StorageId`s;
- replace a held read with current physical state;
- turn a send/accept copy into a live alias;
- reorder sample, plant update, send, delivery, or decision events;
- omit a writer, branch guard, mode, property, reward term, or terminal term;
- record proposed rather than executed actions;
- change a buffer lag, padding element, normalization scale, or Float32 conversion;
- remove an invariant initiation/preservation check;
- remove a visible-difference obligation;
- accept `UNKNOWN`, timeout, or an unvalidated SAT candidate;
- change source/runtime code without invalidating the certificate.
- omit a progress/totality case, elapsed duration, action mask, or outcome constructor;
- reverse an abstraction implication so that a proof query becomes an under-approximation;
- use mathematical-real algebra to eliminate an IEEE-754 behavior without proved side conditions;
- claim simulator equivalence from differential tests alone.

### 15.3 Differential runtime validation

For generated concrete states within the supported profile:

- execute one decision interval in the reference runtime;
- execute the same interval in `MarkovIR`;
- compare every retained state version and visible output exactly;
- emphasize boundary values, branch thresholds, mode changes, message timing, and floating-point special values.

Differential tests support the translation validator; they do not replace proof of complete source-event coverage.

### 15.4 Production-model order

1. Thermostat: smallest end-to-end decision transition.
2. Cruise: multiple physical observations and two actuators.
3. Mixing: periodic scan phase, synchronous request/response chains, and larger action composition.

Do not proceed to the next production model until the preceding model has:

- source-to-IR validation;
- expected SAT negative-buffer fixtures;
- expected UNSAT sufficient-buffer result or a precisely classified blocker;
- independent certificate replay;
- recorded formula/resource metrics.

## 16. Implementation phases and gates

### Phase 0: preserve and baseline

- Create a new implementation branch from the handoff branch.
- Record current source, tests, models, Z3 version, and failed-query metrics.
- Freeze the current lazy verifier for comparison.

Gate: reproducible baseline and clean working tree.

### Phase 1: theorem and interface contract

- Implement data classes for the theorem-visible interface.
- Write the executable/formal small-step rules for the thermostat-supported subset and state the representation relation `rho`.
- Resolve reward, intrinsic termination, time-limit truncation, shield proposal/execution, and property-boundary contracts.
- Add theorem fixtures before extraction code.

Gate: reviewed theorem, operational semantics, TCB, and executable interface tests with no SysML lowering.

### Phase 2: typed source extraction

- Implement `StorageId`, roles, native sorts, event SSA, and decision boundaries.
- Support the thermostat operation subset.
- Implement the independent validator simultaneously.

Gate: exact physical/held/sent/received separation, full source-event coverage, and checked local forward-simulation obligations.

### Phase 3: finite transition and slicing

- Compose the finite next-decision transition.
- Implement complete relevance closure and replayable reductions.
- Emit formula metrics before Z3 integration.

Gate: thermostat slice contains no generic values, heaps, arrays, recursion, or unrelated runtime objects.

### Phase 4: direct two-execution Z3 proof

- Implement native-sort translation and split counterexample obligations.
- Start with exact reset-prefix cases, then steady-state invariant windows.
- Decode and replay SAT candidates.

Gate: expected SAT and UNSAT results on all theorem fixtures.

### Phase 5: invariants and linear/convex reduction

- Add checked linear invariants, affine elimination, LP pruning, and observability helpers only where metrics show need.
- Compare formulas before and after each optimization.

Gate: every behavior-removing optimization has replayable evidence and a mutation test.

### Phase 6: production models

- Thermostat, then cruise, then mixing.
- Derive mixing's finite scan phase rather than introducing recursive reachability.
- Use workstation jobs only after VM-scale compilation and fixture gates pass.

Gate: controlled solver runs remain within formula budgets and produce independently replayable results.

### Phase 7: certificate and pipeline integration

- Add the new evidence schema and checker.
- Connect buffer selection directly to the new proof.
- Preserve Stage 3 fail-closed behavior.
- Do not enable later stages until independent checking passes.

Gate: corrupted, stale, partial, unknown, or resource-failed evidence cannot produce a passing certificate.

## 17. Resource workflow

Use the autonomous VM for:

- design and implementation;
- source/IR inspection;
- extraction and translation validation;
- small Z3 fixtures once a compatible Z3 wheel is available;
- formula metrics and serialization;
- unit and mutation tests.

Use the WSL workstation for:

- full production-model Z3 queries;
- controlled parallel obligation runs;
- high-memory comparisons;
- complete pipeline and independent checker runs;
- later PyTorch/GPU training.

Every workstation job must run from a pushed, immutable commit and write a structured result containing the commit, command, environment, solver identity, limits, exit status, duration, peak memory, query hashes, and proof results.

## 18. Acceptance criteria

The new implementation is acceptable only if:

1. Physical, sensor-held, sent, and received values are distinct typed storage in extraction, transition, query, evidence, and checker.
2. The exact controller buffer layout, Float32 normalization, padding, and executed-action convention match the runtime.
3. The theorem is a direct two-execution counterexample proof, not full internal-state reconstruction unless reconstruction is locally useful.
4. A formal operational semantics exists for every supported operation, and checked forward simulation establishes that IR behaviors over-approximate all source behaviors.
5. The source transition is finite, first-outcome preserving, total, validated, backward-sliced, and quantifier-free for the supported profile.
6. Action availability, shield execution, reward, elapsed time/discount, intrinsic termination, errors, and required property outcomes are complete.
7. `R_Z[p,q] subseteq J[p,q]` is established by checked invariant initiation/preservation and the reset-prefix/steady-window induction.
8. Every removed operation or state component has checked equivalence, non-influence, or counterexample-over-approximation evidence in the correct direction.
9. All progress, totality, reset-prefix, steady-state, and complete visible-output obligations return `UNSAT`.
10. `SAT` witnesses are clearly classified as reachable or abstract; abstract candidates never become asserted counterexamples.
11. `UNKNOWN`, timeout, crash, memory failure, unsupported constructs, missing obligations, or failed validation cannot certify.
12. Independent certificate checking re-extracts and reruns the proof.
13. Query metrics demonstrate structural scaling rather than path explosion, with no generic tagged scalar state or recursive Horn reachability.
14. Insufficient-buffer fixtures reliably produce counterexamples and sufficient-buffer fixtures prove `UNSAT`.
15. The final claim names its formal semantics and TCB and does not claim executable-runtime equivalence without a checked refinement or shared kernel.
16. Production results explicitly state what was and was not certified; component success is never presented as end-to-end success.

## 19. First implementation decision

Before writing the extractor, inspect and freeze the exact controller-step interface for thermostat:

- source event order from one installed controller response to the next request;
- exact observation fields and normalization;
- proposed versus executed action behavior;
- reward calculation;
- intrinsic termination versus time-limit truncation;
- property boundaries;
- physical, held, sent, received, actuator, mode, and clock storage.

That inventory becomes the first `MarkovIR` golden artifact and the first independent-validator fixture. No generic feature should enter the implementation until a supported model or theorem obligation requires it.
