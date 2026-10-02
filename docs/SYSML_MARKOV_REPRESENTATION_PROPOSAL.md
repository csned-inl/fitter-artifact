THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

# Review draft: thermostat-specific symbolic representation for Markov certification

Status: **active, user-selected prototype plan (2026-10-02).** The earlier
reset-witness plan is frozen. The only authorized implementation endpoint is
one thermostat-specific, solver-native relation object, one fixed
`(b_obs=2, b_act=1)` paired Markov query, and focused tests. It may read the
existing SysML parser to reject source drift, but must not import or extend the
legacy verifier, simulator, generic IR, generic SysML compiler, property
engine, invariant synthesizer, or workstation loop. If the needed source
semantics cannot be represented under this boundary, production stops and the
user decides the next step.

## 1. Representation problem and exact scope

The mathematical specification in the SysML model is the primitive. The required representation must preserve the thermostat model's equations, inequalities, finite state, decision boundary, and query-relevant property roles without compiling or symbolically replaying a simulator.

This proposal is a mathematical theorem schema for one deterministic thermostat query. It is not authorization for reusable IR classes, compiler passes, a general property engine, generic event infrastructure, arbitrary SysML support, fitting, invariant generation, or another simulator.

## 2. Proposed symbolic object

For one approved thermostat query, represent:

```text
M_Q = (G, X, A, D, I, E_Q, Step_Q, O_Q, Phi_Q)
```

where:

- `G` contains only contract-fixed values shared by every trace and reset of one certified MDP instance;
- `X` is the complete query-relevant model state, including every scenario value that can differ between allowed initializations or resets;
- `A` is the proposed controller-action alphabet;
- `D(G,X)` is the source-derived admissible-state/domain predicate;
- `I(G,X)` is the initial-state relation, proved nonempty for every certified fixed value `g`;
- `E_Q(G,Z,A)` is the lifted enabled/contract-admissible proposed-action predicate;
- `Step_Q(G,Z,A)` is a total, single-valued tagged decision-boundary outcome;
- `O_Q` is the exact query-declared observation function at the approved decision epoch;
- `Phi_Q` contains typed source property predicates and their declared roles, but is not a general property-verification subsystem.

`Z`, defined below, is the lifted state containing `X` and exact query memory.

The theorem is a family indexed by a fixed `g`:

```text
for every g in Dom(G), the process that keeps g fixed for its entire trace
and reset regime satisfies the proved result.
```

A setpoint, outside temperature, or other scenario value selectable at initialization or reset is not in `G`; it is persistent state in `X` with explicit initialization/reset semantics. It is therefore compared across the two Markov histories and cannot remain hidden by sharing one symbolic parameter.

## 3. Tagged decision-boundary outcomes

`Step_Q` does not invent a successor after termination. Its codomain is the exact finite set of query-approved outcome tags, for example:

```text
Continue(next_Z, returned_fields)
Stop(returned_fields)
Reset(returned_fields, reset_Z)
Error(returned_fields)
```

The exact tags and fields are fixed by `Q`; unused tags do not exist. A reset is a transition, not an initializer, unless the approved source/query semantics prove it is exactly a context-free return to `I`.

`Result_Q(G,Z,A)` is the complete tagged projection used by the Markov proof. It contains every controller-boundary field returned by `Q`. For every outcome that continues the same certified process, including both `Continue` and a continuing `Reset`, it also contains `beta_Q(successor_Z)` even when the external API does not immediately expose that successor. For a genuinely trace-ending `Stop` or `Error`, it contains no invented `beta(next)` value.

If reset ends the certified trace and begins a separately initialized episode, it is represented as `Stop` for this theorem and the new episode is governed by `I`. It is not treated as a continuing `Reset` with an omitted successor.

The query must state whether terminal output observes the pre-reset state, the terminal post-step state, an absorbing state, or an explicit reset successor.

## 4. Exact source-role classification

Every retained source construct is assigned one exact role under the approved thermostat semantic profile:

- initialization/instance creation;
- persistent state or fixed contract value;
- admissible source domain in `D`;
- guarded step/update behavior;
- observation behavior;
- proposed-action availability or explicit contract assumption;
- returned outcome field;
- monitored property metadata;
- proved irrelevant;
- unsupported.

Requirements and properties never enter `D`, `I`, `E_Q`, or `Step_Q` merely because they occur in the source. Their operational role must be explicit and must not override source-fixed behavior.

## 5. Corrected source-to-representation rules

The first compiler uses only thermostat-specific syntax-directed rules approved in the binding plan:

| Source construct | Required treatment |
| --- | --- |
| Attribute initializer | Contributes to instance creation or initialization only according to approved redefinition, variability, and reset semantics |
| Scenario input or apparently static attribute | Enters `G` only if proved fixed across every trace/reset; otherwise it is state in `X` with explicit initial/reset rules |
| Source constraint | Enters `D`, an update relation, or property metadata according to its exact declared semantics; never classified by syntax alone |
| Assignment | Guarded update only when its containing action executes, with exact source read/write ordering and an explicit frame rule for unassigned state |
| Conditional assignment | Priority-preserving guarded expression; overlapping and equality boundaries remain explicit |
| Value binding | May be eliminated as an alias only after proving type, direction, identity irrelevance, and timing preservation |
| `flow`, `connect`, `send`, or `accept` | Never assumed to be algebraic equality; use the approved thermostat synchronous-transfer rule or represent required transfer/consumption state explicitly |
| Action invocation | Exact input/output parameter binding, invocation guard, and return/effect semantics |
| State-machine declaration | Finite state in `X`, including entry initialization used by the thermostat |
| State transition | Exact source state, trigger/event consumption, guard, selection/priority, effect, and target state |
| Sensor relation | Exact observation/update equation at its approved timing, not an assumed instantaneous alias |
| Completion expression | Typed predicate entering `Result_Q` only when selected by `Q` under an approved completion/terminal rule |
| Prohibition, obligation, or neural requirement | Typed predicate in `Phi_Q`; operational only through an explicit role described in Section 8 |
| Clock/timing declaration | Exact state and update when it can affect decision epochs or results |

Unsupported entry/exit/do behavior, transition priority, event delivery, repeated-command behavior, or invocation semantics required by the query causes `UNSUPPORTED`. The compiler cannot borrow a simulator convention.

## 6. Component expression DAG, not global mode enumeration

Each query-relevant next-state or output component is represented by a hash-consed expression DAG:

```text
x_i' = Expr_i(G, X, A)
```

The DAG may contain:

- exact arithmetic nodes under the approved numeric theory;
- source-declared finite-mode tests;
- interned comparison atoms;
- priority-preserving `ite` nodes;
- exact Boolean connectives;
- explicit casts and terminal/event branches.

The compiler must not synthesize a global mode from the Cartesian product of unrelated guards, enumerate guard truth assignments, distribute Boolean formulas, or duplicate common predicates into every case. Source-declared modes remain separate finite variables.

Interned atoms and common subexpressions remain shared through solver lowering using shared definitions or `let` bindings. The binding plan must limit both unique DAG size and serialized/expanded formula size so sharing cannot conceal later blow-up.

## 7. Affine recognition is an exact optional annotation

An expression DAG node or guarded branch may be annotated as affine only under exact real/rational semantics and only when it has the form:

```text
c + sum_i(k_i * v_i)
```

where every `k_i` and `c` is a fixed numeric constant in the selected arithmetic theory. A reset-selectable or symbolic scenario value is a variable, not a coefficient constant. `P*X`, `X/P`, or any product of two varying terms is nonlinear. Division additionally requires a fixed proved-nonzero numeric divisor.

IEEE floating-point addition, multiplication, division, rounding, conversion, overflow, NaN, infinities, and signed zero are not normalized as real-affine operations. Floating-point expressions remain exact floating-point DAGs without reassociation unless a separately checked exact equivalence permits a rewrite.

Strict and non-strict thresholds, saturation, `min`/`max`, casts, equality boundaries, overlapping conditions, event priority, and terminal/reset branches remain exact guarded operations. They are not absorbed into an affine matrix or merged without an exact boundary-preserving proof.

For the first thermostat query, any required nonlinear or unsupported numeric expression produces `UNSUPPORTED`; no fitting, approximation, or sampling substitute is allowed.

## 8. Property predicates and non-circular roles

A prohibition, obligation, or neural requirement defaults to a monitored typed predicate in `Phi_Q`. It does not restrict reachability and does not become a shield or action rule by itself.

It may affect the Markov theorem only when the approved source semantics are constructive or the exact query explicitly adds one of these roles:

1. **Action-contract assumption:** the predicate contributes to `E_Q`. The result is explicitly reported as conditional on that assumption; it does not claim to prove the assumed property.
2. **Specified shield mechanism:** a separately defined, total, deterministic proposed-to-executed action function contributes to `Step_Q`. A predicate alone cannot synthesize or choose a shield action.
3. **Returned property label:** its truth value is explicitly named in `Result_Q`.

If the same predicate is both monitored and assumed, the audit record exposes that dependency and cannot claim that the assumption was independently proved.

Properties unrelated to `E_Q`, `Step_Q`, `Result_Q`, `D`, or `I` remain typed source-ledger metadata and do not enter Z3. Independent safety verification requires a separate user-approved plan. Temporal obligations requiring monitor history are `UNSUPPORTED` in this scope.

## 9. Totality and deterministic step validation

For every fixed `g` in `Dom(G)`, lifted state `z` in the approved domain, and action `a` with `E_Q(g,z,a)`, the representation must establish:

- exactly one tagged `Step_Q(g,z,a)` outcome exists;
- every required source update and frame value is defined;
- the exact returned fields are defined for that outcome tag;
- event delivery, action invocation, transition selection, timing, and update ordering needed by the query are resolved;
- no conflicting constraint or update produces a second outcome;
- a terminal or error outcome is represented explicitly rather than treated as missing behavior.

Partial, conflicting, multivalued, deadlocked, or unresolved behavior is `UNSUPPORTED` for the deterministic scope.

## 10. Lifted buffered state

The buffer is explicit memory. Define:

```text
Z = (X, M)
beta_Q(Z) = B
```

where `M` contains exactly the query-defined observation/action history and initialization. Any query-relevant delay, queue, clock phase, reset phase, or event memory belongs in `X` or `M`.

For a continuing or explicit-reset outcome, the buffer successor is defined under one approved ordering from the exact source observation, proposed/executed action fields selected by `Q`, and returned labels. A trace-ending `Stop`/`Error` outcome has no buffer successor unless `Q` explicitly defines one.

Action availability at the lifted boundary is represented by one predicate:

```text
E_Q(G, Z, A)
```

so availability may soundly depend on phase or other query-visible memory when the approved semantics require it.

## 11. Reachable proof domain

For every certified fixed `g`, the lifted initialization must satisfy:

```text
exists z: I_Q(g,z)
```

including a valid query-buffer initialization. Equivalently, the certified `Dom(G)` may be restricted to exactly those `g` for which this existence obligation is proved.

For every such `g`, `R_Q(g,z)` must:

1. contain every allowed lifted initialization/reset state for that `g`;
2. be nonempty as a consequence of containing that lifted initialization;
3. satisfy the source domain `D` on its model-state projection;
4. be inductive and transition-closed for every continuing/reset successor of every enabled proposed action.

For the first thermostat plan, `R_Q` is only the declared admissible lifted domain or one fixed model-specific invariant written into and approved with the binding plan. Failure produces `NO_RESULT`. It does not authorize invariant synthesis, refinement, iterative strengthening, or a search for another invariant.

## 12. Exact deterministic Markov counterexample formula

For the family of fixed-`g` MDPs, the residual counterexample formula is:

```text
exists g, z1, z2, a:
    Dom(G)(g)
    and R_Q(g, z1)
    and R_Q(g, z2)
    and beta_Q(z1) = beta_Q(z2)
    and (
        E_Q(g, z1, a) xor E_Q(g, z2, a)
        or (
            E_Q(g, z1, a)
            and E_Q(g, z2, a)
            and Result_Q(g, z1, a) != Result_Q(g, z2, a)
        )
    ).
```

All reset-selectable scenario values are inside `z1` and `z2`, so this formula does not incorrectly force them equal. A value may remain shared in `g` only when the implementation contract fixes it identically throughout the entire certified MDP instance.

`UNSAT` proves deterministic strong lumpability of the lifted model under `beta_Q` for every certified `g`, every allowed initialization/reset state, and every policy over enabled proposed actions. A stationary result additionally requires time/phase to be in `beta_Q` or proved irrelevant.

`SAT` is `DISPROVED_FOR_QUERY` only when both sides correspond to source-valid reachable histories from approved initialization/reset. A pair found only in an overapproximation produces `NO_RESULT`.

## 13. Minimality and anti-growth enforcement

- Dependency slicing begins only from `D`, `I`, `E_Q`, `Step_Q`, `Result_Q`, `beta_Q`, buffer recurrence, and decision timing required by the exact query.
- The source-coverage ledger and unrelated `Phi_Q` predicates remain outside Z3.
- Only retained component DAGs and unresolved compact obligations enter Z3.
- Every solver variable and assertion has a model/query-specific necessity entry.
- The first implementation may contain only thermostat-specific extraction required by the approved query; no reusable compiler, IR, property, event, or representation framework is authorized.
- The binding plan must set limits on retained variables, expression nodes, shared atoms, solver queries, assertions, unique AST/DAG size, serialized and expanded formula size, solver time, runtime, and files changed.
- Exceeding any limit produces `NO_RESULT` and immediate stop. It does not authorize query splitting, mode expansion, formula enlargement, timeout increases, or another plan without returning to the user.

Always forbidden:

- simulator control-flow encoding;
- AST/control/event/scheduler graph encoding;
- guard Cartesian products or truth-table enumeration;
- path enumeration;
- sampling, fitting, or approximation;
- generic invariant generation;
- one-timeout-at-a-time patching;
- building another SMT solver.

## 14. Review questions

1. Does `(G,X,A,D,I,E_Q,Step_Q,O_Q,Phi_Q)` capture exactly the deterministic thermostat information needed by a buffered Markov theorem?
2. Are reset-selectable parameters, initialization, reset, terminal outcomes, and action availability represented without hidden-history mistakes?
3. Do the source rules preserve action invocation, event transfer, state-machine, binding, flow, and update semantics without reconstructing a simulator?
4. Do component expression DAGs and exact optional affine annotations avoid both unsound normalization and global mode explosion?
5. Are properties represented without being silently assumed, proved circularly, or turned into arbitrary shield behavior?
6. Does the explicit two-copy formula prove the intended fixed-parameter-family controlled Markov result?
7. Is every remaining route to generic framework growth or solver blow-up closed?

Until all reviews pass and the user approves a new binding plan, this proposal remains non-authorizing.
