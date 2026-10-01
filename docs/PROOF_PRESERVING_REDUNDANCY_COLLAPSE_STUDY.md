# Proof-Preserving Redundancy Collapse Study

**Status:** non-normative research and design study; not an adopted implementation plan.

**Authority boundary:** this document does not amend, replace, or reorder the finite-history
MDP proof design, its obligations, its trust boundary, its pinned solver, or the current
thermostat implementation sequence. It authorizes no implementation by itself. If any proposal
here conflicts with the governing proof design, the governing design wins.

## 1. Result in one paragraph

There is no sound general mechanism that can merely "notice" that a proof condition looks
redundant and discard it. The standard sound construction has layers. First, prevent identical
typed expressions from being constructed twice by interning them in a hash-consed term DAG.
Second, emit only the complete dependency closure of the mandatory proof roots. Third, apply
only a small exact rewrite system. Finally, treat any semantic pruning, contextual
simplification, or obligation subsumption as a separate proof obligation whose evidence is
checked independently. A timeout, failed check, hash collision, missing certificate, or
uncertain side condition must retain the original expression or obligation. This gives the
desired implicit anti-redundancy behavior for structural duplication while preserving the
existing soundness boundary for every stronger reduction.

## 2. Non-negotiable soundness contract

Let `C` be the exact counterexample formula before a transformation and `C'` the formula sent
to the solver afterward. A transformation may support an `UNSAT` certificate only when the
independent checker establishes one of these relations:

1. **Exact:** `C <=> C'`.
2. **Counterexample over-approximation:** `models(C) subseteq models(C')`.

The second relation is enough because `UNSAT(C')` implies `UNSAT(C)`. It may create spurious
`SAT` results, so a reported counterexample still has to replay against the exact semantics.
An under-approximation is never sufficient for certification.

For this study, "lazy" therefore means **demand-driven construction of a checked exact or
over-approximating formula DAG**. It never means heuristic branch skipping.

| Change | Required fact | Permitted certificate use |
| --- | --- | --- |
| Share two occurrences of one typed node | They have the same exact node key and structure | Exact |
| Omit an unreachable constructed node | It is outside the complete transitive dependency closure of all proof roots | Exact |
| Replace a term by a simpler term | Equality under the applicable theory and context | Exact |
| Remove a branch under path condition `P` | `P => guard` or `P => not guard` | Exact |
| Weaken a constraint | Every exact counterexample remains in the weakened formula | `UNSAT` only; replay `SAT` |
| Omit one obligation because another covers it | The omitted counterexample formula implies the retained one | Exact discharge of that obligation |
| Drop an obligation because it was absent from an unsat core | No adequate fact | Forbidden |

## 3. What redundancy means here

The verifier can encounter several different phenomena that must not be conflated:

- **Repeated representation:** the same operator, sort, and operands are rebuilt at many call
  sites.
- **Repeated state transport:** an unchanged state component is copied through many events.
- **Repeated guards:** several events ask the same predicate over the same SSA versions.
- **Repeated join values:** multiple control-flow arms produce the same value.
- **Dead construction:** a term is built but no mandatory proof root depends on it.
- **Logical equivalence:** differently shaped formulas happen to mean the same thing.
- **Contextual redundancy:** a path condition already determines a guard or value.
- **Obligation redundancy:** proving one complete counterexample query also rules out another.
- **Post-solve premise redundancy:** an `UNSAT` result depends on only a subset of named
  assumptions.

The first five admit inexpensive, mostly structural treatment. The next three require logical
evidence. The last one is evidence minimization after a solve, not permission to omit proof
roots before solving.

## 4. Recommended layered design

This is a research recommendation for later evaluation, not a change to the current project
plan.

### 4.1 Typed, content-addressed expression DAG

All term creation should pass through one node factory. A node key should include at least:

- the exact operator or literal representation;
- result sort and all relevant type parameters;
- ordered child node identifiers;
- exact bit patterns for floating-point literals;
- rounding mode and any operation-specific semantic mode;
- SSA version and namespace for free variables;
- any definedness, exceptional-result, or error-state distinction represented by the node.

If the key and full structure equal an existing node, the factory returns the existing node ID.
Otherwise it creates one node. The hash is only an index: equality must be confirmed by full
structural comparison, so a hash collision can affect performance but never soundness.

This is the mechanism that can implicitly refuse most redundancy. Duplicate expressions never
become duplicate graph nodes. It is common subexpression elimination at construction time,
not a later guess about meaning.

SMT serialization should preserve that graph with named definitions or `let` bindings rather
than recursively expanding it into a tree. The SMT-LIB standard gives `let` its simultaneous
substitution semantics, making this representation sharing semantically transparent.

### 4.2 Demand-driven construction from mandatory roots

The generator may create metadata or lazy thunks for all possible terms, but should materialize
and serialize only nodes reachable from a complete root inventory. For the current proof
architecture, that inventory must include every active:

- domain and well-formedness assumption;
- transition and scheduler constraint;
- progress or termination condition;
- correspondence and buffer condition;
- exceptional, blocked, and definedness condition;
- frame condition that remains semantically necessary; and
- visible-difference root for the current obligation.

Reachability is an exact graph operation only if the dependency graph is complete. Data,
control, definedness, exceptional-flow, scheduler, progress, and frame dependencies must all be
represented. An independent checker should recompute the closure from the canonical root list
and reject a serialized formula that omits a reachable node or an expected root.

This is a proof-oriented form of cone-of-influence reduction. Model-checking systems use COI
to restrict analysis to variables relevant to a property, but their own documentation warns
that a counterexample in the reduced model may not validate in the original. That is why this
design requires exact dependency accounting and exact-semantics replay of any `SAT` witness.

### 4.3 A deliberately small exact rewrite kernel

The node factory may apply locally checkable identities before interning, for example:

- Boolean identities such as `and(x, true) = x` and `ite(c, x, x) = x`;
- idempotence where it is valid in the exact theory;
- elimination of a join whose incoming node IDs are all identical;
- flattening, sorting, or deduplication only for operators whose applicable semantics actually
  establish associativity, commutativity, or idempotence;
- symmetric ordering of equality operands when sorts and equality semantics match.

Each rewrite belongs to an explicit whitelist with a direction and a checker rule. In
particular, floating-point associativity, distributivity, cancellation, and real-number
substitution are forbidden without all required IEEE-754 side conditions and a checked proof.
NaNs, signed zero, infinities, rounding, and exceptional behavior make visually plausible
algebra unsound.

### 4.4 Preserve and extend sparse SSA

The existing sparse event SSA is already the correct first step: create versions only for
actual writes, retain identity for untouched state, and validate the full event frame. The DAG
layer should sit beneath that representation so repeated guards and right-hand sides over the
same input versions are shared automatically.

The word "same" is strict. A predicate over `temperature@7` is not interchangeable with the
same source text over `temperature@8`. Likewise, terms from independently quantified history
or implementation copies cannot be merged merely because their printed names or syntax match.
Only genuinely common immutable inputs may be shared across such namespaces.

### 4.5 Checked cone-of-influence slicing

After root selection, compute the backward transitive closure through every dependency kind.
The reducer should emit a ledger containing:

- the canonical root IDs;
- all retained node IDs;
- all removed node IDs;
- the edge kinds used in the closure; and
- counts before and after slicing.

The independent checker reconstructs the graph and closure instead of trusting the reducer's
claim. If dependency metadata is absent, malformed, cyclic where cycles are prohibited, or
inconsistent with the serialized term graph, reduction fails closed and the exact unreduced
form is retained.

### 4.6 Context-sensitive simplification with local proofs

Some redundancy is visible only under a path condition `P`. For example, an `ite(g, a, b)` may
reduce to `a` if `P => g`. The safe procedure is:

1. Ask whether `P and not g` is unsatisfiable.
2. Accept the rewrite only with independently checkable evidence or an exact replayable local
   implication query under the governing trust policy.
3. Cache the result by the exact pair `(context ID, term ID)` plus theory and solver-policy
   version.
4. On `SAT`, `UNKNOWN`, timeout, missing evidence, or a cache mismatch, keep the original
   branch.

Z3's own programming guide presents context-aware simplification by proving local term
equalities and also emphasizes memoizing visited subterms. That is a useful engineering
pattern, but the project's certificate policy must determine what local evidence is trusted.

The context key must be exact. Reusing a fact proved under a stronger, stale, or merely similar
context is unsound.

### 4.7 Deduplicate and subsume obligations only with the right implication

Syntactically identical canonical counterexample roots can share one solver query. Logical
subsumption is also possible, but the direction matters. If `C1` is solved and `C2` is to be
omitted, the checker must establish:

```text
C2 => C1
```

Then `UNSAT(C1)` implies `UNSAT(C2)`. The opposite implication does not justify the omission.
For a partitioned check, the governing complete formula must satisfy an independently checked
identity such as:

```text
C_total <=> (C_1 or C_2 or ... or C_n)
```

No visible-difference category may disappear merely because it appears unimportant or because
some other query was unsatisfiable.

### 4.8 Use unsat cores only after the proof is established

Named assumptions and unsat cores can reduce the evidence replayed or retained after an
`UNSAT` result. They do not prove that absent mandatory obligations were unnecessary. Z3's
documentation describes a core as a subset of tracked assumptions sufficient for the current
unsatisfiability result and notes that it need not be minimal.

A safe optional workflow is:

1. solve the complete query;
2. request a core;
3. construct the core-only query with the same conclusion/root;
4. solve and independently check it again; and
5. retain the original root inventory and the mapping from omitted assumptions to the checked
   core reduction.

This can shrink evidence, but it is not the main mechanism for avoiding initial construction.

### 4.9 Reuse immutable artifacts, not unrecorded solver state

The common base term DAG can be content-addressed once and combined with a small,
obligation-specific root. This permits caching of serialization, parsed solver input, and
checked local reductions without altering semantics.

Incremental solver scopes can improve development throughput, but they are a performance
mechanism, not a proof rule. They must not silently replace the current production requirement
for isolated, canonical, individually hashed obligation runs. Any future relaxation would need
an explicit trust and reproducibility analysis outside this study.

### 4.10 Proof-producing equality saturation is optional, not the baseline

E-graphs can represent many equivalent terms compactly, and proof-producing congruence
closure can explain a chosen equivalence. They become sound here only if every rewrite rule,
side condition, congruence step, and extracted equality is independently checked. An e-graph
whose rewrite library treats floating-point operations like real arithmetic would be dangerous.

Likewise, reduced ordered binary decision diagrams can canonically collapse Boolean functions
for a fixed variable order, but their size can still be exponential. They may be useful for a
bounded Boolean control skeleton with a hard size cap and exact fallback; they should not be
the default representation for the mixed-theory transition relation.

## 5. Safety classification

| Class | Examples | Status |
| --- | --- | --- |
| Structural exactness | Typed hash-consing, DAG-preserving `let`, identical-assertion deduplication, reachable-node serialization | Preferred first layer |
| Locally checked exactness | Small rewrite whitelist, identical SSA join values, checked COI closure | Preferred after checker exists |
| Translation-validated semantics | Contextual branch pruning, global value numbering across joins, logical obligation subsumption | Allowed only with checked evidence |
| Counterexample over-approximation | Dropping selected constraints, replacing deterministic internals by a shared abstraction with the required identity policy | Sound only for `UNSAT`; replay `SAT` |
| Heuristic or under-approximating pruning | Dropping branches because they seem infeasible, sampled-action pruning, incomplete difference sets | Forbidden for certification |
| Unchecked theory rewriting | Real-algebra rewrites over IEEE floating point, unproved datatype or exceptional-flow simplification | Forbidden |
| Evidence shortcuts | Treating timeout/`UNKNOWN` as success, using core absence to erase obligations, trusting hashes as equality | Forbidden |

## 6. Reduction evidence and independent checking

The producer should be allowed to be aggressive only because its output is checked. This is
the translation-validation pattern: validate each optimized translation against a common
semantics instead of trusting the optimizer implementation itself.

A reduction artifact should contain:

1. **Canonical node table:** node ID, exact sort, operator/literal, ordered child IDs, and
   semantic-mode fields.
2. **Root inventory:** every mandatory root, its obligation, and its role.
3. **Reduction records:** rule ID, input node IDs, output node ID, soundness direction, side
   conditions, and evidence reference.
4. **Dependency ledger:** all dependency-edge kinds and the checked reachability result.
5. **Obligation map:** exact formula or checked implication used for every deduplicated or
   subsumed obligation.
6. **Serialization digest:** canonical SMT-LIB bytes and hashes of referenced inputs.
7. **Metrics:** tree occurrence count, unique DAG nodes, roots, edges, fan-out, maximum `ite`
   depth, declarations, assertions, SMT-LIB bytes, and solver resource data.

The checker must:

- reparse canonical nodes and reject ill-sorted or malformed terms;
- confirm structural equality after every hash lookup;
- independently replay each rewrite or verify its proof;
- recompute dependency closure from the roots;
- confirm the obligation implication direction;
- reject missing, unknown, timed-out, or version-incompatible evidence; and
- reproduce the final canonical formula digest.

The optimized generator can initially remain an untrusted convenience program. To support the
project's direction of removing Python from the trusted compute boundary, Python may propose
nodes and reductions, but a small native or formally verified checker should decide whether
they are admissible. No Python hash table, traversal order, or cache hit should be a soundness
assumption.

## 7. The implicit mechanism, concretely

The desired "do not allow redundancy to form" behavior is best expressed by the following
abstract constructor:

```text
intern(sort, operator, ordered_children, semantic_parameters):
    key := canonical_encoding(sort, operator, ordered_children,
                              semantic_parameters)
    for candidate in hash_index[hash(key)]:
        if candidate.full_key == key:
            return candidate.node_id
    node_id := append_canonical_node(key)
    hash_index[hash(key)].append(node_id)
    return node_id
```

Later, serialization begins only from mandatory roots:

```text
emit(roots):
    reachable := checked_transitive_dependency_closure(roots)
    order := deterministic_topological_order(reachable)
    write_each_node_once(order)
    write_roots(roots)
```

These two operations eliminate structural duplication and dead construction without asking an
SMT solver whether anything is redundant. Logical redundancy is deliberately left to the
checked layers because recognizing arbitrary logical equivalence is itself a theorem-proving
problem.

## 8. Fail-closed policy

Every optional optimization needs an unreduced fallback. The result of any of the following is
"keep the original":

- a reduction check is `SAT`, `UNKNOWN`, interrupted, or timed out;
- a side condition cannot be represented exactly;
- proof production is unavailable for the active theory or preprocessing step;
- a cache key, tool version, semantic mode, or input digest differs;
- dependency metadata is incomplete;
- an expression crosses an unsafe namespace or SSA-version boundary;
- a size cap for BDD, e-graph, or contextual reasoning is reached; or
- the independent checker disagrees with the producer.

No optimization failure is a verification failure. It is only a missed performance
opportunity. Conversely, no optimization success may become a certificate until its evidence
has passed the checker.

## 9. Evaluation order, without changing the project plan

If this study is later approved for implementation, the safest order to evaluate is:

1. measure unique subterms versus repeated tree occurrences in the current compact transition
   formula;
2. add typed DAG interning and graph-preserving SMT-LIB serialization;
3. add checked root-reachability serialization;
4. add the small exact rewrite kernel;
5. add checked COI metadata and mutation tests for every dependency-edge kind;
6. measure again before considering contextual implication queries;
7. consider obligation subsumption only where measured duplication remains material; and
8. consider proof-producing e-graphs or bounded BDDs only if the simpler layers leave a
   demonstrated bottleneck.

This order is intentionally conservative: it captures the largest low-risk gains before adding
new theorem-proving work or expanding the checker.

## 10. Validation and adversarial tests

At minimum, later prototypes should include mutations that:

- force hash collisions and confirm that distinct structures never merge;
- change one child sort, SSA version, rounding mode, namespace, or literal bit and confirm a
  distinct node results;
- delete each dependency-edge class in turn and confirm closure checking fails;
- drop an exceptional, blocked, progress, scheduler, or frame root and confirm root checking
  fails;
- reverse the implication used for obligation subsumption and confirm rejection;
- reuse a contextual proof under a weaker context and confirm rejection;
- inject NaN, signed-zero, infinity, and rounding-sensitive floating-point cases;
- feed `UNKNOWN`, timeout, corrupt evidence, and tool-version mismatch outcomes and confirm
  exact fallback;
- replay every reduced `SAT` witness against the unreduced exact semantics; and
- compare reduced and unreduced results on generated small instances where exhaustive
  enumeration is possible.

Success metrics must include both formula size and proof risk: unique DAG nodes, serialized
bytes, construction time, peak memory, solver time, number and cost of local checks, evidence
size, checker time, fallback rate, and mutation-test detection rate.

## 11. Open decisions requiring separate approval

- Whether the pinned Z3 build can produce sufficiently complete, stable proof evidence for
  the exact mix of floating-point, datatype, and preprocessing operations in use.
- Whether local implication results should be replayed by the same pinned solver in isolated
  processes or checked in a smaller independent kernel.
- The canonical node encoding and whether the trusted checker should be native code, a Lean
  artifact, or both.
- Whether common base artifacts may be cached across production obligations without weakening
  current isolation and reproducibility requirements.
- Whether the remaining formula profile after structural sharing justifies any e-graph or BDD
  complexity.

None of these decisions is needed to preserve the benefits already obtained from sparse SSA.

## 12. Primary references

- Barrett, Fontaine, and Tinelli, [The SMT-LIB Standard, Version 2.7](https://smt-lib.org/papers/smt-lib-reference-v2.7-r2025-02-05.pdf): formal semantics of `let`, definitions, scripts, and solver interaction.
- Bjørner, de Moura, and colleagues, [Programming Z3](https://z3prover.github.io/papers/programmingz3.html): DAG-aware term traversal, incremental scopes, unsat cores, and context-aware simplification by local solver checks.
- Z3 source, [`ast.cpp`](https://github.com/Z3Prover/z3/blob/master/src/ast/ast.cpp): the solver's own AST manager uses hash-consing.
- Pnueli, Siegel, and Singerman, [Translation Validation](https://link.springer.com/content/pdf/10.1007/BFb0054170.pdf): validation of individual optimized translations through a common semantics and simulation relation.
- Monniaux and Six, [Simple, Light, Yet Formally Verified, Global Common Subexpression Elimination and Loop-Invariant Code Motion](https://arxiv.org/pdf/2105.01344): verified CSE, hashed representations, semantic preservation, and trust-boundary tradeoffs.
- nuXmv project, [nuXmv 2.2 User Manual](https://nuxmv.fbk.eu/downloads/nuxmv-user-manual.pdf): property-specific cone-of-influence reduction and the reduced-model counterexample caveat.
- Barbosa et al., [Flexible Proof Production in an Industrial-Strength SMT Solver](https://gereon-kremer.de/static/2022-ijcar-cvc5-proofs.pdf): eager and lazy proof production for preprocessing transformations.
- cvc5 project, [Proof production documentation](https://cvc5.github.io/docs/latest/proofs/proofs.html): proof calculus and proof output/checking formats.
- Willsey et al., [egg: Fast and Extensible Equality Saturation](https://arxiv.org/abs/2004.03082): e-graph equality saturation and extraction.
- Flatt et al., [Small Proofs from Congruence Closure](https://arxiv.org/abs/2209.03398): proof certificates for congruence closure and equality saturation.
- Bryant, [Graph-Based Algorithms for Boolean Function Manipulation](https://www.cs.cmu.edu/~emc/15414-s14/lecture/ieeetc86.pdf): canonical reduced ordered BDDs and their worst-case size limitation.

## 13. Bottom line

The sound answer is not to ask the verifier to guess what can be ignored. It is to make exact
duplicate construction impossible, make unreferenced construction unnecessary, and require a
checked theorem for every further semantic omission. The first two measures are implicit and
cheap; the third is explicit because soundness demands it. This preserves the project's
ultimate limit: computational savings are optional, but the mathematical result is not.
