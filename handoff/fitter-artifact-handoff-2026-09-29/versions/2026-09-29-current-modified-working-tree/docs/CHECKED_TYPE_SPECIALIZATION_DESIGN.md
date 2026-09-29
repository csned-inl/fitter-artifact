# Checked type specialization: design and implementation plan

Status: design only. No implementation or new test execution is authorized by this document.
Baseline: `36f597e79f4f7e389ef47133126cb06b4cc2609d`.

## 1. Objective and scope

Reduce the Boolean and arithmetic expansion caused by generic tagged values in the existing source-transition proof. Carry established type information into operation lowering and, where justified, the recursive relation's storage signature. Preserve the exact transition and requirement meanings already being checked.

This is a compiler optimization with correspondence evidence, not a new theorem about the type correctness of arbitrary SysML programs. It analyzes the execution graph already used by the certificate. It adds no broader relevance analysis, new controller behavior, or new safety requirements. The SysML models, parser, initialization interpretation, simulator, shields, checking times, physical/delayed separation, and experimental settings remain unchanged. It does not equate a finite observation buffer with a bound on internal execution steps.

The MDP transition checker is currently the expensive consumer. Specialization must preserve the states and properties passed onward to the existing discretization checker. It does not replace the continuous-interval proof or discharge the separate, unresolved history-reconstruction and progress obligations.

## 2. What the current code actually does

`lazy_expressions.Equations.bind` already retains an explicit constructor such as `Float(x)` when local simplification establishes it. That useful behavior should be extended, not replaced.

The main information loss occurs at graph boundaries: `lazy_scalar_store.StoreEquations.symbolic` and `lazy_program.NodeCompiler.symbolic` create unconstrained tagged values. `lazy_graph.compile_graph` lowers nodes separately. The recursive solver then uses a shared state datatype whose scalar value fields and message payload arrays still contain the full tagged `Value` type.

`lazy_specialization.check_specialization` is a small existing prototype. Its acceptance condition proves constructor preservation on successful transitions; its execution-error result is separate. It cannot be used as a whole-node optimization certificate without addressing failure paths and partial updates. No production compiler currently calls it.

Three different notions must remain distinct:

- **Declared type:** the SysML declaration and its source provenance.
- **Runtime value type:** Absent, Boolean, Integer, Float64, or Text under the existing evaluator. A Real declaration alone does not establish that every actual stored value uses the Float constructor.
- **Storage mode:** `Cell.kind` distinguishes scalar storage, stored alias, and live binding. It is not a numeric type tag.

The optimizer must preserve all three meanings rather than reuse one field for another.

## 3. Type evidence and finite propagation

Introduce a type-fact record indexed by source node, storage identity or expression version, and evaluation context. It contains possible runtime constructors, presence information, activation/success conditions when needed, declaration provenance, and a derivation reference. Facts for raw stored payloads and values obtained by reading aliases/live bindings are separate.

Use a finite set of constructors. No execution-path enumeration is needed. At joins, union the possibilities. Around cycles, use a worklist that only adds possibilities until no fact changes. Start from all supported source initializations, including the existing scenario overrides and action conversions. Unvisited nodes begin with no derived reachable facts; an unsupported transfer produces the full set of possibilities, not an empty set. An empty set must never be used to delete a node unless unreachability has independently been established.

Transfer rules follow the existing operation semantics:

| Operation | Required treatment |
| --- | --- |
| Literal/default/override | Use the actual evaluator constructor; combine all admissible initialization sources. |
| Assignment | Transfer the evaluated value's possible types; preserve the old type when the write is inactive. |
| Arithmetic/comparison | Apply the existing runtime promotion, comparison, conversion, and error rules to the operand possibilities. |
| Conditional/short circuit | Combine possible selected values and retain activation and error propagation. |
| Stored alias/live binding | Follow the actual source lookup and binding rules; do not substitute a physical location for a held reading. |
| Sampling/send/copy/receive | Transfer payload type evidence through the actual sender, item field, compatible message types, and receiver. |
| Call/return | Include every source-compatible caller/return edge and existing local-storage sharing. |
| Constraint propagation | Follow the existing ordered updates, convergence test, and source-defined pass limit. |
| Unhandled write or lookup | Mark affected facts unknown and retain generic lowering. |

Message facts are indexed by item type and field, including compatible subtypes. Reused heap slots and absent fields cannot inherit facts from a previous occupant. A receive with several possible senders uses their union. Type propagation does not assume a fixed sensor-delivery schedule.

The worklist's termination follows from finite nodes, finite tracked locations, and monotone growth of finite constructor sets. This establishes termination of the analysis, not termination of the controller.

## 4. How proposed facts become trusted

Declarations identify intended domains and candidate optimizations. The existing evaluator and transition semantics determine whether a runtime representation is justified. There is no implicit numeric coercion added by this pass.

Prefer replayable structural derivations: literal constructors, copies, source-established action conversions, and previously checked arithmetic transfer rules. These avoid asking the numerical solver to rediscover basic type facts in every benchmark query.

For a proposed fact that cannot be discharged structurally, use a local check against the original generic node equations. Let I_n(s) be the type facts at node n and T_nm(s,s',o) the original edge relation, including its output/status o. Required obligations are:

    Init(s) => I_entry(s)
    I_n(s) and T_nm(s,s',o) => I_m(s')

Each edge includes its actual guard, inactive-write case, and applicable error/terminal paths. Validate the proposed entry facts and every transfer before they become solver premises. Cyclic facts may be proposed together but must satisfy initiation and all edge obligations; a cycle of mutually asserted facts is not evidence.

If a check returns SAT, UNKNOWN, crashes, or cannot express the operation, remove that proposed restriction and propagate the weaker information to a fixed point. The generic representation remains valid. No unsuccessful check can become an assumption, a dropped path, or a certificate success.

Only facts used to remove a branch or change a representation require this evidence. Do not create a separate program-wide type-theorem gate.

## 5. Two coordinated compiler changes

### 5.1 Specialize operations before building their formulas

Extend expression metadata so established operand types survive named equations, writes, reads, and joins. Select a lowering rule before constructing arithmetic branches. Building every generic branch and relying on Z3 to simplify afterward would retain much of the observed cost.

For example, two established Float operands use the existing Float64 addition with its original rounding mode. They need no integer-addition branch or integer-to-float overflow branch. Integer operands retain unbounded-integer semantics. Mixed integer/float comparisons retain the existing exact comparison rule; they must not be replaced with rounded float comparisons.

Errors disappear only when established operand facts make that particular error impossible. Operand-evaluation errors, division-by-zero behavior, exceptional results, and short-circuit ordering remain explicit.

### 5.2 Specialize eligible scalar storage in the solver signature

Choose one stable layout for each scalar location across the existing shared graph schema:

- A proved fixed constructor uses its native payload sort.
- A proved optional fixed constructor additionally retains a separate value-absence discriminator.
- A genuinely mixed or insufficiently established value retains the original tagged representation.

Keep `Cell.present`, storage mode, identity, insertion order, and their existing semantics. A missing cell and a present cell containing `Value.Absent` are different cases. A missing cell can also retain a stale raw payload in the current implementation. Do not canonicalize that payload or erase metadata merely because ordinary reads hide it. Specialize raw storage only when its full representation is justified; otherwise specialize reads/operations while leaving that cell generic.

Per-node type facts can still specialize an expression when the location's global layout remains mixed. Do not create one graph copy per type combination or a distinct state signature for every path.

Message heaps, queues, and call-frame layouts remain structurally unchanged in this change. Proved payload-read types feed operation specialization, with explicit box/unbox boundaries. A redesign into multiple typed heaps is outside this implementation. Measure how much generic storage remains rather than assuming scalar specialization removes all costs.

## 6. Exact correspondence contract

Let z be a specialized state and R_n(s,z) relate it to the original state s. The relation compares decoded payloads and every unchanged metadata field. For a fixed Float field, decoding is `Float(payload)`; it is not an equality to another physical or delayed field. Preserve signed zero, NaN treatment, and separately tracked identities using the same semantics as the original compiler.

The mapping must cover every admissible initialized state. Extra unused payload choices in an absent variant may exist; an injective mapping is not required. They must not affect any transition, status, or observed result.

For every original edge and related admissible input pair, require both directions:

    T_nm(s,s',o) and R_n(s,z)
      => exists z',o_z: T_special_nm(z,z',o_z)
                         and R_m(s',z') and o_z = o

    T_special_nm(z,z',o_z) and R_n(s,z)
      => exists s',o: T_nm(s,s',o)
                       and R_m(s',z') and o = o_z

Outputs include successor choice, errors, and the original boundary requirement results. Apply correspondence to exceptional exits and partially updated states as well as successful exits. Successful-transition preservation alone is insufficient.

The implementation should discharge this compositionally through explicit decode functions, checked operation identities, and unchanged generic components. Do not introduce one giant quantified equivalence query over the entire program. Local operation checks can compare deterministic decoded outputs and statuses directly; initialization and checked edge composition extend that result along executions.

For every original SysML property P at its existing boundary, require:

    R_n(s,z) => Eval(P,s) = Eval_special(P,z)

Here evaluation includes its original error/undefined result. Property expressions and checking times remain unchanged.

## 7. Concrete integration boundaries

| Module | Planned change |
| --- | --- |
| New `type_facts.py` | Finite propagation, source provenance, local derivation records, and deterministic evidence replay. |
| `lazy_specialization.py` | Extend the existing prototype with guarded facts, optional values, decoding, and full outcome correspondence; separate proposal from acceptance. |
| `lazy_expressions.py` | Preserve evidence through named values and dispatch to type-specific lowering before generic formulas are constructed. |
| `lazy_scalar_store.py` | Accept a checked layout; preserve raw payload, presence, storage mode, identity and ordering. |
| `lazy_graph.py` / `lazy_program.py` | Compute/check facts before optimized lowering; pass the chosen layout consistently into every node and relation field list. |
| Message-operation/constraint lowering | Carry type evidence through existing operations without changing delivery, heap, or convergence semantics. |
| `lazy_solver.py` | Consume the specialized shared signature and corresponding projections; retain the original graph, recursion, progress and coverage obligations. |
| `source_solver.py` | Include type evidence/layout in implementation identities, cache keys and solver evidence. |
| Certificate generation/checking | Record specialization identity and replay evidence from re-extracted source; reject mismatches rather than trusting recorded type labels. |

Proposed interfaces: `derive_type_facts(execution)`, `check_type_facts(execution, facts)`, and `choose_scalar_layout(checked_facts)`. The layout consumer must accept checked evidence, not an unchecked candidate map. `compile_graph` gets an explicit checked layout/facts argument; absence of it selects the existing generic path for comparison.

Cache keys include execution identity, evaluator/compiler implementation, dt, scenario-input contract, state schema, type facts, and layout. Reuse identical checked local rules and equations, not stale solver verdicts. Invalidate the whole derived type map when a writer changes.

## 8. Implementation order and decision gates

1. **Evidence and baseline inventory.** Preserve the current version-control snapshot. Enumerate candidate scalar/expression types for all three models, their writers, initialization alternatives, and message sources. Report how many uses are fixed, optional, mixed or unknown. Implement finite propagation and independent derivation replay before using any restriction.
2. **Operation specialization.** Add checked lowering for the existing arithmetic and Boolean operations. Compare each supported rule with the generic equations on the same input domain and same output/error property. Keep unknown operands on the generic path.
3. **Scalar layout integration.** Introduce native payloads only for eligible locations, including absent/stale-value cases. Update every state pack/unpack and projection together. Check initialization and full node correspondence before enabling the specialized backend.
4. **Certificate integration.** Record and replay specialization evidence, include it in identity checks, and prove that corrupted or missing evidence cannot enable optimization or pass certification.
5. **Workstation validation.** Run the planned controlled comparison, existing suites, and full pipeline. If the specialized query still times out, report that outcome and its measured residual costs. Do not broaden this task into a heap rewrite, recursion change, model modification, or new proof obligation.

All five steps are required to call the specialization integrated. Component tests alone do not complete this plan.

## 9. Correctness and efficiency validation

Run computation on kubuntu-workstation through Harnesslite. Retain the current 30-second limit per production proof query and 64 GB process-group memory limit with swap disabled. Diagnostic child exit codes, signals and timeouts must propagate into the test record; wrapper completion cannot count as success.

Correctness tests must cover:

- Literal integer initialization followed by float writes; fixed integer/float/Boolean fields; truly mixed fields; scenario overrides; action conversions.
- Branch joins and loops with type changes, alias writes, live bindings, multiple callers, and late-discovered writers.
- Separate physical/held/sent/received values; message subtype alternatives, delayed arrival, payload-slot reuse, and absent attributes.
- Inactive operations, failing operands, division by zero, overflow, NaN, signed zero, identity-sensitive equality, partial writes on failure, and ordered constraint convergence.
- Missing versus present-Absent cells, stale payloads, insertion order and metadata preservation.
- Mutations that remove a type alternative, change a sender or initializer, conflate physical/delayed storage, discard an error, or corrupt the evidence. They must invalidate specialization or fail correspondence/certification.

For the three source models, compare initialization and transition outputs with the existing concrete reference, then rerun the existing compiler, certification, discretization, and safety suites and full pipeline. Keep the original saved policies, seeds, episode counts, and checking boundaries when rerunning the existing policy replay. Runtime replay is supporting evidence, not the formal correspondence proof.

For efficiency, compare generic and specialized versions on the same model, input assumptions, property, dt, solver version, limits, and machine. Run them sequentially. Record compilation, type checking, serialization, solver and total time separately; record process-group peak RAM, Boolean variables and cumulative clauses, and each proof result. Use matched formula-level tests, not differently scoped fixtures, to claim a speedup. A crash/timeout is censored failure data, not a completed runtime measurement.

Acceptance requires replayable type evidence, preservation checks for every applied transformation, unchanged requirement semantics, rejection of corrupted evidence, and measured reduction of the targeted solver expansion including the cost of checking types. The full pipeline outcome must be reported explicitly. Only an actual passing end-to-end run establishes that the overall certification pipeline works; unresolved reconstruction or progress failures cannot be relabeled as specialization success for that larger claim.
