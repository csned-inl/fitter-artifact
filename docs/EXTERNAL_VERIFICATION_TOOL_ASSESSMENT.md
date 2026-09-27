# Reuse of existing verification tools

Documentation assessment, 2026-09-20. No packages were installed, no benchmarks were executed, and no production implementation was changed. Rankings below are engineering judgments about fit, not demonstrated performance or completed source correspondence.

## Conclusion

Existing tools can supply much of the typed verification, obligation generation, invariant checking, and proof checking contemplated in our custom plan. The strongest candidate to evaluate for replacing custom obligation construction is Why3/WhyML. The smallest backend experiment is to send existing suitable local SMT obligations to cvc5 and check its proof output. Neither removes the need to translate CLARITY's actual source execution and property meanings faithfully.

The project already uses an external solver, Z3/Spacer. The question is which additional framework components can replace custom compiler/verifier machinery, rather than merely selecting another solver for the same oversized formula.

## 1. Why3 / WhyML: strongest framework candidate

WhyML is a typed verification language. Why3 generates verification conditions from program bodies and contracts, including loop/recursion termination obligations. It also provides a strongest-postcondition mode, `[@vc:sp]`, specifically intended to avoid formula duplication across successive conditionals. This is directly relevant to our compact-formula objective. [Verification-condition generation](https://why3.org/doc/vcgen.html)

Why3's IEEE floating-point library includes rounding modes, exceptional values, and a Float64 module. It distinguishes logical equality from IEEE comparison equality. Its documentation separates public operations from a private axiomatization; an exporter must use the appropriate public operations and solver driver. This is evidence of a suitable vocabulary, not proof that every current Python conversion/error rule translates automatically. [Floating-point library](https://why3.org/stdlib/ieee_float.html)

**Could replace:** much of the handwritten generation of local preservation checks, contract composition, invariant checks, termination conditions, and solver-task management.

**Ingredients we must supply:** typed state definitions, exact source-node bodies, explicit error outcomes, initialization/scenario semantics, message copies and delivery, unchanged requirement predicates, and candidate invariants or termination measures where automatic inference is insufficient. Current dynamic numeric kinds require a checked translation; declaring every SysML Real to be Float64 is not enough.

**Fit assessment:** worth evaluating before expanding our custom verification framework. It requires a WhyML exporter and source-correspondence validation. A type checker establishes facts about that exported program; it does not establish that the exporter preserved SysML execution. The strongest-postcondition option addresses formula-generation duplication, not necessarily the measured floating-point bit-level expansion.

## 2. cvc5 + Ethos: local solving and proof checking

cvc5 supports proof objects and external proof formats. Its CPC output can be checked with Ethos. The documentation explicitly distinguishes a fully checked proof from one containing unsupported, trusted steps: Ethos can exit successfully while reporting `incomplete`. Our acceptance condition must require the complete result, not merely exit code zero. [Proof production](https://cvc5.github.io/docs/latest/proofs/proofs.html), [CPC/Ethos checking](https://cvc5.github.io/docs/latest/proofs/output_cpc.html)

**Could replace:** selected local Z3 queries and repeated search during verification when a complete independently checked proof is available for the exact obligation.

**Ingredients:** well-scoped SMT-LIB formulas with the original sorts, premises, and failure cases; pinned solver/checker versions; exact input-formula identity; handling of SAT, UNKNOWN, crash, incomplete proof, and checker failure.

**Fit assessment:** a relatively small comparison at the existing local-equation boundary. It is not a demonstrated replacement for the current recursive Spacer query. Floating-point solving support does not by itself guarantee complete external proof support for our combination of arithmetic, arrays, and datatypes. Test that combination before relying on proof replay. Simply moving the existing giant formula to another solver is not an established efficiency fix.

## 3. CBMC: alternative for discrete transition execution

CBMC has floating-point support and can check that loop unrolling covers all executions using unwinding assertions. It also supports loop contracts with invariants, modified-location descriptions, and decreasing measures for termination; it is not limited to arbitrary finite bug-finding runs. [Floating-point theory](https://www.cprover.org/SMT-LIB-Float/), [Unwinding assertions](https://www.cprover.org/cprover-manual/cbmc/unwinding/), [Loop contracts](https://diffblue.github.io/cbmc/contracts-loops.html)

**Could replace:** some custom symbolic execution for a generated C transition program, including bounded buffers and source-bounded loops.

**Ingredients:** a faithful C representation of state, messages, timing, errors, and requirements; proved numerical ranges before replacing mathematical/Python integers with fixed-width C integers; either checked unwinding sufficiency or valid loop contracts. Keep a finite observation buffer distinct from an execution-step bound.

**Fit assessment:** credible alternative backend, but a larger translation obligation than local SMT reuse. IEEE arithmetic support does not automatically preserve Python exceptions, mixed comparisons, aliases, or identity behavior. No claim is made here that CBMC supplies the independent proof artifacts required by our certificate format.

## 4. Existing dataflow/abstract-interpretation libraries

MLIR supplies reusable dataflow infrastructure, including forward/backward propagation, call/region handling, constant propagation and liveness. Crab supplies abstract domains and fixed-point algorithms for constructing static analyses. [MLIR dataflow tutorial](https://github.com/llvm/llvm-project/blob/main/mlir/docs/Tutorials/DataFlowAnalysis.md), [Crab](https://github.com/seahorn/crab)

**Could replace:** generic worklist, dependency propagation and fixed-point infrastructure in the proposed type-fact pass.

**Ingredients:** an adapter for our execution graph and sound transfer functions for its particular operations. A library's fixed-point algorithm does not supply the CLARITY-specific transfer semantics or proof of them.

**Fit assessment:** useful if their infrastructure removes substantial custom work, but neither should be adopted merely to replace a small finite-set worklist. MLIR introduces an intermediate representation and native dependency stack; Crab's numerical analyses are not themselves the required runtime-constructor analysis. Integration cost may exceed the narrowly scoped pass. SeaHorn combines LLVM translation, Horn solving and Crab invariants; routing through it without showing a better encoding would not establish that our existing Horn bottleneck disappears. [SeaHorn](https://github.com/seahorn/seahorn)

Frama-C's Eva provides sound overapproximations of IEEE operations for C-supported floating formats. It is another candidate if a faithful C exporter is chosen, rather than a direct analyzer of our current SysML/Python execution. [Eva IEEE model](https://www.frama-c.com/api/frama-c-eva/Eva/IEEE754/index.html)

## 5. Numerical and continuous safety components

**Gappa** proves numerical interval/rounding properties and can emit proof scripts checked by Coq/Rocq. It is a candidate for suitable arithmetic leaves of the discretization proof. Its documented command-line proof export provides an existing alternative to a new arithmetic proof checker. It is not an automatic proof of all hybrid trajectories or the MDP property. [Gappa](https://gappa.gitlabpages.inria.fr/), [Proof export and checking](https://gappa.gitlabpages.inria.fr/gappa/invoking.html)

**KeYmaera X** is a prover for hybrid systems, covering discrete control and differential-equation evolution. It is relevant to preservation of safety throughout a continuous interval. Its use would require a faithful hybrid-program translation including sensor sampling, held values, clocks, and the actual evolution assumptions. A proof over real-valued dynamics would still need the specified correspondence to rounded executable arithmetic. It is a candidate for the continuous layer, not a direct fix for the current Stage 3 numeric query. [KeYmaera X](https://keymaerax.org/)

## The ingredients shared by every credible route

1. A source-derived execution representation with physical, held, sent and received values distinct, plus timing, control and errors.
2. Original SysML requirements represented without operator, value-source, or checking-boundary substitutions.
3. Exact numeric semantics, including runtime integer/float distinctions and conversions. No real-arithmetic or fixed-width approximation silently replaces execution.
4. The actual theorem as an explicit contract: buffer reconstruction, equal-state/action transition agreement, progress when required, and separate continuous-interval preservation. These do not follow simply from making the model well typed.
5. Translation correspondence and an inventory of all required obligations. Solver success applies to the formula supplied; it does not validate the extraction step by itself.
6. Evidence validation appropriate to the selected tool: complete proof replay where available, or an explicitly stated solver-trust boundary. A proof-session record, hash, or successful process exit is not automatically an independently checked proof.

## Decision before implementation expansion

The immediate comparison should be at an existing semantic boundary: one actual source transition with delayed data and an original requirement, plus the local numeric/type obligations it uses. Compare the current encoding with Why3-generated obligations, and try cvc5 proof production on compatible leaves. Include deliberate property/value-source errors that must fail. For a loop, prove the required invariant/progress or unwinding sufficiency; do not supply them as unverified assumptions.

A successful package demonstration must preserve the source mapping, cover the same executions, produce acceptable evidence, and improve either maintainability or measured resource use under the existing workstation limits. A small demonstration is a feasibility test, not full-model certification. Until that evidence exists, the recommendation is to evaluate framework reuse, not to replace the pipeline wholesale or add all listed tools as dependencies.
